"""Format routing, page rendering, OCR decisions and the Docling call (ADR 0002/0003).

Routing:
  PDF        -> probe text layer per page -> Docling (Tesseract OCR only where needed)
  PNG/JPEG   -> Docling image pipeline (OCR)
  DOCX/PPTX  -> LibreOffice -> PDF when `soffice` exists (real pages + bboxes for citations),
                else Docling's native Office backend (no page numbers; citations use sections)
  XLSX       -> Docling native backend (sheets)
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pypdfium2 as pdfium

from app.core.logging import get_logger
from app.extraction.layout.docling_adapter import to_graph
from app.extraction.models import DocumentGraph
from app.ingestion.validation import DocumentKind

logger = get_logger("app.parser")

TEXT_LAYER_MIN_CHARS = 20  # fewer extractable chars than this => treat the page as scanned
RENDER_DPI = 150


@dataclass(slots=True)
class RenderedPage:
    page_no: int
    path: Path
    width_pt: float
    height_pt: float


def probe_text_layer(pdf_path: Path) -> dict[int, bool]:
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        out = {}
        for i in range(len(pdf)):
            textpage = pdf[i].get_textpage()
            out[i + 1] = len(textpage.get_text_range().strip()) >= TEXT_LAYER_MIN_CHARS
        return out
    finally:
        pdf.close()


def render_pdf_pages(pdf_path: Path, out_dir: Path, dpi: int = RENDER_DPI) -> list[RenderedPage]:
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf = pdfium.PdfDocument(str(pdf_path))
    pages: list[RenderedPage] = []
    try:
        for i in range(len(pdf)):
            page = pdf[i]
            w, h = page.get_size()
            target = out_dir / f"{i + 1}.png"
            page.render(scale=dpi / 72).to_pil().save(target, format="PNG", optimize=True)
            pages.append(RenderedPage(i + 1, target, w, h))
    finally:
        pdf.close()
    return pages


def office_to_pdf(path: Path, workdir: Path, timeout_s: int = 180) -> Path | None:
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if soffice is None:
        return None
    out = workdir / "rendition"
    out.mkdir(exist_ok=True)
    # Isolated profile dir: concurrent conversions must not share LibreOffice state.
    profile = (workdir / "lo-profile").as_uri()
    subprocess.run(  # noqa: S603 - fixed argv, no shell, file path we created
        [
            soffice,
            f"-env:UserInstallation={profile}",
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            str(out),
            str(path),
        ],
        check=True,
        timeout=timeout_s,
        capture_output=True,
    )
    pdfs = list(out.glob("*.pdf"))
    return pdfs[0] if pdfs else None


def tesseract_available() -> bool:
    return shutil.which("tesseract") is not None


@lru_cache(maxsize=2)
def _converter(do_ocr: bool):  # type: ignore[no-untyped-def]
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, TesseractCliOcrOptions
    from docling.document_converter import DocumentConverter, ImageFormatOption, PdfFormatOption

    opts = PdfPipelineOptions(
        do_ocr=do_ocr,
        ocr_options=TesseractCliOcrOptions(lang=["eng"]),
        do_table_structure=True,
        do_picture_classification=True,
        generate_picture_images=True,
        images_scale=2.0,
    )
    opts.table_structure_options.do_cell_matching = True
    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=opts),
            InputFormat.IMAGE: ImageFormatOption(pipeline_options=opts),
        }
    )


def parse_document(
    path: Path, kind: DocumentKind, *, text_layer: dict[int, bool] | None = None
) -> DocumentGraph:
    """Run Docling on `path` (a PDF, image or Office file) and normalise the result."""
    do_ocr = tesseract_available()
    if not do_ocr:
        logger.warning(
            "ocr_unavailable", reason="tesseract binary not found; scanned pages yield no text"
        )
    result = _converter(do_ocr).convert(str(path))
    ocr_pages: set[int] = set()
    if do_ocr:
        if kind in {DocumentKind.PNG, DocumentKind.JPEG}:
            ocr_pages = {1}
        elif text_layer:
            ocr_pages = {p for p, has_text in text_layer.items() if not has_text}
    graph = to_graph(result.document, text_layer=text_layer, ocr_pages=ocr_pages)
    graph.metadata.update({"ocr_available": do_ocr, "source_kind": kind.value})
    return graph
