"""Deterministic synthetic financial reports with exact ground truth.

Every number in the rendered PDF comes from a `ReportSpec`, and the same spec is serialised as
ground truth, so extraction accuracy (tables, charts, text) can be measured exactly, not
eyeballed. Companies and figures are fictional.

Layout (per report):
  p1  title, executive summary, Table 1 "Revenue by segment" (2-level header, units, footnote)
  p2  Table 2 "Operating expenses" (incl. a negative value in parentheses), Figure 1 bar chart
  p3  Figure 2 line chart (quarterly revenue), outlook narrative

Dev-only: requires reportlab + matplotlib (dev dependency group).
"""

from __future__ import annotations

import io
import json
import random
from dataclasses import asdict, dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

SEGMENTS = ["Cloud Services", "Enterprise Software", "Hardware", "Consulting"]
EXPENSES = [
    "Research and development",
    "Sales and marketing",
    "General and administrative",
    "Cost of revenue",
    "Restructuring",
]
QUARTERS = ["Q1", "Q2", "Q3", "Q4"]


def _d(x: float) -> Decimal:
    return Decimal(str(x)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


@dataclass
class ReportSpec:
    company: str
    years: tuple[int, int]
    seed: int
    revenue: dict[str, dict[int, Decimal]] = field(default_factory=dict)  # segment -> year -> USD m
    expenses: dict[str, dict[int, Decimal]] = field(default_factory=dict)
    quarterly: dict[str, Decimal] = field(default_factory=dict)  # later year, Q -> USD m
    other_income: dict[int, Decimal] = field(default_factory=dict)  # may be negative

    @classmethod
    def generate(cls, company: str, seed: int, years: tuple[int, int] = (2023, 2024)) -> ReportSpec:
        rng = random.Random(seed)
        spec = cls(company=company, years=years, seed=seed)
        y0, y1 = years
        for seg in SEGMENTS:
            base = rng.uniform(80, 900)
            growth = rng.uniform(-0.12, 0.35)
            spec.revenue[seg] = {y0: _d(base), y1: _d(base * (1 + growth))}
        total_rev_y1 = sum(v[y1] for v in spec.revenue.values())
        for cat in EXPENSES:
            share = rng.uniform(0.04, 0.3) if cat != "Restructuring" else rng.uniform(0.0, 0.03)
            spec.expenses[cat] = {
                y0: _d(float(total_rev_y1) * share * rng.uniform(0.85, 1.0)),
                y1: _d(float(total_rev_y1) * share),
            }
        weights = [rng.uniform(0.8, 1.2) for _ in QUARTERS]
        scale = float(total_rev_y1) / sum(weights)
        quarters = [_d(w * scale) for w in weights]
        quarters[-1] += total_rev_y1 - sum(quarters)  # quarters sum exactly to the annual total
        spec.quarterly = dict(zip(QUARTERS, quarters, strict=True))
        spec.other_income = {y0: _d(rng.uniform(-40, 25)), y1: _d(rng.uniform(-40, 25))}
        return spec

    # ---- derived facts (ground truth for QA) ---------------------------------------------
    def total_revenue(self, year: int) -> Decimal:
        return sum((v[year] for v in self.revenue.values()), Decimal("0"))

    def revenue_growth_pct(self) -> Decimal:
        y0, y1 = self.years
        return (
            (self.total_revenue(y1) - self.total_revenue(y0)) / self.total_revenue(y0) * 100
        ).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)

    def largest_expense(self, year: int) -> tuple[str, Decimal]:
        cat = max(self.expenses, key=lambda c: self.expenses[c][year])
        return cat, self.expenses[cat][year]

    def segment_change_pct(self, seg: str) -> Decimal:
        y0, y1 = self.years
        a, b = self.revenue[seg][y0], self.revenue[seg][y1]
        return ((b - a) / a * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def fmt_money(v: Decimal) -> str:
    """Accounting format: thousands separators, negatives in parentheses."""
    s = f"{abs(v):,.1f}"
    return f"({s})" if v < 0 else s


# ------------------------------------------------------------------------------------------
# Rendering
# ------------------------------------------------------------------------------------------
def _bar_chart_png(spec: ReportSpec) -> bytes:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    y0, y1 = spec.years
    fig, ax = plt.subplots(figsize=(6.4, 3.4), dpi=150)
    xs = range(len(SEGMENTS))
    w = 0.38
    b0 = ax.bar(
        [x - w / 2 for x in xs], [float(spec.revenue[s][y0]) for s in SEGMENTS], w, label=str(y0)
    )
    b1 = ax.bar(
        [x + w / 2 for x in xs], [float(spec.revenue[s][y1]) for s in SEGMENTS], w, label=str(y1)
    )
    ax.bar_label(b0, fmt="%.1f", fontsize=7)
    ax.bar_label(b1, fmt="%.1f", fontsize=7)
    ax.set_xticks(list(xs), SEGMENTS, fontsize=8)
    ax.set_ylabel("Revenue (USD millions)")
    ax.set_title(f"Revenue by segment, FY{y0} vs FY{y1}")
    ax.legend()
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()


def _line_chart_png(spec: ReportSpec) -> bytes:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(6.4, 3.0), dpi=150)
    vals = [float(spec.quarterly[q]) for q in QUARTERS]
    ax.plot(QUARTERS, vals, marker="o")
    for q, v in zip(QUARTERS, vals, strict=True):
        ax.annotate(
            f"{v:.1f}", (q, v), textcoords="offset points", xytext=(0, 6), ha="center", fontsize=7
        )
    ax.set_ylabel("Revenue (USD millions)")
    ax.set_xlabel(f"Quarter of FY{spec.years[1]}")
    ax.set_title(f"Quarterly revenue, FY{spec.years[1]}")
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()


