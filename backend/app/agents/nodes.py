"""Agent nodes. Dependencies arrive via `config["configurable"]["deps"]` (an AgentDeps)."""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from app.agents import prompts
from app.agents.analysts import analyze_charts, analyze_tables, facts_as_dicts, format_facts
from app.agents.state import MAX_VERIFY_ATTEMPTS, GraphState, Intent, Plan
from app.agents.tools.calculator import CalculationError, Operand, Result, compute
from app.agents.verification import Claim, apply_judge, as_dict, check_claim, numbers_in
from app.database.models import ROLE_RANK, Document, Role, Sensitivity
from app.database.session import async_session_scope
from app.providers.llm import ChatModel, Message, ProviderError
from app.retrieval.retriever import (
    ContextItem,
    RetrievalConfig,
    Retriever,
    assemble_context,
    format_context,
    resolve_citation,
)
from app.retrieval.search import Filters, RetrievedChunk


@dataclass
class AgentDeps:
    org_id: uuid.UUID
    user_id: uuid.UUID
    role: Role
    retriever: Retriever
    chat: ChatModel | None
    top_k: int = 8
    context_tokens: int = 3500


def _deps(config: RunnableConfig) -> AgentDeps:
    # Live deps are looked up by thread id; they are never stored in checkpointed config.
    from app.agents.service import lookup_deps

    return lookup_deps(str(config["configurable"]["thread_id"]))


def _trace(node: str, started: float, **info: Any) -> dict[str, Any]:
    return {"node": node, "ms": round((time.perf_counter() - started) * 1000, 1), **info}


async def _llm_json(
    deps: AgentDeps, system: str, user: str, schema: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, int]]:
    assert deps.chat is not None  # noqa: S101
    chat = deps.chat
    res = await run_in_threadpool(
        lambda: chat.generate_json(
            messages=[Message("system", system), Message("user", user)], schema=schema
        )
    )
    return res.data, {
        "input_tokens": res.usage.input_tokens,
        "output_tokens": res.usage.output_tokens,
    }


# ---------------------------------------------------------------- supervisor ---------------
_CMP = re.compile(r"\b(compare|comparison|versus|vs\.?|difference between|both|across)\b", re.I)
_CHART = re.compile(r"\b(chart|graph|figure|plot|trend|visual)\b", re.I)
_CALC = re.compile(
    r"\b(how much (more|less)|difference|growth|grew|increase|decrease|change|percent|%|ratio|"
    r"total|sum|average|largest|smallest|highest|lowest|calculate|compute)\b",
    re.I,
)
_TABLE = re.compile(
    r"\b(table|revenue|expense|cost|income|profit|margin|amount|value|number|figure|budget|"
    r"usd|\$|eur|million|billion)\b",
    re.I,
)
_SUMMARY = re.compile(r"\b(summari[sz]e|summary|overview|tl;?dr|key points)\b", re.I)


_INTENTS = {"lookup", "table_numeric", "chart", "compare", "summarize", "out_of_scope"}


def classify_by_rules(question: str, n_docs: int) -> Plan:
    """Deterministic fallback router (used when no LLM is configured)."""
    is_cmp = bool(_CMP.search(question)) or n_docs > 1
    needs_calc = bool(_CALC.search(question))
    needs_tables = needs_calc or bool(_TABLE.search(question))
    needs_charts = bool(_CHART.search(question))
    intent: Intent = (
        "compare"
        if is_cmp
        else "summarize"
        if _SUMMARY.search(question)
        else "chart"
        if needs_charts and not needs_tables
        else "table_numeric"
        if needs_tables
        else "lookup"
    )
    return Plan(
        intent=intent,
        needs_tables=needs_tables,
        needs_charts=needs_charts or intent == "summarize",
        needs_calculation=needs_calc,
        is_comparison=is_cmp,
        sub_queries=[],
        classifier="rules",
        reason="keyword rules",
    )


