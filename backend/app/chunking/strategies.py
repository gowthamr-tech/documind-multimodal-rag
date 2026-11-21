"""The seven chunking strategies (all reproducible; compared in evaluation/experiments)."""

from __future__ import annotations

import math
from collections.abc import Iterable

from app.chunking.base import (
    Block,
    BlockKind,
    ChunkDraft,
    ChunkingConfig,
    ChunkType,
    Embedder,
    count_tokens,
    split_sentences,
)

_SEPARATORS = ["\n\n", "\n", ". ", "; ", ", ", " "]


def _body(blocks: Iterable[Block]) -> list[Block]:
    return [b for b in blocks if b.kind is not BlockKind.HEADING or b.text]


def _unique(blocks: Iterable[Block]) -> list[Block]:
    """Order-preserving dedupe by identity (Block is an unhashable dataclass)."""
    seen: set[int] = set()
    out = []
    for b in blocks:
        if id(b) not in seen:
            seen.add(id(b))
            out.append(b)
    return out


def _figure_chunks(blocks: list[Block]) -> list[ChunkDraft]:
    out = []
    for b in blocks:
        if b.kind is BlockKind.FIGURE and b.text.strip():
            ctype = ChunkType.CHART if b.extra.get("figure_type") == "chart" else ChunkType.IMAGE
            out.append(
                ChunkDraft(
                    b.text,
                    ctype,
                    [b],
                    metadata={"values_are_estimated": b.extra.get("values_are_estimated", True)},
                )
            )
    return out


def _table_chunk(b: Block) -> ChunkDraft:
    return ChunkDraft(b.text, ChunkType.TABLE, [b], metadata={"rows": len(b.table_rows)})


def _table_row_groups(b: Block, rows_per_chunk: int) -> list[ChunkDraft]:
    out = []
    for i in range(0, len(b.table_rows), rows_per_chunk):
        rows = b.table_rows[i : i + rows_per_chunk]
        content = "\n".join(filter(None, [b.extra.get("caption"), b.table_header, *rows]))
        out.append(
            ChunkDraft(
                content,
                ChunkType.TABLE_ROWS,
                [b],
                metadata={"row_start": i, "row_end": i + len(rows) - 1},
            )
        )
    return out


def _windows(
    units: list[tuple[str, Block]], max_tokens: int, overlap: int, ctype: ChunkType
) -> list[ChunkDraft]:
    """Greedy packing of (text, block) units into <=max_tokens windows with token overlap."""
    out: list[ChunkDraft] = []
    cur: list[tuple[str, Block]] = []
    size = 0
    for text, block in units:
        t = count_tokens(text)
        if cur and size + t > max_tokens:
            out.append(ChunkDraft(" ".join(x for x, _ in cur), ctype, _unique(b for _, b in cur)))
            # carry trailing units as overlap
            carry: list[tuple[str, Block]] = []
            csize = 0
            for x, bb in reversed(cur):
                ct = count_tokens(x)
                if csize + ct > overlap:
                    break
                carry.insert(0, (x, bb))
                csize += ct
            cur, size = carry, csize
        cur.append((text, block))
        size += t
    if cur:
        out.append(ChunkDraft(" ".join(x for x, _ in cur), ctype, _unique(b for _, b in cur)))
    return out


def _recursive_split(text: str, max_tokens: int, seps: list[str] | None = None) -> list[str]:
    seps = _SEPARATORS if seps is None else seps
    if count_tokens(text) <= max_tokens or not seps:
        return [text] if text.strip() else []
    sep, rest = seps[0], seps[1:]
    parts = [p for p in text.split(sep) if p.strip()]
    if len(parts) == 1:
        return _recursive_split(text, max_tokens, rest)
    pieces: list[str] = []
    buf = ""
    for p in parts:
        candidate = f"{buf}{sep}{p}" if buf else p
        if count_tokens(candidate) <= max_tokens:
            buf = candidate
            continue
        if buf:
            pieces.append(buf)
        if count_tokens(p) > max_tokens:
            pieces.extend(_recursive_split(p, max_tokens, rest))
            buf = ""
        else:
            buf = p
    if buf:
        pieces.append(buf)
    return pieces


