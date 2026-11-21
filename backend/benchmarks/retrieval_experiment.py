"""Retrieval experiments: chunking strategy x retrieval mode (x reranking when available).

Dataset: N synthetic financial reports (benchmarks/synthetic.py) with exact ground truth.
Queries are generated from the ground truth and asked corpus-wide (no document filter), so the
retriever must also pick the right company. A retrieved chunk is RELEVANT iff it belongs to the
right document, covers the expected page, and contains the exact answer string. Labels are
programmatic (derived from known data) — NOT human-verified; see evaluation/README.md.

Usage:  uv run python -m benchmarks.retrieval_experiment [--docs 6] [--out ../evaluation/results]
Requires the docker-compose Postgres (dev DB) and the Docling models.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import re
import statistics
import subprocess
import tempfile
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select, text

from app.chunking.indexer import index_version
from app.chunking.strategies import STRATEGIES
from app.database.models import Chunk, Document, DocumentVersion, Organization, VersionStatus
from app.database.session import async_session_scope, dispose_engines, sync_session_scope
from app.embeddings.providers import get_embedder
from app.evaluation.metrics.retrieval import hit_at_k, mrr, ndcg_at_k, precision_at_k, recall_at_k
from app.ingestion.pipeline import run_pipeline
from app.ingestion.validation import spool_to_disk, validate_file
from app.retrieval.rerank import CrossEncoderReranker, reranker_available
from app.retrieval.retriever import RetrievalConfig, Retriever
from app.retrieval.search import Filters
from app.services.documents import storage_key_for
from app.storage import get_storage
from benchmarks.synthetic import SEGMENTS, ReportSpec, fmt_money, write_report

COMPANIES = [
    ("Northwind Analytics", 7),
    ("Blue Harbor Systems", 11),
    ("Crescent Robotics", 23),
    ("Juniper Bioworks", 31),
    ("Atlas Freight Group", 43),
    ("Meridian Health Data", 59),
    ("Orchid Semiconductors", 71),
    ("Pioneer Grid Energy", 83),
]


@dataclass
class Query:
    qid: str
    text: str
    doc_key: str
    page: int
    answer: str  # exact string that a relevant chunk must contain
    kind: str


@dataclass
class RunResult:
    config: dict[str, Any]
    per_query: list[dict[str, Any]] = field(default_factory=list)

    def summary(self) -> dict[str, float]:
        keys = ["recall@5", "precision@5", "hit@1", "mrr@10", "ndcg@10", "latency_ms"]
        return {k: round(statistics.fmean(q[k] for q in self.per_query), 4) for k in keys}


def build_queries(company: str, spec: ReportSpec) -> list[Query]:
    y0, y1 = spec.years
    cat, val = spec.largest_expense(y1)
    seg = SEGMENTS[spec.seed % len(SEGMENTS)]
    return [
        Query(
            f"{company}:total",
            f"What was {company}'s total revenue in FY{y1}?",
            company,
            1,
            fmt_money(spec.total_revenue(y1)),
            "table/text",
        ),
        Query(
            f"{company}:largest_expense",
            f"Which operating expense category was largest for {company} in FY{y1}?",
            company,
            2,
            fmt_money(val),
            "table",
        ),
        Query(
            f"{company}:segment",
            f"How much revenue did {company}'s {seg} segment generate in FY{y0}?",
            company,
            1,
            fmt_money(spec.revenue[seg][y0]),
            "table-cell",
        ),
        Query(
            f"{company}:other_income",
            f"What was {company}'s other income (loss) in FY{y1}?",
            company,
            2,
            fmt_money(spec.other_income[y1]),
            "table-negative",
        ),
        Query(
            f"{company}:quarterly",
            f"Show {company}'s quarterly revenue trend for FY{y1}.",
            company,
            3,
            "Quarterly revenue",
            "chart",
        ),
    ]


def ingest(org_id: uuid.UUID, pdf: Path, title: str) -> uuid.UUID:
    """Store + register + run the production pipeline synchronously (as a worker would)."""
    with pdf.open("rb") as fh:
        path, sha, size = spool_to_disk(fh, 50 * 1024 * 1024)
    v = validate_file(path, sha256=sha, size_bytes=size, original_filename=pdf.name, max_pages=2000)
    doc_id, ver_id = uuid.uuid4(), uuid.uuid4()
    key = storage_key_for(org_id, doc_id, ver_id)
    get_storage().put_file(key, path, v.mime_type)
    path.unlink(missing_ok=True)
    with sync_session_scope(org_id) as s:
        s.add(Document(id=doc_id, org_id=org_id, title=title, tags=["benchmark"]))
        s.flush()
        s.add(
            DocumentVersion(
                id=ver_id,
                document_id=doc_id,
                org_id=org_id,
                version_no=1,
                sha256=sha,
                mime_type=v.mime_type,
                size_bytes=size,
                storage_key=key,
                original_filename=pdf.name,
                page_count=v.page_count,
                metadata_={"kind": v.kind.value},
                status=VersionStatus.QUEUED,
            )
        )
        s.flush()
        s.get(Document, doc_id).current_version_id = ver_id  # type: ignore[union-attr]
    status = run_pipeline(ver_id, org_id)
    if status is not VersionStatus.READY:
        raise RuntimeError(f"pipeline failed for {pdf.name}: {status}")
    return ver_id


class BM25:
    """Okapi BM25 (k1=1.5, b=0.75) over chunk texts — a true BM25 baseline to compare with
    PostgreSQL ts_rank_cd (which is not BM25)."""

    def __init__(self, docs: dict[uuid.UUID, str], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.tf = {d: Counter(_tok(t)) for d, t in docs.items()}
        self.len = {d: sum(c.values()) for d, c in self.tf.items()}
        self.avg = statistics.fmean(self.len.values()) if self.len else 0
        df: Counter[str] = Counter()
        for c in self.tf.values():
            df.update(c.keys())
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def search(self, query: str, k: int) -> list[uuid.UUID]:
        q = _tok(query)
        scores = {}
        for d, tf in self.tf.items():
            s = 0.0
            for t in q:
                if t in tf:
                    f = tf[t]
                    s += (
                        self.idf[t]
                        * f
                        * (self.k1 + 1)
                        / (f + self.k1 * (1 - self.b + self.b * self.len[d] / self.avg))
                    )
            if s > 0:
                scores[d] = s
        return [d for d, _ in sorted(scores.items(), key=lambda kv: -kv[1])[:k]]


_STOP = set(
    [
        "the",
        "a",
        "an",
        "of",
        "in",
        "for",
        "to",
        "and",
        "was",
        "what",
        "which",
        "how",
        "much",
        "did",
        "show",
        "is",
        "its",
    ]
)


def _tok(s: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", s.lower()) if w not in _STOP]


def _relevant(
    content: str, page_start: int | None, page_end: int | None, doc_ok: bool, q: Query
) -> bool:
    if not doc_ok or q.answer not in content:
        return False
    if page_start is None:
        return True
    return page_start <= q.page <= (page_end or page_start)


async def evaluate(
    org_id: uuid.UUID,
    versions: dict[str, uuid.UUID],
    queries: list[Query],
    strategy: str,
    mode: str,
    rerank: bool,
    retriever: Retriever,
) -> RunResult:
    res = RunResult({"strategy": strategy, "mode": mode, "rerank": rerank})
    async with async_session_scope(org_id) as s:
        rows = (
            await s.execute(
                select(
                    Chunk.id,
                    Chunk.document_version_id,
                    Chunk.content,
                    Chunk.page_start,
                    Chunk.page_end,
                    Chunk.parent_chunk_id,
                ).where(Chunk.strategy == strategy, Chunk.is_retrievable.is_(True))
            )
        ).all()
        parents = dict(
            (
                await s.execute(
                    select(Chunk.id, Chunk.content).where(
                        Chunk.strategy == strategy, Chunk.is_retrievable.is_(False)
                    )
                )
            ).all()
        )
    info = {r[0]: r for r in rows}
    bm25 = BM25({r[0]: r[2] for r in rows}) if mode == "bm25" else None
    ver_to_doc = {v: k for k, v in versions.items()}

    for q in queries:
        t = time.perf_counter()
        if bm25 is not None:
            ranked = bm25.search(q.text, 10)
        else:
            async with async_session_scope(org_id) as s:
                out = await retriever.search(
                    s,
                    q.text,
                    Filters(strategy=strategy),
                    RetrievalConfig(mode=mode, rerank=rerank, top_k=10, rerank_candidates=30),
                )
            ranked = [c.chunk_id for c in out.chunks]
        latency = (time.perf_counter() - t) * 1000

        def rel(cid: uuid.UUID, q: Query = q) -> bool:
            r = info.get(cid)
            if r is None:
                return False
            # a child counts if its parent (what the LLM will read) contains the answer
            content = r[2] + "\n" + (parents.get(r[5], "") if r[5] else "")
            return _relevant(content, r[3], r[4], ver_to_doc.get(r[1]) == q.doc_key, q)

        relevant_all = {cid for cid in info if rel(cid)}
        res.per_query.append(
            {
                "qid": q.qid,
                "kind": q.kind,
                "n_relevant": len(relevant_all),
                "recall@5": recall_at_k(ranked, relevant_all, 5) if relevant_all else 0.0,
                "precision@5": precision_at_k(ranked, relevant_all, 5),
                "hit@1": hit_at_k(ranked, relevant_all, 1),
                "mrr@10": mrr(ranked, relevant_all, 10),
                "ndcg@10": ndcg_at_k(ranked, relevant_all, 10),
                "latency_ms": round(latency, 2),
            }
        )
    return res


def _git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=Path(__file__).parent,
        )
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=True,
            cwd=Path(__file__).parent,
        ).stdout.strip()
        return out.stdout.strip() + ("-dirty" if dirty else "")
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "uncommitted"


async def main(n_docs: int, out_dir: Path) -> None:
    started = datetime.now(UTC)
    with sync_session_scope() as s:
        org = Organization(name="Benchmark", slug=f"benchmark-{uuid.uuid4().hex[:8]}")
        s.add(org)
    org_id = org.id

    tmp = Path(tempfile.mkdtemp(prefix="documind-bench-"))
    versions: dict[str, uuid.UUID] = {}
    queries: list[Query] = []
    truth_hash = hashlib.sha256()
    t0 = time.perf_counter()
    for company, seed in COMPANIES[:n_docs]:
        pdf, truth = write_report(company, seed, tmp)
        truth_hash.update(json.dumps(truth, sort_keys=True).encode())
        versions[company] = ingest(org_id, pdf, f"{company} Annual Report FY2024")
        queries += build_queries(company, ReportSpec.generate(company, seed))
    ingest_s = time.perf_counter() - t0

    embedder = get_embedder()
    t0 = time.perf_counter()
    for strategy in STRATEGIES:
        if strategy == "parent_child":
            continue  # indexed by the production pipeline
        for ver in versions.values():
            with sync_session_scope(org_id) as s:
                index_version(
                    s, org_id=org_id, version_id=ver, embedder=embedder, strategy=strategy
                )
    index_s = time.perf_counter() - t0

    has_reranker = reranker_available()
    retriever = Retriever(embedder, CrossEncoderReranker() if has_reranker else None)
    results: list[RunResult] = []
    for strategy in STRATEGIES:
        for mode in ("dense", "lexical", "bm25", "hybrid"):
            results.append(
                await evaluate(org_id, versions, queries, strategy, mode, False, retriever)
            )
        if has_reranker:
            results.append(
                await evaluate(org_id, versions, queries, strategy, "hybrid", True, retriever)
            )

    async with async_session_scope(org_id) as s:
        chunk_counts = dict(
            (await s.execute(text("SELECT strategy, count(*) FROM chunks GROUP BY strategy"))).all()
        )
    await dispose_engines()

    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = started.strftime("%Y%m%dT%H%M%SZ")
    report = {
        "experiment": "retrieval_strategy_grid",
        "started_at": started.isoformat(),
        "git": _git_sha(),
        "dataset": {
            "name": "synthetic-financial-reports",
            "documents": n_docs,
            "queries": len(queries),
            "ground_truth_sha256": truth_hash.hexdigest(),
            "labels": "programmatic (exact answer string + doc + page)",
        },
        "embedding_model": f"{embedder.provider}:{embedder.model}",
        "reranker": "cross-encoder/ms-marco-MiniLM-L6-v2"
        if has_reranker
        else "unavailable (weights not downloaded)",
        "timings_s": {
            "ingest_parse_index_parent_child": round(ingest_s, 1),
            "index_other_strategies": round(index_s, 1),
        },
        "chunk_counts": chunk_counts,
        "results": [
            {"config": r.config, "summary": r.summary(), "per_query": r.per_query} for r in results
        ],
    }
    (out_dir / f"retrieval_{stamp}.json").write_text(json.dumps(report, indent=2, default=str))
    (out_dir / f"retrieval_{stamp}.md").write_text(render_markdown(report))
    print((out_dir / f"retrieval_{stamp}.md").read_text())


def render_markdown(r: dict[str, Any]) -> str:
    d = r["dataset"]
    lines = [
        f"# Retrieval experiment — {r['started_at'][:19]}Z",
        "",
        f"- **Dataset:** {d['name']}: {d['documents']} documents, {d['queries']} queries, labels: {d['labels']}",
        f"- **Ground-truth hash:** `{d['ground_truth_sha256'][:16]}…` · **Code:** `{r['git']}`",
        f"- **Embeddings:** `{r['embedding_model']}` · **Reranker:** {r['reranker']}",
        f"- **Chunks per strategy:** {r['chunk_counts']}",
        f"- **Timings:** {r['timings_s']}",
        "",
        "Queries are asked corpus-wide (all documents). Small synthetic dataset: treat differences of a few",
        "points as noise, and do not extrapolate to real-world documents without the human-verified set.",
        "",
        "| Strategy | Mode | Rerank | Recall@5 | Precision@5 | Hit@1 | MRR@10 | NDCG@10 | Latency ms |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for res in sorted(r["results"], key=lambda x: -x["summary"]["ndcg@10"]):
        c, s = res["config"], res["summary"]
        lines.append(
            f"| {c['strategy']} | {c['mode']} | {'yes' if c['rerank'] else 'no'} | {s['recall@5']:.3f} | "
            f"{s['precision@5']:.3f} | {s['hit@1']:.3f} | {s['mrr@10']:.3f} | {s['ndcg@10']:.3f} | {s['latency_ms']:.1f} |"
        )
    lines += ["", "## By query kind (best config per strategy is in the JSON)", ""]
    best = max(r["results"], key=lambda x: x["summary"]["ndcg@10"])
    kinds: dict[str, list[float]] = {}
    for q in best["per_query"]:
        kinds.setdefault(q["kind"], []).append(q["hit@1"])
    lines.append(
        f"Best config `{best['config']}` Hit@1 by kind: "
        + ", ".join(f"{k}={statistics.fmean(v):.2f}" for k, v in sorted(kinds.items()))
    )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs", type=int, default=6)
    ap.add_argument(
        "--out", type=Path, default=Path(__file__).resolve().parents[2] / "evaluation" / "results"
    )
    args = ap.parse_args()
    asyncio.run(main(args.docs, args.out))
