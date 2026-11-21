# ADR 0002 — Document parsing and rendering

- **Status:** Accepted (2026-11-18)
- **Options:** Docling, Unstructured, PyMuPDF, pypdfium2.
- **Decision:** **Docling** (MIT) is the primary parser for PDF, DOCX, PPTX, XLSX and images: layout model, reading order, section hierarchy, TableFormer table structure, bbox provenance. **pypdfium2** (Apache-2.0/BSD) for page rendering, text-layer density probes and image extraction. **LibreOffice headless** (worker image only) renders DOCX/PPTX to PDF for display and page anchors. Content is still parsed natively.
- **Rejected:** PyMuPDF is AGPL-3.0, which is incompatible with commercial use without a paid licence. Unstructured overlaps Docling and its hi_res path adds a heavy dependency tree with weaker table structure.
- **Consequences:** The worker image carries PyTorch (CPU) and Docling models. DOCX page numbers are approximate (see ADR 0004 / risk R4).
