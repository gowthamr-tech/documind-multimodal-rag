# API Contracts — `/api/v1`

The machine-readable source of truth is the generated OpenAPI document (`/api/v1/openapi.json`).
This file records the meaning behind it: semantics, status codes and invariants.

## Conventions

- **Auth:** `Authorization: Bearer <access JWT>`. The org context comes from the token's `org_id` claim.
- **Errors:** RFC 9457 `application/problem+json`:
  `{type, title, status, detail, instance, request_id, errors?}`. `errors` holds field-level validation issues.
- **Pagination:** `?cursor=<opaque>&limit=<1..100, default 20>` → `{items, next_cursor}`.
- **Idempotency:** mutating endpoints accept an `Idempotency-Key` header (Redis-cached response, 24 h).
- **Request ID:** every response includes `X-Request-ID`, echoed from the request or generated.
- **Rate limits:** `429` with `Retry-After`. Headers: `RateLimit-Limit`, `RateLimit-Remaining`.

## Endpoints

| Method & path | Request | Success | Errors | Phase |
|---|---|---|---|---|
| `GET /health/live` | — | `200 {status:"ok"}` | — | 2.1 |
| `GET /health/ready` | — | `200 {status, checks:{db,redis,storage}}` | `503` | 2.2 |
| `POST /auth/register` | `{email, password, org_name}` | `201 TokenPair` | 409, 422 | 2.5 |
| `POST /auth/login` | `{email, password}` | `200 TokenPair` | 401 | 2.5 |
| `POST /auth/refresh` | `{refresh_token}` | `200 TokenPair` | 401 (reuse revokes family) | 2.5 |
| `POST /auth/logout` | `{refresh_token}` | `204` | — | 2.5 |
| `POST /documents/upload` | multipart `file`, `document_id?`, `title?`, `tags[]?` | `202 {document_id, version_id, status, deduplicated}` | 413, 415, 422 | 2.6 |
| `GET /documents` | `q, tag, status, mime, cursor, limit` | `200 Page[DocumentSummary]` | — | 2.6 |
| `GET /documents/{id}` | `version?` | `200 DocumentDetail` | 404 | 2.6 |
| `DELETE /documents/{id}` | — | `204` (soft delete + purge job; audited; admin+) | 403, 404 | 2.6 |
| `GET /documents/{id}/status` | — | `200 {status, stages[]}`; `/status/stream` = SSE | 404 | 2.7 |
| `GET /documents/{id}/file` | `rendition=original\|pdf` | `200 {url, expires_at}` | 404 | 3 |
| `GET /documents/{id}/pages/{n}` | `format=png\|meta` | `200 {url}` / `PageMeta` | 404 | 3 |
| `GET /documents/{id}/tables` | `page?, cursor, limit` | `200 Page[TableOut]` | 404 | 3 |
| `GET /documents/{id}/charts` | `page?, cursor, limit` | `200 Page[ChartOut]` | 404 | 3 |
| `POST /query` | `QueryIn` | `200 AnswerOut` or `text/event-stream` | 422, 429 | 5 |
| `POST /query/compare` | `CompareIn` (2..N documents) | `200 ComparisonOut` or SSE | 422, 429 | 5 |
| `POST /approvals/{id}` | `{decision: approve\|reject, comment?}` | `200 ApprovalOut` (resumes graph) | 403, 404, 409 | 5 |
| `GET /processing/history` | `status, from, to, cursor` | `200 Page[ProcessingJobOut]` | — | 2.7 |
| `GET /evaluations` | `dataset?, cursor` | `200 Page[EvalRunOut]` | — | 7 |
| `GET /evaluations/{run_id}` | — | `200 EvalRunDetail` | 404 | 7 |

## Core answer contract

```python
class Citation(BaseModel):
    citation_id: str
    document_id: UUID
    version_id: UUID
    document_title: str
    page: int                       # DOCX: rendered page, flagged approximate
    page_is_approximate: bool
    section: list[str]
    bbox: BBox | None               # normalised [x0, y0, x1, y1]
    quote: str                      # verbatim text from the chunk
    verification: Literal["verified", "partial", "unsupported"]

class Computation(BaseModel):
    operation: Literal["add", "subtract", "multiply", "divide", "pct_change", "cagr", "ratio"]
    operands: list[Operand]         # each operand: value, unit, scale, citation_id
    result: Decimal
    unit: str | None

class Claim(BaseModel):
    text: str
    citation_ids: list[str]
    computation: Computation | None

class AnswerOut(BaseModel):
    answer_markdown: str
    claims: list[Claim]
    citations: list[Citation]
    verification_summary: Literal["verified", "partially_verified", "insufficient_evidence"]
    limitations: list[str]
    route: list[str]                # agents actually executed
    usage: Usage                    # tokens, cost_usd, latency_ms
    trace_id: str
```

**Invariant:** every `citation_id` referenced in `claims` exists in `citations`, and every citation was
resolved by the server from a chunk that was in the model's context. The model never authors page
numbers.

## SSE event stream (`stream: true`)

| Event | Payload |
|---|---|
| `route` | `{intent, planned_agents[]}` |
| `agent_step` | `{node, status, started_at, ended_at}` |
| `token` | `{text}` (draft answer) |
| `citation` | `Citation` (unverified at first) |
| `verification` | `{claim_idx, status}` |
| `final` | `AnswerOut` |
| `error` | problem+json body |

Verification happens after drafting. The UI shows a "verifying…" badge until the `final` event arrives.