async def supervisor(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    t = time.perf_counter()
    deps = _deps(config)
    n_docs = len(state.get("document_ids") or [])
    usage: list[dict[str, int]] = []
    if deps.chat is None:
        plan = classify_by_rules(state["question"], n_docs)
    else:
        try:
            data, u = await _llm_json(
                deps,
                prompts.SUPERVISOR_SYSTEM,
                f"Documents selected: {n_docs}\nQuestion: {state['question']}",
                prompts.SUPERVISOR_SCHEMA,
            )
            usage.append(u)
            if data.get("intent") not in _INTENTS:
                raise ProviderError(f"invalid intent from model: {data.get('intent')!r}")
            plan = Plan(
                intent=data["intent"],
                needs_tables=bool(data.get("needs_tables")),
                needs_charts=bool(data.get("needs_charts")),
                needs_calculation=bool(data.get("needs_calculation")),
                is_comparison=bool(data.get("is_comparison")),
                sub_queries=[q for q in data.get("sub_queries", []) if isinstance(q, str)][:3],
                classifier="llm",
                reason=str(data.get("reason", "")),
            )
        except ProviderError:
            plan = classify_by_rules(state["question"], n_docs)
    if state.get("mode") == "compare":
        plan["intent"], plan["is_comparison"] = "compare", True
    return {
        "plan": plan,
        "attempts": 0,
        "usage": usage,
        "trace": [
            _trace("supervisor", t, intent=plan["intent"], classifier=plan.get("classifier"))
        ],
    }


# ---------------------------------------------------------------- approval gate ------------
async def approval_gate(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    """Human-in-the-loop: non-admins need approval to query confidential documents."""
    t = time.perf_counter()
    deps = _deps(config)
    ids = [uuid.UUID(d) for d in state.get("document_ids") or []]
    requester = Role(state.get("requester_role", Role.VIEWER.value))  # missing => least privilege
    if not ids or ROLE_RANK[requester] >= ROLE_RANK[Role.ADMIN]:
        return {"approval": None, "trace": [_trace("approval_gate", t, required=False)]}
    async with async_session_scope(deps.org_id) as s:
        confidential = (
            await s.execute(
                select(Document.id, Document.title).where(
                    Document.id.in_(ids), Document.sensitivity == Sensitivity.CONFIDENTIAL
                )
            )
        ).all()
    if not confidential:
        return {"approval": None, "trace": [_trace("approval_gate", t, required=False)]}
    decision = interrupt(
        {
            "action": "query_confidential_documents",
            "documents": [{"id": str(d), "title": title} for d, title in confidential],
            "question": state["question"],
        }
    )
    approved = bool(decision.get("approved")) if isinstance(decision, dict) else False
    return {
        "approval": {
            "approved": approved,
            "decided_by": decision.get("decided_by") if isinstance(decision, dict) else None,
        },
        "trace": [_trace("approval_gate", t, required=True, approved=approved)],
    }


# ---------------------------------------------------------------- document classification --
async def classify_documents(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    """No explicit scope: pick the documents whose evidence ranks highest (and any named in the question)."""
    t = time.perf_counter()
    deps = _deps(config)
    async with async_session_scope(deps.org_id) as s:
        res = await deps.retriever.search(
            s, state["question"], Filters(), RetrievalConfig(top_k=12, rerank=False)
        )
        titles = (
            await s.execute(
                select(Document.id, Document.title).where(Document.deleted_at.is_(None))
            )
        ).all()
    q = state["question"].lower()
    named = [
        str(d)
        for d, title in titles
        if title
        and any(
            w in q
            for w in [
                title.lower(),
                title.lower().split(" annual")[0],
                title.lower().split(" fy")[0],
            ]
            if len(w) > 4
        )
    ]
    ranked: list[str] = []
    for c in res.chunks:
        if str(c.document_id) not in ranked:
            ranked.append(str(c.document_id))
    limit = 4 if state.get("plan", {}).get("is_comparison") else 2
    scope = list(dict.fromkeys(named + ranked))[:limit]
    return {
        "doc_scope": scope,
        "trace": [_trace("classify_documents", t, selected=len(scope), named=len(named))],
    }


# ---------------------------------------------------------------- retrieval ----------------
def _ser(item: ContextItem) -> dict[str, Any]:
    c = item.chunk
    return {
        "source_id": item.source_id,
        "text": item.text,
        "chunk_id": str(c.chunk_id),
        "document_id": str(c.document_id),
        "document_title": c.document_title,
        "version_id": str(c.version_id),
        "chunk_type": c.chunk_type,
        "page_start": c.page_start,
        "page_end": c.page_end,
        "bboxes": c.bboxes,
        "section_path": c.section_path,
        "metadata": c.metadata,
        "scores": c.scores,
        "parent_chunk_id": str(c.parent_chunk_id) if c.parent_chunk_id else None,
    }


def deser(e: dict[str, Any]) -> ContextItem:
    chunk = RetrievedChunk(
        chunk_id=uuid.UUID(e["chunk_id"]),
        document_id=uuid.UUID(e["document_id"]),
        document_title=e["document_title"],
        version_id=uuid.UUID(e["version_id"]),
        chunk_type=e["chunk_type"],
        content=e["text"],
        page_start=e["page_start"],
        page_end=e["page_end"],
        bboxes=e["bboxes"],
        section_path=e["section_path"],
        parent_chunk_id=uuid.UUID(e["parent_chunk_id"]) if e.get("parent_chunk_id") else None,
        metadata=e["metadata"],
        scores=e.get("scores", {}),
    )
    return ContextItem(e["source_id"], chunk, e["text"])


async def retrieve(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    t = time.perf_counter()
    deps = _deps(config)
    scope = state.get("document_ids") or state.get("doc_scope") or None
    plan = state.get("plan", {})
    query = state["question"]
    extra = list(plan.get("sub_queries", []))
    if refine := state.get("refine_query"):
        extra.append(refine)  # verification asked for more evidence
    async with async_session_scope(deps.org_id) as s:
        res = await deps.retriever.search(
            s,
            query,
            Filters(document_ids=[uuid.UUID(d) for d in scope] if scope else None),
            RetrievalConfig(top_k=deps.top_k + (2 if plan.get("is_comparison") else 0)),
            extra_queries=extra,
        )
    items = assemble_context(res.chunks, token_budget=deps.context_tokens)
    return {
        "evidence": [_ser(i) for i in items],
        "retrieval_queries": res.queries,
        "trace": [
            _trace(
                "retrieve", t, sources=len(items), queries=len(res.queries), timings=res.timings_ms
            )
        ],
    }


async def table_analysis(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    t = time.perf_counter()
    deps = _deps(config)
    async with async_session_scope(deps.org_id) as s:
        facts = await analyze_tables(s, state["question"], state.get("evidence", []))
    return {
        "table_facts": facts_as_dicts(facts),
        "trace": [_trace("table_analysis", t, facts=len(facts))],
    }


async def chart_analysis(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    t = time.perf_counter()
    deps = _deps(config)
    async with async_session_scope(deps.org_id) as s:
        facts = await analyze_charts(s, state["question"], state.get("evidence", []))
    est = sum(1 for f in facts if f.estimated)
    return {
        "chart_facts": facts_as_dicts(facts),
        "trace": [_trace("chart_analysis", t, facts=len(facts), estimated=est)],
    }


# ---------------------------------------------------------------- reasoning ----------------
def run_computations(
    specs: list[dict[str, Any]], facts: dict[str, dict[str, Any]], sources: dict[str, str]
) -> tuple[dict[str, Result], list[str]]:
    """Execute declared computations. Operand values come from FACTS (authoritative) or must
    appear verbatim in the cited SOURCE; anything else is rejected, never trusted."""
    results: dict[str, Result] = {}
    errors: list[str] = []
    for spec in specs:
        cid = str(spec.get("id") or f"calc{len(results) + 1}")
        operands: list[Operand] = []
        try:
            for o in spec.get("operands", []):
                fid = o.get("fact_id")
                if fid:
                    f = facts.get(fid)
                    if f is None:
                        raise CalculationError(f"unknown fact {fid}")
                    operands.append(
                        Operand.of(
                            f["value"],
                            unit=f["unit"],
                            scale=f["scale"] or 1,
                            citation_id=f["source_id"],
                            label=o.get("label") or f["label"],
                            estimated=f["estimated"],
                        )
                    )
                    continue
                sid, value = o.get("source_id"), o.get("value")
                if not sid or sid not in sources or value is None:
                    raise CalculationError(
                        f"operand needs a fact_id or a valid source_id+value ({o})"
                    )
                v = Operand.of(value).value
                if not any(abs(v) == abs(n) for n in numbers_in(sources[sid])):
                    raise CalculationError(f"value {value} not found in source {sid}")
                operands.append(
                    Operand.of(
                        value,
                        unit=o.get("unit"),
                        scale=int(o.get("scale") or 1),
                        citation_id=sid,
                        label=o.get("label"),
                    )
                )
            results[cid] = compute(spec["operation"], *operands, periods=spec.get("periods"))
        except (CalculationError, KeyError, ValueError) as exc:
            errors.append(f"{cid}: {exc}")
    return results, errors


def _fill(text: str, results: dict[str, Result]) -> str:
    def sub(m: re.Match[str]) -> str:
        r = results.get(m.group(1).strip())
        return r.display() if r else "[calculation unavailable]"

    return re.sub(r"\{\{\s*([\w\-]+)\s*\}\}", sub, text)


def _result_dict(r: Result) -> dict[str, Any]:
    return {
        "operation": r.operation,
        "value": str(r.value),
        "unit": r.unit,
        "scale": r.scale,
        "display": r.display(),
        "estimated": r.estimated,
        "expression": r.expression,
        "operands": [
            {
                "value": str(o.value),
                "unit": o.unit,
                "scale": o.scale,
                "citation_id": o.citation_id,
                "label": o.label,
                "estimated": o.estimated,
            }
            for o in r.operands
        ],
    }


async def reasoning(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    t = time.perf_counter()
    deps = _deps(config)
    evidence = state.get("evidence", [])
    if deps.chat is None:
        return {"draft": None, "trace": [_trace("reasoning", t, skipped="no_language_model")]}
    if not evidence:
        return {
            "draft": {
                "answer_markdown": "",
                "claims": [],
                "computations": [],
                "insufficient_evidence": True,
                "missing_information": "No relevant passages were found in the selected documents.",
            },
            "trace": [_trace("reasoning", t, skipped="no_evidence")],
        }

    facts = (state.get("table_facts") or []) + (state.get("chart_facts") or [])
    history = "\n".join(
        f"{h['role']}: {h['content'][:500]}" for h in (state.get("history") or [])[-6:]
    )
    user = (
        f"QUESTION: {state['question']}\n\n"
        + (f"CONVERSATION SO FAR:\n{history}\n\n" if history else "")
        + f"SOURCES:\n{format_context([deser(e) for e in evidence])}\n\n"
        + (
            f"FACTS (exact values extracted from tables/charts):\n{format_facts(facts)}\n"
            if facts
            else ""
        )
    )
    try:
        data, usage = await _llm_json(
            deps, prompts.REASONING_SYSTEM, user, prompts.REASONING_SCHEMA
        )
    except ProviderError as exc:
        return {
            "draft": None,
            "computation_errors": [f"language model error: {exc}"],
            "trace": [_trace("reasoning", t, error=str(exc)[:200])],
        }

    sources = {e["source_id"]: e["text"] for e in evidence}
    results, errors = run_computations(
        data.get("computations", []), {f["fact_id"]: f for f in facts}, sources
    )
    draft: dict[str, Any] = {
        "answer_markdown": _fill(str(data.get("answer_markdown", "")), results),
        "claims": [
            {
                "text": _fill(str(c.get("text", "")), results),
                "citation_ids": list(c.get("citation_ids", [])),
                "computation_ids": list(c.get("computation_ids", [])),
            }
            for c in data.get("claims", [])
        ],
        "insufficient_evidence": bool(data.get("insufficient_evidence")),
        "missing_information": str(data.get("missing_information", "")),
    }
    return {
        "draft": draft,
        "computations": {k: _result_dict(v) for k, v in results.items()},
        "computation_errors": errors,
        "usage": [usage],
        "trace": [
            _trace(
                "reasoning",
                t,
                claims=len(draft["claims"]),
                computations=len(results),
                errors=len(errors),
            )
        ],
    }


# ---------------------------------------------------------------- verification -------------
def _rehydrate(d: dict[str, Any]) -> Result:
    ops = tuple(
        Operand(
            value=Decimal(o["value"]),
            unit=o["unit"],
            scale=o["scale"],
            citation_id=o["citation_id"],
            label=o["label"],
            estimated=o["estimated"],
        )
        for o in d["operands"]
    )
    return Result(
        d["operation"],
        Decimal(d["value"]),
        d["unit"],
        d["scale"],
        ops,
        d["estimated"],
        d["expression"],
    )


async def verify(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    t = time.perf_counter()
    deps = _deps(config)
    draft = state.get("draft")
    attempts = state.get("attempts", 0) + 1
    if not draft:
        return {"checks": [], "attempts": attempts, "trace": [_trace("verify", t, skipped=True)]}
    sources = {e["source_id"]: e["text"] for e in state.get("evidence", [])}
    comps = {k: _rehydrate(v) for k, v in (state.get("computations") or {}).items()}
    checks = [
        check_claim(
            Claim(c["text"], c["citation_ids"], c.get("computation_ids", [])), sources, comps
        )
        for c in draft["claims"]
    ]
    usage: list[dict[str, int]] = []
    if deps.chat is not None and checks:
        listing = "\n\n".join(
            f"CLAIM {i}: {c.claim.text}\nSOURCE TEXT:\n"
            + "\n".join(sources.get(s, "")[:1500] for s in c.valid_citations)
            for i, c in enumerate(checks)
        )
        try:
            data, u = await _llm_json(deps, prompts.JUDGE_SYSTEM, listing, prompts.JUDGE_SCHEMA)
            usage.append(u)
            for v in data.get("verdicts", []):
                idx = v.get("index")
                if isinstance(idx, int) and 0 <= idx < len(checks):
                    apply_judge(checks[idx], str(v.get("verdict")))
        except ProviderError:
            pass  # deterministic checks still stand
    unsupported = [c for c in checks if c.status == "unsupported"]
    refine = None
    if (unsupported or draft.get("insufficient_evidence")) and attempts < MAX_VERIFY_ATTEMPTS:
        refine = (
            draft.get("missing_information")
            or " ".join(c.claim.text for c in unsupported)[:300]
            or None
        )
    return {
        "checks": [as_dict(c) for c in checks],
        "attempts": attempts,
        "refine_query": refine,
        "usage": usage,
        "trace": [
            _trace(
                "verify", t, claims=len(checks), unsupported=len(unsupported), retry=bool(refine)
            )
        ],
    }


# ---------------------------------------------------------------- response -----------------
NO_LLM_MESSAGE = (
    "Answer generation is unavailable because no language model is configured "
    "(set GEMINI_API_KEY). The most relevant evidence from your documents is listed below."
)


async def respond(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    t = time.perf_counter()
    plan = state.get("plan", {})
    evidence = {e["source_id"]: deser(e) for e in state.get("evidence", [])}
    draft = state.get("draft")
    checks = state.get("checks", [])
    limitations: list[str] = []

    if plan.get("intent") == "out_of_scope":
        answer, summary = (
            "This question doesn't appear to be about your documents, so I can't answer it from them.",
            "insufficient_evidence",
        )
        cited: list[str] = []
    elif (approval := state.get("approval")) is not None and not approval.get("approved"):
        answer, summary, cited = (
            "Access to the confidential documents was not approved, so no answer was generated.",
            "insufficient_evidence",
            [],
        )
        evidence = {}
    elif draft is None:
        answer, summary = NO_LLM_MESSAGE, "insufficient_evidence"
        cited = list(evidence)[:5]
        limitations += state.get("computation_errors", [])
    else:
        answer = draft["answer_markdown"] or "I couldn't find enough evidence to answer this."
        cited = list(dict.fromkeys(c for ch in checks for c in ch["citation_ids"]))
        summary = summarise_dicts(checks) if checks else "insufficient_evidence"
        if draft.get("insufficient_evidence"):
            limitations.append(
                draft.get("missing_information") or "The documents don't fully cover this question."
            )
        for ch in checks:
            if ch["status"] != "verified":
                limitations.append(
                    f"Not fully verified: “{ch['text'][:160]}” ({'; '.join(ch['notes']) or ch['status']})"
                )
        limitations += [f"Calculation rejected — {e}" for e in state.get("computation_errors", [])]
        if any(f.get("estimated") for f in state.get("chart_facts") or []):
            limitations.append("Some values come from chart readings and are approximate.")

    status_by_source: dict[str, str] = {}
    for ch in checks:
        for sid in ch["citation_ids"]:
            prev = status_by_source.get(sid)
            if prev is None or ["unsupported", "partial", "verified"].index(ch["status"]) > [
                "unsupported",
                "partial",
                "verified",
            ].index(prev):
                status_by_source[sid] = ch["status"]
    citations = []
    for sid in cited:
        if sid in evidence:
            cit = resolve_citation(evidence[sid])
            cit["verification"] = status_by_source.get(sid, "unverified")
            citations.append(cit)

    final = {
        "answer_markdown": answer,
        "claims": checks,
        "citations": citations,
        "computations": state.get("computations") or {},
        "verification_summary": summary,
        "limitations": list(dict.fromkeys(limitations)),
        "route": [tr["node"] for tr in state.get("trace", [])] + ["respond"],
        "plan": plan,
        "evidence_count": len(state.get("evidence", [])),
    }
    return {
        "final": final,
        "trace": [_trace("respond", t, citations=len(citations), summary=summary)],
    }


def summarise_dicts(checks: list[dict[str, Any]]) -> str:
    """Same rule as verification.summarise, over serialised checks."""
    statuses = [c["status"] for c in checks]
    if not statuses or all(st == "unsupported" for st in statuses):
        return "insufficient_evidence"
    if all(st == "verified" for st in statuses):
        return "verified"
    return "partially_verified"
