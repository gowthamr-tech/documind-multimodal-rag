"""DoclingDocument -> DocumentGraph. The only module that knows Docling's data model.

Beyond a 1:1 mapping it applies layout corrections found on real output:
  * text detected *inside* a figure (e.g. a chart's own title OCR'd from pixels) is attached to
    that figure, not treated as a document heading — otherwise it corrupts section paths;
  * table footnotes and figure captions that the parser left unlinked are attached by
    proximity (same page, adjacent in reading order);
  * page headers/footers are kept but flagged as furniture (excluded from body chunks).
"""

from __future__ import annotations

import io
from typing import Any

from docling_core.types.doc import DoclingDocument, PictureItem, SectionHeaderItem, TableItem
from docling_core.types.doc.document import ContentLayer, DocItem

from app.extraction.models import (
    BBox,
    CellRecord,
    DocumentGraph,
    ElementRecord,
    ElementType,
    FigureRecord,
    FigureType,
    PageRecord,
    TableRecord,
)
from app.extraction.tables.normalize import (
    apply_numeric_semantics,
    assign_header_paths,
    stitch_tables,
)

_LABELS: dict[str, ElementType] = {
    "title": ElementType.TITLE,
    "section_header": ElementType.SECTION_HEADER,
    "text": ElementType.PARAGRAPH,
    "paragraph": ElementType.PARAGRAPH,
    "reference": ElementType.PARAGRAPH,
    "list_item": ElementType.LIST_ITEM,
    "table": ElementType.TABLE,
    "picture": ElementType.FIGURE,
    "chart": ElementType.FIGURE,
    "caption": ElementType.CAPTION,
    "footnote": ElementType.FOOTNOTE,
    "page_header": ElementType.HEADER,
    "page_footer": ElementType.FOOTER,
    "formula": ElementType.FORMULA,
    "code": ElementType.CODE,
}

_CHART_CLASSES = {
    "bar_chart",
    "line_chart",
    "pie_chart",
    "scatter_plot",
    "box_plot",
    "area_chart",
    "stacked_bar_chart",
    "heatmap",
    "chart",
}
_DIAGRAM_CLASSES = {
    "flow_chart",
    "engineering_drawing",
    "chemistry_structure",
    "geographical_map",
    "topographical_map",
    "table",
}
_IMAGE_CLASSES = {
    "photograph",
    "logo",
    "icon",
    "signature",
    "stamp",
    "screenshot_from_computer",
    "screenshot_from_manual",
    "full_page_image",
    "page_thumbnail",
}


def _figure_type(label: str | None) -> FigureType:
    if label in _CHART_CLASSES or (label or "").endswith("_chart"):
        return FigureType.CHART
    if label in _DIAGRAM_CLASSES:
        return FigureType.DIAGRAM
    if label in _IMAGE_CLASSES:
        return FigureType.IMAGE
    return FigureType.OTHER


class _Geometry:
    def __init__(self, doc: DoclingDocument) -> None:
        self.sizes = {no: (p.size.width, p.size.height) for no, p in doc.pages.items()}

    def bbox(self, item: DocItem) -> tuple[int | None, BBox | None]:
        if not item.prov:
            return None, None
        prov = item.prov[0]
        return prov.page_no, self.norm(prov.page_no, prov.bbox)

    def norm(self, page_no: int, bbox: Any) -> BBox | None:
        size = self.sizes.get(page_no)
        if size is None or bbox is None:
            return None
        w, h = size
        tl = bbox.to_top_left_origin(page_height=h)
        clamp = lambda v: max(0.0, min(1.0, v))  # noqa: E731
        return (clamp(tl.l / w), clamp(tl.t / h), clamp(tl.r / w), clamp(tl.b / h))


def _inside(inner: BBox, outer: BBox, threshold: float = 0.8) -> bool:
    ix0, iy0 = max(inner[0], outer[0]), max(inner[1], outer[1])
    ix1, iy1 = min(inner[2], outer[2]), min(inner[3], outer[3])
    inter = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
    area = max(1e-9, (inner[2] - inner[0]) * (inner[3] - inner[1]))
    return inter / area >= threshold


