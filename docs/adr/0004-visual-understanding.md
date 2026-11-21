# ADR 0004 — Chart and image understanding

- **Status:** Accepted (2026-11-18)
- **Decision:** Docling's picture classifier routes figures to chart or image. Charts go to a **vision-language model with a strict JSON schema** (type, title, axes, legend, series, data points, trends). The output is validated against OCR'd axis ticks and the legend count. All chart-derived values carry `values_are_estimated = true`. Results are cached by `(crop_sha256, model, prompt_version)`.
- **Rejected:** OCR-only chart understanding (misses semantics). DePlot-style local models (extra weights; quality TBD, may revisit).
- **Consequences:** Needs an API key. Chart values are never presented as exact, and the calculator warns when a chart value is an operand.
