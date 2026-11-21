// Mirrors the backend Pydantic contracts (docs/api-contracts.md).
export type VersionStatus = "uploaded" | "queued" | "processing" | "ready" | "failed";
export type Verification = "verified" | "partial" | "unsupported" | "unverified";
export type Summary = "verified" | "partially_verified" | "insufficient_evidence";

export interface Page<T> { items: T[]; next_cursor: string | null }

export interface Version {
  id: string; version_no: number; sha256: string; mime_type: string; size_bytes: number;
  original_filename: string; page_count: number | null; status: VersionStatus;
  pipeline_version: string | null; error: { stage?: string; message?: string } | null; created_at: string;
}

export interface DocumentSummary {
  id: string; title: string; tags: string[]; sensitivity: "normal" | "confidential";
  classification: string | null; created_at: string; updated_at: string; current_version: Version | null;
}
export interface DocumentDetail extends DocumentSummary { versions: Version[] }

export interface Stage {
  stage: string; status: "pending" | "running" | "succeeded" | "failed" | "skipped"; attempt: number;
  started_at: string | null; finished_at: string | null; metrics: Record<string, unknown>;
  error: { message?: string } | null;
}
export interface StatusOut { document_id: string; version_id: string; status: VersionStatus; error: Version["error"]; stages: Stage[] }
export interface ProcessingJob extends Stage { id: string; document_version_id: string; created_at: string }

export interface PageInfo { page_no: number; width: number; height: number; has_text_layer: boolean | null; ocr_applied: boolean; has_image: boolean }
export interface Cell {
  row_idx: number; col_idx: number; row_span: number; col_span: number; is_header: boolean;
  header_path: string[]; row_path: string[]; raw_text: string; numeric_value: string | null;
  unit: string | null; scale: number | null;
}
export interface TableInfo {
  id: string; page_start: number | null; page_end: number | null; bbox: number[] | null; caption: string | null;
  footnotes: string[]; units: string | null; header_depth: number; n_rows: number; n_cols: number;
  section_path: string[]; markdown: string; stitched_from: number; cells: Cell[];
}
export interface ChartPoint { x: string; y: number | null; value_label_visible: boolean }
export interface FigureInfo {
  id: string; page_no: number | null; bbox: number[] | null; figure_type: string; classifier_label: string | null;
  classifier_confidence: number | null; caption: string | null; section_path: string[];
  analysis_status: "pending" | "succeeded" | "failed" | "skipped"; description: string | null;
  chart: { chart_type: string; title: string | null; series: { name: string; points: ChartPoint[] }[]; trends: string[] } | null;
  values_are_estimated: boolean; validation: { points_total?: number; points_verified?: number; ocr_available?: boolean } | null;
  model: string | null; error: string | null; has_image: boolean;
}

export interface Citation {
  citation_id: string; chunk_id: string; document_id: string; version_id: string; document_title: string;
  page: number | null; page_end: number | null; section: string[]; bbox: number[] | null; bbox_page: number | null;
  chunk_type: string; quote: string; quote_is_verbatim: boolean; verification: Verification;
}
export interface ClaimCheck {
  text: string; citation_ids: string[]; status: "verified" | "partial" | "unsupported";
  invalid_citations: string[]; ungrounded_numbers: string[]; notes: string[];
}
export interface Answer {
  answer_markdown: string; claims: ClaimCheck[]; citations: Citation[];
  computations: Record<string, { display: string; expression: string; estimated: boolean }>;
  verification_summary: Summary; limitations: string[]; route: string[];
  conversation_id: string; run_id: string; message_id?: string;
  usage?: { prompt_tokens: number; completion_tokens: number; cost_usd: string | null; latency_ms: number };
}
export interface PendingApproval { status: "awaiting_approval"; approval_id: string; run_id: string }
export interface SearchHit { citation: Citation; content: string; context: string; scores: Record<string, number> }
