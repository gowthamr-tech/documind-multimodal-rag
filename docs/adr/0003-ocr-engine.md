# ADR 0003 — OCR engine

- **Status:** Accepted (2026-11-18)
- **Decision:** **Tesseract 5** through Docling's OCR interface, applied **only** to pages without a usable text layer (per-page text density threshold). RapidOCR (ONNX) is kept as a pluggable alternative and benchmarked in Phase 7 (CER/WER).
- **Rejected:** PaddleOCR needs the Paddle framework (multi-GB, awkward on arm64 macOS), which doesn't fit the disk budget.
- **Consequences:** OCR quality on dense or rotated scans may trail PaddleOCR. This is measured, not assumed. The interface allows a swap.
