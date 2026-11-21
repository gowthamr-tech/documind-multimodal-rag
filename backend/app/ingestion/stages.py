"""Phase 3 pipeline stages: render -> parse/extract -> analyze figures."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from sqlalchemy import select, update

from app.core.config import get_settings
from app.core.logging import get_logger
from app.database.models import DocumentVersion, Figure
from app.database.models.extraction import FigureAnalysisStatus
from app.database.session import sync_session_scope
from app.extraction.charts.analyze import analyze_chart, describe_image
from app.extraction.parser import office_to_pdf, parse_document, probe_text_layer, render_pdf_pages
from app.extraction.persist import replace_graph
from app.ingestion.pipeline import StageContext, StageError
from app.ingestion.validation import DocumentKind
from app.providers.llm import ProviderError, get_vision_model

logger = get_logger("app.stages")


def _kind(ctx: StageContext) -> DocumentKind:
    with sync_session_scope(ctx.org_id) as s:
        meta = s.execute(
            select(DocumentVersion.metadata_).where(DocumentVersion.id == ctx.version_id)
        ).scalar_one()
    return DocumentKind(meta["kind"])


def _prefix(ctx: StageContext) -> str:
    return ctx.storage_key.rsplit("/", 1)[0]  # org/{org}/docs/{doc}/versions/{ver}


def stage_render(ctx: StageContext) -> dict[str, Any]:
    """Produce a PDF rendition when possible, probe text layers, render page images."""
    assert ctx.original_path is not None  # noqa: S101 - fetch_verify ran first
    kind = _kind(ctx)
    ctx.state["kind"] = kind.value
    pdf_path = None
    if kind is DocumentKind.PDF:
        pdf_path = ctx.original_path
    elif kind in {DocumentKind.DOCX, DocumentKind.PPTX}:
        named = ctx.original_path.with_suffix(f".{kind.value}")
        ctx.original_path.rename(named)
        ctx.original_path = named
        pdf_path = office_to_pdf(named, ctx.workdir)
        ctx.state["page_numbers_approximate"] = kind is DocumentKind.DOCX and pdf_path is not None
        if pdf_path is not None:
            ctx.storage.put_file(f"{_prefix(ctx)}/rendition.pdf", pdf_path, "application/pdf")
    ctx.state["pdf_path"] = str(pdf_path) if pdf_path else None

    image_keys: dict[int, str] = {}
    if pdf_path is not None:
        ctx.state["text_layer"] = probe_text_layer(pdf_path)
        for page in render_pdf_pages(pdf_path, ctx.workdir / "pages"):
            key = f"{_prefix(ctx)}/pages/{page.page_no}.png"
            ctx.storage.put_file(key, page.path, "image/png")
            image_keys[page.page_no] = key
    elif kind in {DocumentKind.PNG, DocumentKind.JPEG}:
        key = f"{_prefix(ctx)}/pages/1.png"
        from PIL import Image

        png = ctx.workdir / "page1.png"
        with Image.open(ctx.original_path) as img:
            img.convert("RGB").save(png, format="PNG")
        ctx.storage.put_file(key, png, "image/png")
        image_keys[1] = key
    ctx.state["page_image_keys"] = image_keys
    return {"pages_rendered": len(image_keys), "rendition": "pdf" if pdf_path else "native"}


def stage_parse(ctx: StageContext) -> dict[str, Any]:
    kind = DocumentKind(ctx.state["kind"])
    source = ctx.state.get("pdf_path") or str(ctx.original_path)
    parse_kind = DocumentKind.PDF if ctx.state.get("pdf_path") else kind
    from pathlib import Path

    graph = parse_document(Path(source), parse_kind, text_layer=ctx.state.get("text_layer"))
    if not graph.elements and not graph.tables:
        raise StageError("No content could be extracted from this document.")

    crop_keys: dict[str, str] = {}
    for i, fig in enumerate(graph.figures):
        if fig.image_png:
            key = f"{_prefix(ctx)}/figures/{i}.png"
            ctx.storage.put_bytes(key, fig.image_png, "image/png")
            crop_keys[fig.ref] = key

    with sync_session_scope(ctx.org_id) as s:
        counts = replace_graph(
            s,
            org_id=ctx.org_id,
            version_id=ctx.version_id,
            graph=graph,
            page_image_keys={int(k): v for k, v in ctx.state.get("page_image_keys", {}).items()},
            figure_crop_keys=crop_keys,
        )
        s.execute(
            update(DocumentVersion)
            .where(DocumentVersion.id == ctx.version_id)
            .values(page_count=graph.page_count or None)
        )
    ctx.state["page_count"] = graph.page_count
    ocr_pages = sum(1 for p in graph.pages if p.ocr_applied)
    return {**counts, "ocr_pages": ocr_pages, "ocr_available": graph.metadata.get("ocr_available")}


def stage_analyze_figures(ctx: StageContext) -> dict[str, Any]:
    """VLM chart extraction + OCR validation; image descriptions. Skips honestly without a key."""
    settings = get_settings()
    with sync_session_scope(ctx.org_id) as s:
        figures = s.execute(
            select(
                Figure.id, Figure.figure_type, Figure.crop_key, Figure.caption, Figure.embedded_text
            ).where(
                Figure.document_version_id == ctx.version_id,
                Figure.analysis_status == FigureAnalysisStatus.PENDING,
            )
        ).all()
    if not figures:
        return {"figures": 0}

    vlm = get_vision_model(settings)
    if vlm is None:
        with sync_session_scope(ctx.org_id) as s:
            s.execute(
                update(Figure)
                .where(Figure.id.in_([f.id for f in figures]))
                .values(
                    analysis_status=FigureAnalysisStatus.SKIPPED,
                    error="No vision model configured (set GEMINI_API_KEY).",
                )
            )
        return {"figures": len(figures), "skipped": len(figures), "reason": "no_vision_model"}

    def work(f: Any) -> tuple[Any, dict[str, Any]]:
        try:
            png = ctx.storage.get_bytes(f.crop_key)
            if f.figure_type == "chart":
                a = analyze_chart(vlm, png, caption=f.caption, embedded_text=list(f.embedded_text))
                return f, {
                    "analysis_status": FigureAnalysisStatus.SUCCEEDED,
                    "chart": a.chart.model_dump(),
                    "description": a.chart.description,
                    "validation": a.validation,
                    "values_are_estimated": a.values_are_estimated,
                    "model": a.model,
                    "prompt_version": a.prompt_version,
                    "_tokens": a.usage.input_tokens + a.usage.output_tokens,
                }
            desc, model, usage = describe_image(vlm, png, caption=f.caption)
            return f, {
                "analysis_status": FigureAnalysisStatus.SUCCEEDED,
                "description": desc,
                "model": model,
                "prompt_version": "image-v1",
                "_tokens": usage.input_tokens + usage.output_tokens,
            }
        except (ProviderError, OSError) as exc:
            logger.warning("figure_analysis_failed", figure_id=str(f.id), error=str(exc))
            return f, {"analysis_status": FigureAnalysisStatus.FAILED, "error": str(exc)[:500]}

    with ThreadPoolExecutor(max_workers=settings.figure_analysis_concurrency) as pool:
        results = list(pool.map(work, figures))
    tokens = 0
    with sync_session_scope(ctx.org_id) as s:
        for f, values in results:
            tokens += values.pop("_tokens", 0)
            s.execute(update(Figure).where(Figure.id == f.id).values(**values))
    ok = sum(1 for _, v in results if v["analysis_status"] is FigureAnalysisStatus.SUCCEEDED)
    return {"figures": len(figures), "succeeded": ok, "failed": len(figures) - ok, "tokens": tokens}


def stage_chunk_embed(ctx: StageContext) -> dict[str, Any]:
    """Chunk with the configured strategy and embed into the default space."""
    from app.chunking.indexer import index_version
    from app.embeddings.providers import get_embedder

    settings = get_settings()
    embedder = get_embedder(settings.embedding_provider, settings.embedding_model)
    with sync_session_scope(ctx.org_id) as s:
        stats = index_version(
            s,
            org_id=ctx.org_id,
            version_id=ctx.version_id,
            embedder=embedder,
            strategy=settings.chunking_strategy,
        )
    return {
        "strategy": settings.chunking_strategy,
        "chunks": stats.chunks,
        "embedded": stats.embedded,
        "parents": stats.parents,
        "embedding_model": embedder.model,
    }
