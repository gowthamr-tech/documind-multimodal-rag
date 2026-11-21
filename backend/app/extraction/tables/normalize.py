"""Table semantics: numeric parsing, units/scale, hierarchical header paths, cross-page stitching.

These are pure functions over `TableRecord`/`CellRecord`, independent of the parser, and are
the basis for exact numeric answers (the Table Agent reads `table_cells.numeric_value`).
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from app.extraction.models import CellRecord, TableRecord

_CURRENCY_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY", "₹": "INR"}
_CURRENCY_CODES = ("USD", "EUR", "GBP", "JPY", "INR", "CHF", "CAD", "AUD", "CNY")
_EMPTY = {"", "-", "\u2013", "\u2014", "n/a", "na", "n.a.", "nm", "n.m.", "*", "…"}
_SUFFIX_SCALE = {
    "k": 1_000,
    "m": 1_000_000,
    "mn": 1_000_000,
    "bn": 1_000_000_000,
    "b": 1_000_000_000,
}
_SCALE_WORDS = [
    (re.compile(r"\b(billions?|bn)\b", re.I), 1_000_000_000),
    (re.compile(r"\b(millions?|mn|mm)\b|\$m\b|€m\b|£m\b", re.I), 1_000_000),
    (re.compile(r"\b(thousands?)\b|\$\s?0{3}s?\b|€\s?0{3}s?\b|£\s?0{3}s?\b|\b000s\b", re.I), 1_000),
]
_FOOTNOTE_MARKER = re.compile(r"(\s+\(\d{1,2}\)|\s*\*+|\s+[a-z]\))$")
_LEADING_CURRENCY = re.compile(r"^([$€£¥₹])\s*")
_NUMBER_BODY = re.compile(
    r"(\d{1,3}(?:[,\u00a0\u202f ]\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)\s*(%|bn|mn|[kmb])?",
    re.I,
)


def parse_number(text: str) -> tuple[Decimal | None, str | None, int | None]:
    """Parse an accounting-formatted cell. Returns (value, unit, inline_scale).

    Steps: strip trailing footnote markers (`12.0 (1)`, `5.1*`); parentheses mean negative
    (accounting convention); optional currency symbol and sign in either order; thousands
    separators; `%`; magnitude suffixes (`1.2bn`). Anything else returns (None, None, None) —
    never a guess.
    """
    raw = text.strip().replace("\u2212", "-")
    if raw.lower() in _EMPTY:
        return None, None, None
    raw = _FOOTNOTE_MARKER.sub("", raw).strip()

    negative = False
    if raw.startswith("(") and raw.endswith(")"):
        negative, raw = True, raw[1:-1].strip()

    currency: str | None = None
    for _ in range(3):  # accept "$-5", "-$5", "$(5.0)" in any order
        if m := _LEADING_CURRENCY.match(raw):
            currency, raw = _CURRENCY_SYMBOLS[m.group(1)], raw[m.end() :]
        if raw[:1] in {"-", "+"}:
            negative ^= raw[0] == "-"
            raw = raw[1:].strip()
        if raw.startswith("(") and raw.endswith(")"):
            negative, raw = not negative, raw[1:-1].strip()

    m = _NUMBER_BODY.fullmatch(raw)
    if not m:
        return None, None, None
    try:
        value = Decimal(re.sub(r"[,\u00a0\u202f ]", "", m.group(1)))
    except InvalidOperation:
        return None, None, None
    suffix = (m.group(2) or "").lower()
    unit = "%" if suffix == "%" else currency
    scale = _SUFFIX_SCALE.get(suffix) if suffix and suffix != "%" else None
    return (-value if negative else value), unit, scale


def detect_units(*texts: str | None) -> tuple[str | None, str | None, int | None]:
    """Find a units declaration like "(USD millions)", "in thousands of euros", "$000s".

    Returns (human label, unit code, scale). Searches captions first, then headers.
    """
    for text in texts:
        if not text:
            continue
        scale = next((s for rx, s in _SCALE_WORDS if rx.search(text)), None)
        unit = next((c for c in _CURRENCY_CODES if re.search(rf"\b{c}\b", text)), None)
        if unit is None:
            for sym, code in _CURRENCY_SYMBOLS.items():
                if sym in text:
                    unit = code
                    break
        if unit is None and re.search(r"\b(dollars?)\b", text, re.I):
            unit = "USD"
        if unit is None and re.search(r"\b(euros?)\b", text, re.I):
            unit = "EUR"
        if scale or unit:
            label_match = re.search(
                r"\(([^()]*(?:million|thousand|billion|USD|EUR|GBP|%)[^()]*)\)", text, re.I
            )
            label = label_match.group(1).strip() if label_match else text.strip()[:60]
            return label, unit, scale
    return None, None, None


def assign_header_paths(table: TableRecord) -> None:
    """Give every body cell its column header path (outermost first) and row header path.

    Column headers come from header cells spanning the cell's column; row headers from
    row-header cells (or, when none are flagged, the first column's text in that row).
    """
    header_rows = sorted(
        {r for c in table.cells if c.is_header for r in range(c.row, c.row + c.row_span)}
    )
    table.header_depth = 0
    for r in range(table.n_rows):  # header depth = leading run of header rows
        if r in header_rows:
            table.header_depth += 1
        else:
            break

    col_headers: dict[int, list[str]] = {k: [] for k in range(table.n_cols)}
    for c in sorted(
        (c for c in table.cells if c.is_header and c.row < table.header_depth), key=lambda c: c.row
    ):
        for k in range(c.col, min(c.col + c.col_span, table.n_cols)):
            text = c.raw_text.strip()
            if text and (not col_headers[k] or col_headers[k][-1] != text):
                col_headers[k].append(text)

    row_label: dict[int, list[str]] = {}
    for c in table.cells:
        if c.row >= table.header_depth and c.col == 0 and c.raw_text.strip():
            for r in range(c.row, c.row + c.row_span):
                row_label.setdefault(r, []).append(c.raw_text.strip())

    for c in table.cells:
        if c.row < table.header_depth:
            continue
        c.header_path = list(col_headers.get(c.col, []))
        c.row_path = [] if c.col == 0 else list(row_label.get(c.row, []))


def apply_numeric_semantics(table: TableRecord) -> None:
    """Parse body cells into numbers, inheriting table-level unit/scale where the cell has none."""
    label, unit, scale = detect_units(
        table.caption,
        " ".join(c.raw_text for c in table.cells if c.is_header),
    )
    table.units, table.unit, table.scale = label, unit, scale
    for c in table.cells:
        if c.row < table.header_depth or c.col == 0:
            continue
        value, cell_unit, inline_scale = parse_number(c.raw_text)
        if value is None:
            continue
        c.numeric_value = value
        header_says_pct = any("%" in h for h in c.header_path)
        c.unit = cell_unit or ("%" if header_says_pct else unit)
        c.scale = inline_scale or (None if c.unit == "%" else scale)


def stitch_tables(tables: list[TableRecord]) -> list[TableRecord]:
    """Merge a table continued on the next page into its predecessor.

    Conditions (conservative, to avoid merging unrelated tables): consecutive in reading order,
    on adjacent pages, same column count, and the continuation either has no caption or repeats
    the predecessor's header rows exactly.
    """
    out: list[TableRecord] = []
    for t in tables:
        prev = out[-1] if out else None
        if (
            prev is not None
            and prev.page_end is not None
            and t.page_start == prev.page_end + 1
            and t.n_cols == prev.n_cols
            and (not t.caption or _same_header(prev, t))
        ):
            _append_rows(prev, t)
            continue
        out.append(t)
    return out


def _header_grid(t: TableRecord) -> list[list[str]]:
    return t.grid()[: t.header_depth]


def _same_header(a: TableRecord, b: TableRecord) -> bool:
    return a.header_depth > 0 and _header_grid(a) == _header_grid(b)


def _append_rows(base: TableRecord, cont: TableRecord) -> None:
    skip = cont.header_depth if _same_header(base, cont) else 0
    offset = base.n_rows - skip
    for c in cont.cells:
        if c.row < skip:
            continue
        base.cells.append(
            CellRecord(
                row=c.row + offset,
                col=c.col,
                row_span=c.row_span,
                col_span=c.col_span,
                is_header=False,
                raw_text=c.raw_text,
                bbox=None,  # bbox is on another page
            )
        )
    base.n_rows += cont.n_rows - skip
    base.page_end = cont.page_end
    base.footnotes.extend(f for f in cont.footnotes if f not in base.footnotes)
    base.stitched_from += 1


def to_markdown(table: TableRecord) -> str:
    grid = table.grid()
    if not grid:
        return ""
    depth = max(table.header_depth, 1)
    header = [
        " / ".join(dict.fromkeys(filter(None, (grid[r][k] for r in range(depth)))))
        for k in range(table.n_cols)
    ]
    lines = ["| " + " | ".join(h or " " for h in header) + " |", "|" + "---|" * table.n_cols]
    for row in grid[depth:]:
        lines.append("| " + " | ".join(cell.replace("|", "\\|") for cell in row) + " |")
    out = "\n".join(lines)
    if table.caption:
        out = f"{table.caption}\n\n{out}"
    if table.footnotes:
        out += "\n\n" + "\n".join(table.footnotes)
    return out


def to_html(table: TableRecord) -> str:
    """HTML preserving row/col spans (for the viewer's extracted-table panel)."""
    from html import escape

    by_row: dict[int, list[CellRecord]] = {}
    for c in table.cells:
        by_row.setdefault(c.row, []).append(c)
    rows = []
    for r in range(table.n_rows):
        cells = []
        for c in sorted(by_row.get(r, []), key=lambda c: c.col):
            tag = "th" if c.is_header else "td"
            span = (f' rowspan="{c.row_span}"' if c.row_span > 1 else "") + (
                f' colspan="{c.col_span}"' if c.col_span > 1 else ""
            )
            cells.append(f"<{tag}{span}>{escape(c.raw_text)}</{tag}>")
        rows.append("<tr>" + "".join(cells) + "</tr>")
    caption = f"<caption>{escape(table.caption)}</caption>" if table.caption else ""
    return f"<table>{caption}{''.join(rows)}</table>"
