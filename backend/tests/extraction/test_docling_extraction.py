"""End-to-end extraction quality on a synthetic report with exact ground truth.

Runs the real Docling models (slow on first run: model load). Thresholds are set from what the
system actually achieves; a regression below them fails the build.
"""

from decimal import Decimal
from typing import Any

import pytest

from app.evaluation.metrics.extraction import cell_prf
from app.extraction.models import DocumentGraph, ElementType, FigureType
from app.extraction.parser import parse_document, probe_text_layer
from app.ingestion.validation import DocumentKind
from benchmarks.synthetic import write_report

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def parsed(tmp_path_factory: pytest.TempPathFactory) -> tuple[DocumentGraph, dict[str, Any]]:
    pdf, truth = write_report("Northwind Analytics", 7, tmp_path_factory.mktemp("synth"))
    return parse_document(pdf, DocumentKind.PDF, text_layer=probe_text_layer(pdf)), truth


def _truth_grid(t: dict[str, Any]) -> list[list[str]]:
    return t["header_rows"] + t["body"]


def test_pages_and_text_layer(parsed: tuple[DocumentGraph, dict[str, Any]]) -> None:
    graph, _ = parsed
    assert [p.page_no for p in graph.pages] == [1, 2, 3]
    assert all(p.has_text_layer for p in graph.pages)
    assert not any(p.ocr_applied for p in graph.pages)  # born-digital: no OCR needed


def test_tables_recovered_cell_exact(parsed: tuple[DocumentGraph, dict[str, Any]]) -> None:
    graph, truth = parsed
    assert len(graph.tables) == 2
    for pred, gt in zip(graph.tables, truth["tables"], strict=True):
        assert pred.page_start == gt["page"]
        score = cell_prf(pred.grid(), _truth_grid(gt))
        assert score.f1 >= 0.95, (gt["id"], score)


def test_table_semantics(parsed: tuple[DocumentGraph, dict[str, Any]]) -> None:
    graph, truth = parsed
    t1, t2 = graph.tables
    assert t1.caption == truth["tables"][0]["caption"]
    assert t1.header_depth == 2
    assert (t1.unit, t1.scale) == ("USD", 1_000_000)
    assert t1.footnotes == truth["tables"][0]["footnotes"]

    cloud_2024 = next(
        c for c in t1.cells if c.row_path == ["Cloud Services"] and c.header_path[-1] == "2024"
    )
    assert cloud_2024.header_path == ["Fiscal year", "2024"]
    assert cloud_2024.numeric_value == Decimal(truth["tables"][0]["body"][0][2].replace(",", ""))
    assert cloud_2024.unit == "USD"

    change = next(
        c for c in t1.cells if c.row_path == ["Cloud Services"] and c.header_path == ["Change", "%"]
    )
    assert change.unit == "%"

    other = next(
        c for c in t2.cells if c.row_path == ["Other income (loss)"] and c.header_path[-1] == "2024"
    )
    assert other.numeric_value == Decimal(
        truth["facts"]["other_income"]["2024"]
    )  # "(21.2)" -> -21.2


def test_figures_classified_and_captioned(parsed: tuple[DocumentGraph, dict[str, Any]]) -> None:
    graph, truth = parsed
    assert [f.figure_type for f in graph.figures] == [FigureType.CHART, FigureType.CHART]
    assert [f.classifier_label for f in graph.figures] == ["bar_chart", "line_chart"]
    assert [f.page_no for f in graph.figures] == [2, 3]
    assert graph.figures[0].caption and graph.figures[0].caption.startswith("Figure 1")
    assert all(f.image_png and f.image_png.startswith(b"\x89PNG") for f in graph.figures)


def test_text_inside_chart_is_not_a_heading(parsed: tuple[DocumentGraph, dict[str, Any]]) -> None:
    graph, truth = parsed
    chart_title = truth["charts"][0]["title"]
    headings = [e.text for e in graph.elements if e.type is ElementType.SECTION_HEADER]
    assert chart_title not in headings
    assert all(chart_title not in e.section_path for e in graph.elements)


def test_section_paths_and_bboxes(parsed: tuple[DocumentGraph, dict[str, Any]]) -> None:
    graph, _ = parsed
    expense_para = next(
        e for e in graph.elements if e.text.startswith("The largest expense category")
    )
    assert expense_para.section_path[-1] == "Operating expenses"
    assert expense_para.page_no == 2
    x0, y0, x1, y1 = expense_para.bbox  # type: ignore[misc]
    assert 0 <= x0 < x1 <= 1
    assert 0 <= y0 < y1 <= 1
    assert y0 < 0.3  # near the top of page 2 (top-left origin)
