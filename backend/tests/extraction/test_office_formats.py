"""Native Office parsing (no LibreOffice on this host): structure must survive, and pages are
honestly absent rather than invented."""

from decimal import Decimal
from pathlib import Path

import docx
import openpyxl
import pptx
import pytest
from pptx.util import Inches

from app.extraction.models import ElementType
from app.extraction.parser import parse_document
from app.ingestion.validation import DocumentKind

pytestmark = pytest.mark.slow


def test_docx_headings_paragraphs_and_table(tmp_path: Path) -> None:
    d = docx.Document()
    d.add_heading("Vendor Agreement", level=1)
    d.add_heading("Payment terms", level=2)
    d.add_paragraph("Invoices are payable within 45 days of receipt.")
    t = d.add_table(rows=3, cols=2)
    for r, (k, v) in enumerate(
        [("Item", "Amount (USD)"), ("Licence", "12,500.00"), ("Support", "(1,200.00)")]
    ):
        t.cell(r, 0).text, t.cell(r, 1).text = k, v
    path = tmp_path / "agreement.docx"
    d.save(path)

    g = parse_document(path, DocumentKind.DOCX)
    para = next(e for e in g.elements if "45 days" in e.text)
    assert para.section_path[-1] == "Payment terms"
    assert para.page_no is None  # DOCX has no intrinsic pages; we don't invent one
    assert len(g.tables) == 1
    support = next(c for c in g.tables[0].cells if c.raw_text == "(1,200.00)")
    assert support.numeric_value == Decimal("-1200.00")


def test_xlsx_sheet_becomes_table(tmp_path: Path) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Budget"
    for row in [["Department", "Q1", "Q2"], ["Engineering", 120.5, 130.0], ["Sales", 80.0, 95.25]]:
        ws.append(row)
    path = tmp_path / "budget.xlsx"
    wb.save(path)

    g = parse_document(path, DocumentKind.XLSX)
    assert len(g.tables) >= 1
    grid = g.tables[0].grid()
    flat = {v for row in grid for v in row}
    assert {"Department", "Engineering", "Sales"} <= flat
    assert any(v.startswith("95.25") for v in flat)


def test_pptx_slides_map_to_pages(tmp_path: Path) -> None:
    prs = pptx.Presentation()
    for title, body in [("Q3 Review", "Revenue up 12%"), ("Risks", "Supply chain delays")]:
        slide = prs.slides.add_slide(prs.slide_layouts[1])
        slide.shapes.title.text = title  # type: ignore[union-attr]
        slide.placeholders[1].text = body  # type: ignore[attr-defined]
    s3 = prs.slides.add_slide(prs.slide_layouts[5])
    tbl = s3.shapes.add_table(2, 2, Inches(1), Inches(2), Inches(4), Inches(1)).table
    tbl.cell(0, 0).text, tbl.cell(0, 1).text = "Metric", "Value"
    tbl.cell(1, 0).text, tbl.cell(1, 1).text = "Churn", "3.1%"
    path = tmp_path / "deck.pptx"
    prs.save(path)

    g = parse_document(path, DocumentKind.PPTX)
    risk = next(e for e in g.elements if "Supply chain" in e.text)
    assert risk.page_no == 2  # slide number
    churn = next(c for t in g.tables for c in t.cells if c.raw_text == "3.1%")
    assert churn.numeric_value == Decimal("3.1")
    assert churn.unit == "%"
    assert any(e.type in {ElementType.TITLE, ElementType.SECTION_HEADER} for e in g.elements)