def _adjacent_title(text_bbox: BBox, fig_bbox: BBox, max_gap: float = 0.02) -> bool:
    """A text band touching a figure's top/bottom edge and lying within its horizontal span.

    Layout models often carve a chart's own title out of the picture region, leaving it as a
    separate block flush against the figure."""
    within_x = text_bbox[0] >= fig_bbox[0] - 0.01 and text_bbox[2] <= fig_bbox[2] + 0.01
    gap_above = fig_bbox[1] - text_bbox[3]
    gap_below = text_bbox[1] - fig_bbox[3]
    return within_x and (0 <= gap_above <= max_gap or 0 <= gap_below <= max_gap)


def _figure_embedded_refs(items: list[Any], geo: _Geometry, doc: DoclingDocument) -> dict[str, str]:
    """Map text refs that belong to an adjacent figure (by reading order) -> figure ref."""
    out: dict[str, str] = {}
    docitems = [it for it, _ in items if isinstance(it, DocItem)]
    for i, it in enumerate(docitems):
        if not isinstance(it, PictureItem):
            continue
        fpage, fbox = geo.bbox(it)
        if fbox is None:
            continue
        caption = it.caption_text(doc).lower()
        for j in (i - 1, i + 1):
            if not 0 <= j < len(docitems):
                continue
            nb = docitems[j]
            if nb.label.value not in {"section_header", "title", "text", "paragraph"}:
                continue
            npage, nbox = geo.bbox(nb)
            text = (getattr(nb, "text", "") or "").strip().lower()
            if npage != fpage or nbox is None or not text:
                continue
            repeats_caption = len(text) >= 8 and text in caption
            if _adjacent_title(nbox, fbox) or repeats_caption:
                out[nb.self_ref] = it.self_ref
    return out


def _classification(item: PictureItem) -> tuple[str | None, float | None]:
    meta = getattr(item, "meta", None)
    cls = getattr(meta, "classification", None) if meta else None
    preds = getattr(cls, "predictions", None) if cls else None
    if preds:
        top = max(preds, key=lambda p: p.confidence)
        return top.class_name, float(top.confidence)
    return None, None


def _png(item: PictureItem, doc: DoclingDocument) -> bytes | None:
    img = item.get_image(doc)
    if img is None:
        return None
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _table(item: TableItem, ref: str, geo: _Geometry, doc: DoclingDocument) -> TableRecord:
    page_no, bbox = geo.bbox(item)
    cells: list[CellRecord] = []
    for c in item.data.table_cells:
        cells.append(
            CellRecord(
                row=c.start_row_offset_idx,
                col=c.start_col_offset_idx,
                row_span=max(1, c.row_span),
                col_span=max(1, c.col_span),
                is_header=bool(c.column_header or c.row_section),
                raw_text=(c.text or "").strip(),
                bbox=geo.norm(page_no, c.bbox)
                if page_no is not None and c.bbox is not None
                else None,
            )
        )
    caption = item.caption_text(doc).strip() or None
    footnotes = [
        doc_ref.resolve(doc).text
        for doc_ref in item.footnotes
        if hasattr(doc_ref.resolve(doc), "text")
    ]
    return TableRecord(
        ref=ref,
        page_start=page_no,
        page_end=page_no,
        n_rows=item.data.num_rows,
        n_cols=item.data.num_cols,
        cells=cells,
        caption=caption,
        footnotes=footnotes,
        bbox=bbox,
    )