class FixedSizeChunker:
    """Fixed token windows over the document stream (words), with overlap."""

    name = "fixed"

    def __init__(self, cfg: ChunkingConfig | None = None) -> None:
        self.cfg = cfg or ChunkingConfig()

    def chunk(self, blocks: list[Block]) -> list[ChunkDraft]:
        units = [(w, b) for b in _body(blocks) for w in b.text.split()]
        out: list[ChunkDraft] = []
        step = max(1, self.cfg.max_tokens - self.cfg.overlap_tokens)
        for i in range(0, len(units), step):
            win = units[i : i + self.cfg.max_tokens]
            if not win:
                break
            out.append(
                ChunkDraft(" ".join(w for w, _ in win), ChunkType.TEXT, _unique(b for _, b in win))
            )
            if i + self.cfg.max_tokens >= len(units):
                break
        return out


class RecursiveChunker:
    """Split on paragraph > line > sentence > clause > word boundaries until chunks fit."""

    name = "recursive"

    def __init__(self, cfg: ChunkingConfig | None = None) -> None:
        self.cfg = cfg or ChunkingConfig()

    def chunk(self, blocks: list[Block]) -> list[ChunkDraft]:
        units: list[tuple[str, Block]] = []
        for b in _body(blocks):
            for piece in _recursive_split(b.text, self.cfg.max_tokens):
                units.append((piece, b))
        return _windows(units, self.cfg.max_tokens, self.cfg.overlap_tokens, ChunkType.TEXT)


class SemanticChunker:
    """Break between sentences where embedding similarity drops (percentile threshold)."""

    name = "semantic"

    def __init__(self, embedder: Embedder, cfg: ChunkingConfig | None = None) -> None:
        self.embedder = embedder
        self.cfg = cfg or ChunkingConfig()

    def chunk(self, blocks: list[Block]) -> list[ChunkDraft]:
        sents = [
            (s, b)
            for b in _body(blocks)
            for s in (split_sentences(b.text) or [b.text])
            if s.strip()
        ]
        if len(sents) <= 1:
            return (
                [ChunkDraft(" ".join(s for s, _ in sents), ChunkType.TEXT, [b for _, b in sents])]
                if sents
                else []
            )
        vecs = self.embedder.embed_documents([s for s, _ in sents])
        dists = [1 - _cos(vecs[i], vecs[i + 1]) for i in range(len(vecs) - 1)]
        threshold = _percentile(dists, self.cfg.semantic_breakpoint_percentile)
        out: list[ChunkDraft] = []
        cur = [sents[0]]
        for i, d in enumerate(dists, start=1):
            size = count_tokens(" ".join(s for s, _ in cur))
            if d > threshold or size + count_tokens(sents[i][0]) > self.cfg.max_tokens:
                out.append(
                    ChunkDraft(
                        " ".join(s for s, _ in cur), ChunkType.TEXT, _unique(b for _, b in cur)
                    )
                )
                cur = []
            cur.append(sents[i])
        out.append(
            ChunkDraft(" ".join(s for s, _ in cur), ChunkType.TEXT, _unique(b for _, b in cur))
        )
        return out


class StructureChunker:
    """One chunk per section (by section path); oversized sections split recursively."""

    name = "structure"

    def __init__(self, cfg: ChunkingConfig | None = None) -> None:
        self.cfg = cfg or ChunkingConfig()

    def sections(self, blocks: list[Block]) -> list[list[Block]]:
        groups: list[list[Block]] = []
        key: tuple[str, ...] | None = None
        for b in _body(blocks):
            if b.kind in {BlockKind.TABLE, BlockKind.FIGURE}:
                continue
            k = tuple(b.section_path)
            if k != key:
                groups.append([])
                key = k
            groups[-1].append(b)
        return [g for g in groups if any(x.text.strip() for x in g)]

    def chunk(self, blocks: list[Block]) -> list[ChunkDraft]:
        out: list[ChunkDraft] = []
        for group in self.sections(blocks):
            units = [(p, b) for b in group for p in _recursive_split(b.text, self.cfg.max_tokens)]
            out.extend(
                _windows(units, self.cfg.max_tokens, self.cfg.overlap_tokens, ChunkType.SECTION)
            )
        return out