def render_pdf(spec: ReportSpec, path: Path) -> dict[str, Any]:
    """Render the report and return its ground truth."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        Image,
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    styles = getSampleStyleSheet()
    y0, y1 = spec.years
    story: list[Any] = []
    grid = TableStyle(
        [
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("BACKGROUND", (0, 0), (-1, 1), colors.HexColor("#e8eef7")),
            ("FONTNAME", (0, 0), (-1, 1), "Helvetica-Bold"),
            ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
        ]
    )

    # Page 1
    story.append(Paragraph(f"{spec.company} — Annual Report FY{y1}", styles["Title"]))
    story.append(Paragraph("Executive summary", styles["Heading2"]))
    growth = spec.revenue_growth_pct()
    story.append(
        Paragraph(
            f"Total revenue was USD {fmt_money(spec.total_revenue(y1))} million in FY{y1}, compared with "
            f"USD {fmt_money(spec.total_revenue(y0))} million in FY{y0}, a change of {growth}%. "
            "Segment results are presented in Table 1.",
            styles["BodyText"],
        )
    )
    story.append(Spacer(1, 0.15 * inch))
    story.append(Paragraph("Revenue by segment", styles["Heading2"]))
    story.append(Paragraph("Table 1: Revenue by segment (USD millions)", styles["Italic"]))
    t1 = [["Segment", "Fiscal year", "", "Change"], ["", str(y0), str(y1), "%"]]
    for seg in SEGMENTS:
        t1.append(
            [
                seg,
                fmt_money(spec.revenue[seg][y0]),
                fmt_money(spec.revenue[seg][y1]),
                f"{spec.segment_change_pct(seg)}",
            ]
        )
    t1.append(
        [
            "Total revenue (1)",
            fmt_money(spec.total_revenue(y0)),
            fmt_money(spec.total_revenue(y1)),
            f"{growth}",
        ]
    )
    tbl1 = Table(t1, colWidths=[2.2 * inch, 1.1 * inch, 1.1 * inch, 0.9 * inch])
    s1 = TableStyle(grid.getCommands() + [("SPAN", (1, 0), (2, 0)), ("SPAN", (0, 0), (0, 1))])
    tbl1.setStyle(s1)
    story.append(tbl1)
    story.append(Paragraph("(1) Excludes revenue from discontinued operations.", styles["Italic"]))
    story.append(PageBreak())

    # Page 2
    story.append(Paragraph("Operating expenses", styles["Heading2"]))
    big_cat, big_val = spec.largest_expense(y1)
    story.append(
        Paragraph(
            f"The largest expense category in FY{y1} was {big_cat.lower()} at USD {fmt_money(big_val)} million.",
            styles["BodyText"],
        )
    )
    story.append(Paragraph("Table 2: Operating expenses (USD millions)", styles["Italic"]))
    t2 = [["Category", "Fiscal year", ""], ["", str(y0), str(y1)]]
    for cat in EXPENSES:
        t2.append([cat, fmt_money(spec.expenses[cat][y0]), fmt_money(spec.expenses[cat][y1])])
    t2.append(
        ["Other income (loss)", fmt_money(spec.other_income[y0]), fmt_money(spec.other_income[y1])]
    )
    tbl2 = Table(t2, colWidths=[2.6 * inch, 1.1 * inch, 1.1 * inch])
    tbl2.setStyle(
        TableStyle(grid.getCommands() + [("SPAN", (1, 0), (2, 0)), ("SPAN", (0, 0), (0, 1))])
    )
    story.append(tbl2)
    story.append(Spacer(1, 0.2 * inch))
    story.append(Paragraph(f"Figure 1: Revenue by segment, FY{y0} vs FY{y1}", styles["Italic"]))
    story.append(Image(io.BytesIO(_bar_chart_png(spec)), width=6.0 * inch, height=3.2 * inch))
    story.append(PageBreak())

    # Page 3
    story.append(Paragraph("Quarterly performance", styles["Heading2"]))
    story.append(Paragraph(f"Figure 2: Quarterly revenue, FY{y1}", styles["Italic"]))
    story.append(Image(io.BytesIO(_line_chart_png(spec)), width=6.0 * inch, height=2.8 * inch))
    story.append(Paragraph("Outlook", styles["Heading2"]))
    story.append(
        Paragraph(
            "Management expects continued investment in cloud services. Forward-looking statements are "
            "subject to risks and uncertainties.",
            styles["BodyText"],
        )
    )

    SimpleDocTemplate(str(path), pagesize=LETTER, title=f"{spec.company} FY{y1}").build(story)
    return ground_truth(spec)


def ground_truth(spec: ReportSpec) -> dict[str, Any]:
    y0, y1 = spec.years
    big_cat, big_val = spec.largest_expense(y1)
    return {
        "company": spec.company,
        "seed": spec.seed,
        "years": list(spec.years),
        "tables": [
            {
                "id": "table1",
                "page": 1,
                "caption": "Table 1: Revenue by segment (USD millions)",
                "units": "USD millions",
                "footnotes": ["(1) Excludes revenue from discontinued operations."],
                "header_rows": [
                    ["Segment", "Fiscal year", "Fiscal year", "Change"],
                    ["Segment", str(y0), str(y1), "%"],
                ],
                "body": [
                    [
                        s,
                        fmt_money(spec.revenue[s][y0]),
                        fmt_money(spec.revenue[s][y1]),
                        str(spec.segment_change_pct(s)),
                    ]
                    for s in SEGMENTS
                ]
                + [
                    [
                        "Total revenue (1)",
                        fmt_money(spec.total_revenue(y0)),
                        fmt_money(spec.total_revenue(y1)),
                        str(spec.revenue_growth_pct()),
                    ]
                ],
            },
            {
                "id": "table2",
                "page": 2,
                "caption": "Table 2: Operating expenses (USD millions)",
                "units": "USD millions",
                "footnotes": [],
                "header_rows": [
                    ["Category", "Fiscal year", "Fiscal year"],
                    ["Category", str(y0), str(y1)],
                ],
                "body": [
                    [c, fmt_money(spec.expenses[c][y0]), fmt_money(spec.expenses[c][y1])]
                    for c in EXPENSES
                ]
                + [
                    [
                        "Other income (loss)",
                        fmt_money(spec.other_income[y0]),
                        fmt_money(spec.other_income[y1]),
                    ]
                ],
            },
        ],
        "charts": [
            {
                "id": "figure1",
                "page": 2,
                "type": "bar",
                "title": f"Revenue by segment, FY{y0} vs FY{y1}",
                "x_labels": SEGMENTS,
                "y_label": "Revenue (USD millions)",
                "series": {str(y): [str(spec.revenue[s][y]) for s in SEGMENTS] for y in spec.years},
            },
            {
                "id": "figure2",
                "page": 3,
                "type": "line",
                "title": f"Quarterly revenue, FY{y1}",
                "x_labels": QUARTERS,
                "y_label": "Revenue (USD millions)",
                "series": {str(y1): [str(spec.quarterly[q]) for q in QUARTERS]},
            },
        ],
        "facts": {
            "total_revenue": {str(y): str(spec.total_revenue(y)) for y in spec.years},
            "revenue_growth_pct": str(spec.revenue_growth_pct()),
            "largest_expense": {"category": big_cat, "value": str(big_val), "year": y1, "page": 2},
            "other_income": {str(y): str(v) for y, v in spec.other_income.items()},
        },
    }


def write_report(company: str, seed: int, out_dir: Path) -> tuple[Path, dict[str, Any]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = company.lower().replace(" ", "-")
    pdf = out_dir / f"{slug}-fy.pdf"
    truth = render_pdf(ReportSpec.generate(company, seed), pdf)
    (out_dir / f"{slug}-fy.truth.json").write_text(json.dumps(truth, indent=2))
    return pdf, truth


if __name__ == "__main__":  # pragma: no cover
    import sys

    target = Path(sys.argv[1] if len(sys.argv) > 1 else "../evaluation/datasets/synthetic")
    for name, seed in [("Northwind Analytics", 7), ("Blue Harbor Systems", 11)]:
        p, _ = write_report(name, seed, target)
        print("wrote", p)
    _ = asdict  # keep import for downstream scripts