def to_graph(
    doc: DoclingDocument,
    *,
    text_layer: dict[int, bool] | None = None,
    ocr_pages: set[int] | None = None,
) -> DocumentGraph:
    geo = _Geometry(doc)
    pages = [
        PageRecord(
            page_no=no,
            width=w,
            height=h,
            has_text_layer=(text_layer or {}).get(no),
            ocr_applied=no in (ocr_pages or set()),
        )
        for no, (w, h) in sorted(geo.sizes.items())
    ]

    items = list(
        doc.iterate_items(included_content_layers={ContentLayer.BODY, ContentLayer.FURNITURE})
    )
    figure_boxes: list[tuple[str, int, BBox]] = []
    for item, _ in items:
        if isinstance(item, PictureItem):
            page_no, bbox = geo.bbox(item)
            if page_no is not None and bbox is not None:
                figure_boxes.append((item.self_ref, page_no, bbox))

    embedded = _figure_embedded_refs(items, geo, doc)
    elements: list[ElementRecord] = []
    tables: list[TableRecord] = []
    figures: dict[str, FigureRecord] = {}
    title: list[str] = []
    stack: list[tuple[int, str]] = []
    order = 0

    def path() -> list[str]:
        return title + [t for _, t in stack]

    for item, _level in items:
        if not isinstance(item, DocItem):
            continue
        label = item.label.value
        etype = _LABELS.get(label, ElementType.OTHER)
        page_no, bbox = geo.bbox(item)
        text = (getattr(item, "text", "") or "").strip()
        furniture = item.content_layer == ContentLayer.FURNITURE or etype in {
            ElementType.HEADER,
            ElementType.FOOTER,
        }
        attributes: dict[str, Any] = {"parser_label": label}
        parent_ref: str | None = None

        # Text the layout model found inside a figure belongs to the figure, not the outline.
        host = embedded.get(item.self_ref)
        if (
            host is None
            and bbox is not None
            and etype not in {ElementType.FIGURE, ElementType.TABLE}
        ):
            host = next(
                (ref for ref, p, fb in figure_boxes if p == page_no and _inside(bbox, fb)), None
            )
        if host is not None:
            etype, parent_ref = ElementType.OTHER, host
            attributes["inside_figure"] = True
        elif etype is ElementType.TITLE and text:
            title, stack = [text], []
        elif etype is ElementType.SECTION_HEADER and text:
            level = item.level if isinstance(item, SectionHeaderItem) else 1
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, text))

        if isinstance(item, TableItem):
            t = _table(item, item.self_ref, geo, doc)
            t.section_path = path()
            tables.append(t)
            text = t.caption or ""
        elif isinstance(item, PictureItem):
            cls_label, conf = _classification(item)
            figures[item.self_ref] = FigureRecord(
                ref=item.self_ref,
                page_no=page_no,
                bbox=bbox,
                figure_type=_figure_type(cls_label),
                classifier_label=cls_label,
                classifier_confidence=conf,
                caption=item.caption_text(doc).strip() or None,
                image_png=_png(item, doc),
                section_path=path(),
            )
            attributes["classifier_label"] = cls_label
            text = figures[item.self_ref].caption or ""

        elements.append(
            ElementRecord(
                ref=item.self_ref,
                type=etype,
                reading_order=order,
                text=text,
                page_no=page_no,
                bbox=bbox,
                section_path=path(),
                parent_ref=parent_ref,
                is_furniture=furniture,
                attributes=attributes,
            )
        )
        order += 1

    _attach_by_proximity(elements, tables, figures)
    for t in tables:
        assign_header_paths(t)
    tables = stitch_tables(tables)
    for t in tables:
        assign_header_paths(t)
        apply_numeric_semantics(t)

    return DocumentGraph(
        pages=pages,
        elements=elements,
        tables=tables,
        figures=list(figures.values()),
        metadata={"parser": "docling", "name": doc.name},
    )


def _attach_by_proximity(
    elements: list[ElementRecord], tables: list[TableRecord], figures: dict[str, FigureRecord]
) -> None:
    by_ref = {t.ref: t for t in tables}
    for i, el in enumerate(elements):
        neighbours = [e for e in elements[max(0, i - 2) : i + 3] if e is not el]
        if el.type is ElementType.FOOTNOTE:
            # footnote following a table on the same page
            prev = next(
                (e for e in reversed(elements[max(0, i - 3) : i]) if e.type is ElementType.TABLE),
                None,
            )
            if prev and prev.page_no == el.page_no and prev.ref in by_ref:
                t = by_ref[prev.ref]
                if el.text and el.text not in t.footnotes:
                    t.footnotes.append(el.text)
                    el.parent_ref = t.ref
        if el.type is ElementType.CAPTION and el.text:
            for n in neighbours:
                if n.page_no != el.page_no:
                    continue
                if (
                    n.ref in figures
                    and figures[n.ref].caption is None
                    and el.text.lower().startswith(("figure", "fig.", "chart", "exhibit"))
                ):
                    figures[n.ref].caption = el.text
                    n.text = el.text
                    el.parent_ref = n.ref
                    break
                if (
                    n.ref in by_ref
                    and by_ref[n.ref].caption is None
                    and el.text.lower().startswith("table")
                ):
                    by_ref[n.ref].caption = el.text
                    n.text = el.text
                    el.parent_ref = n.ref
                    break
    for el in elements:
        if el.attributes.get("inside_figure") and el.parent_ref in figures and el.text:
            figures[el.parent_ref].embedded_text.append(el.text)