class PageChunker:
    """One chunk per page (pages without numbers, e.g. native DOCX, fall back to structure)."""

    name = "page"

    def __init__(self, cfg: ChunkingConfig | None = None) -> None:
        self.cfg = cfg or ChunkingConfig()

    def chunk(self, blocks: list[Block]) -> list[ChunkDraft]:
        by_page: dict[int, list[Block]] = {}
        unpaged: list[Block] = []
        for b in _body(blocks):
            (by_page.setdefault(b.page_no, []) if b.page_no is not None else unpaged).append(b)
        out = [
            ChunkDraft("\n".join(b.text for b in bs if b.text.strip()), ChunkType.PAGE, bs)
            for _, bs in sorted(by_page.items())
            if any(b.text.strip() for b in bs)
        ]
        if unpaged:
            out.extend(StructureChunker(self.cfg).chunk(unpaged))
        return out


class TableAwareChunker:
    """Structure chunks for prose; tables never split mid-row: whole-table chunk when small,
    row groups with the header repeated when large; figures as their own chunks."""

    name = "table_aware"

    def __init__(self, cfg: ChunkingConfig | None = None) -> None:
        self.cfg = cfg or ChunkingConfig()

    def chunk(self, blocks: list[Block]) -> list[ChunkDraft]:
        out = StructureChunker(self.cfg).chunk(blocks)
        for b in blocks:
            if b.kind is BlockKind.TABLE:
                if count_tokens(b.text) <= self.cfg.max_tokens:
                    out.append(_table_chunk(b))
                else:
                    out.extend(_table_row_groups(b, self.cfg.table_rows_per_chunk))
        out.extend(_figure_chunks(blocks))
        return out


class ParentChildChunker:
    """Multimodal parent-child: parents are whole sections / tables / figures (given to the LLM);
    children are small windows / row groups (what gets embedded and retrieved)."""

    name = "parent_child"

    def __init__(self, cfg: ChunkingConfig | None = None) -> None:
        self.cfg = cfg or ChunkingConfig()

    def chunk(self, blocks: list[Block]) -> list[ChunkDraft]:
        out: list[ChunkDraft] = []
        for group in StructureChunker(self.cfg).sections(blocks):
            parent = ChunkDraft(
                "\n".join(b.text for b in group if b.text.strip()),
                ChunkType.SECTION,
                group,
                metadata={"role": "parent"},
            )
            out.append(parent)
            pidx = len(out) - 1
            units = [(s, b) for b in group for s in (split_sentences(b.text) or [b.text])]
            for child in _windows(
                units, self.cfg.child_tokens, min(30, self.cfg.overlap_tokens), ChunkType.TEXT
            ):
                child.parent_index, child.metadata["role"] = pidx, "child"
                out.append(child)
        for b in blocks:
            if b.kind is BlockKind.TABLE:
                parent = _table_chunk(b)
                parent.metadata["role"] = "parent"
                out.append(parent)
                pidx = len(out) - 1
                rows = max(1, min(self.cfg.table_rows_per_chunk, 4))
                for child in _table_row_groups(b, rows):
                    child.parent_index, child.metadata["role"] = pidx, "child"
                    out.append(child)
        for fig in _figure_chunks(blocks):
            fig.metadata["role"] = "leaf"  # figures are retrieved and used as-is
            out.append(fig)
        return out


def get_chunker(name: str, cfg: ChunkingConfig | None = None, embedder: Embedder | None = None):  # type: ignore[no-untyped-def]
    cfg = cfg or ChunkingConfig()
    match name:
        case "fixed":
            return FixedSizeChunker(cfg)
        case "recursive":
            return RecursiveChunker(cfg)
        case "semantic":
            if embedder is None:
                raise ValueError("semantic chunking requires an embedder")
            return SemanticChunker(embedder, cfg)
        case "structure":
            return StructureChunker(cfg)
        case "page":
            return PageChunker(cfg)
        case "table_aware":
            return TableAwareChunker(cfg)
        case "parent_child":
            return ParentChildChunker(cfg)
    raise ValueError(f"unknown chunking strategy: {name}")


STRATEGIES = ("fixed", "recursive", "semantic", "structure", "page", "table_aware", "parent_child")


def _cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _percentile(values: list[float], pct: float) -> float:
    s = sorted(values)
    if not s:
        return 1.0
    k = (len(s) - 1) * pct / 100
    lo, hi = math.floor(k), math.ceil(k)
    return s[lo] if lo == hi else s[lo] + (s[hi] - s[lo]) * (k - lo)
