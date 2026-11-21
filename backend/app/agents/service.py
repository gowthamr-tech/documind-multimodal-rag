"""Run the agent graph for a request: persistence, checkpointing, approvals, streaming events."""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

from langchain_core.runnables import RunnableConfig
from langgraph.types import Command
from sqlalchemy import select

from app.agents.graph import build_graph
from app.agents.nodes import AgentDeps
from app.agents.state import RECURSION_LIMIT
from app.core.config import Settings, get_settings
from app.core.errors import AppError, ConflictError, NotFoundError
from app.core.logging import get_logger
from app.core.security import Principal
from app.database.models import (
    ROLE_RANK,
    Approval,
    ApprovalStatus,
    Citation,
    Conversation,
    Message,
    QueryRun,
    Role,
)
from app.database.session import async_session_scope
from app.providers.llm import get_chat_model
from app.retrieval.service import get_retriever
from app.services import audit

logger = get_logger("app.agents")

# Live dependencies per graph thread. Kept out of the checkpointed config on purpose: they
# hold connections/models and must never be serialised.
_DEPS: dict[str, AgentDeps] = {}
_saver: Any = None
_pool: Any = None


def lookup_deps(thread_id: str) -> AgentDeps:
    return _DEPS[thread_id]


async def get_checkpointer(settings: Settings | None = None) -> Any:
    """Process-wide AsyncPostgresSaver on a psycopg pool (app role; tables created by setup)."""
    global _saver, _pool
    if _saver is None:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from psycopg.rows import dict_row
        from psycopg_pool import AsyncConnectionPool

        url = (settings or get_settings()).database_url.replace(
            "postgresql+psycopg://", "postgresql://"
        )
        _pool = AsyncConnectionPool(
            url,
            max_size=5,
            open=False,
            kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        )
        await _pool.open()
        _saver = AsyncPostgresSaver(_pool)
    return _saver


async def close_checkpointer() -> None:
    global _saver, _pool
    if _pool is not None:
        await _pool.close()
    _saver, _pool = None, None


async def setup_checkpointer(owner_url: str) -> None:
    """Create LangGraph's checkpoint tables as the owner role, then grant the app role."""
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg import AsyncConnection

    from app.database.rls import grant_app_role

    url = owner_url.replace("postgresql+psycopg://", "postgresql://")
    async with await AsyncConnection.connect(url, autocommit=True) as conn:
        await AsyncPostgresSaver(conn).setup()  # type: ignore[arg-type]
        await conn.execute(grant_app_role())


def _deps_for(principal: Principal, settings: Settings) -> AgentDeps:
    return AgentDeps(
        org_id=principal.org_id,
        user_id=principal.user_id,
        role=principal.role,
        retriever=get_retriever(),
        chat=get_chat_model(settings),
    )


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def _cost(settings: Settings, prompt: int, completion: int) -> Decimal | None:
    pin, pout = settings.llm_input_cost_per_mtok, settings.llm_output_cost_per_mtok
    if pin is None or pout is None:
        return None  # prices are configuration; never guessed
    return (
        Decimal(prompt) * Decimal(str(pin)) + Decimal(completion) * Decimal(str(pout))
    ) / Decimal(1_000_000)


class QueryRejectedError(AppError):
    status_code = 422
    problem_type = "invalid-query"
    title = "Invalid Query"


async def _start_run(
    principal: Principal,
    question: str,
    document_ids: list[uuid.UUID],
    mode: str,
    conversation_id: uuid.UUID | None,
) -> tuple[uuid.UUID, uuid.UUID, list[dict[str, str]]]:
    async with async_session_scope(principal.org_id) as s:
        if conversation_id is not None:
            conv = await s.get(Conversation, conversation_id)
            if conv is None or conv.user_id != principal.user_id:
                raise NotFoundError("Conversation not found.")
            if not document_ids:
                document_ids = list(conv.document_scope)
        else:
            conv = Conversation(
                org_id=principal.org_id,
                user_id=principal.user_id,
                title=question[:120],
                document_scope=document_ids,
            )
            s.add(conv)
            await s.flush()
        prior = (
            await s.execute(
                select(Message.role, Message.content)
                .where(Message.conversation_id == conv.id)
                .order_by(Message.created_at)
            )
        ).all()
        msg = Message(
            org_id=principal.org_id, conversation_id=conv.id, role="user", content=question
        )
        s.add(msg)
        await s.flush()
        run = QueryRun(
            org_id=principal.org_id,
            conversation_id=conv.id,
            message_id=msg.id,
            user_id=principal.user_id,
            question=question,
            mode=mode,
            status="running",
            route=[],
            trace=[],
        )
        s.add(run)
        conv.updated_at = datetime.now(UTC)
        await s.commit()
        return conv.id, run.id, [{"role": r, "content": c} for r, c in prior]


async def _persist(
    principal: Principal,
    run_id: uuid.UUID,
    state: dict[str, Any],
    started: float,
    settings: Settings,
    status: str,
    approval_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    final = state.get("final") or {}
    usage = state.get("usage", [])
    prompt = sum(u.get("input_tokens", 0) for u in usage)
    completion = sum(u.get("output_tokens", 0) for u in usage)
    latency = int((time.perf_counter() - started) * 1000)
    async with async_session_scope(principal.org_id) as s:
        run = await s.get(QueryRun, run_id)
        assert run is not None  # noqa: S101
        run.status, run.trace = status, state.get("trace", [])
        run.route = [t["node"] for t in run.trace]
        run.prompt_tokens, run.completion_tokens = prompt, completion
        run.cost_usd, run.latency_ms = _cost(settings, prompt, completion), latency
        run.verification_summary = final.get("verification_summary")
        answer_msg = None
        if final:
            answer = {
                **final,
                "conversation_id": str(run.conversation_id),
                "run_id": str(run.id),
                "usage": {
                    "prompt_tokens": prompt,
                    "completion_tokens": completion,
                    "cost_usd": str(run.cost_usd) if run.cost_usd is not None else None,
                    "latency_ms": latency,
                },
            }
            answer_msg = Message(
                org_id=principal.org_id,
                conversation_id=run.conversation_id,
                role="assistant",
                content=final.get("answer_markdown", ""),
                answer=answer,
            )
            s.add(answer_msg)
            await s.flush()
            answer["message_id"] = str(answer_msg.id)
            answer_msg.answer = answer
            for c in final.get("citations", []):
                s.add(
                    Citation(
                        org_id=principal.org_id,
                        message_id=answer_msg.id,
                        citation_key=c["citation_id"],
                        chunk_id=uuid.UUID(c["chunk_id"]),
                        document_id=uuid.UUID(c["document_id"]),
                        document_version_id=uuid.UUID(c["version_id"]),
                        page_no=c["page"],
                        bbox=c["bbox"],
                        quote=c["quote"],
                        verification=c["verification"],
                    )
                )
            final = answer
        await audit.record(
            s,
            "query.run",
            org_id=principal.org_id,
            actor_id=principal.user_id,
            resource_type="query_run",
            resource_id=run_id,
            metadata={
                "status": status,
                "summary": run.verification_summary,
                "tokens": prompt + completion,
            },
        )
        await s.commit()
    if approval_id is not None:
        return {
            "status": "awaiting_approval",
            "approval_id": str(approval_id),
            "run_id": str(run_id),
        }
    return final


async def run_query_events(
    principal: Principal,
    question: str,
    document_ids: list[uuid.UUID],
    *,
    mode: str = "qa",
    conversation_id: uuid.UUID | None = None,
    settings: Settings | None = None,
) -> AsyncIterator[tuple[str, Any]]:
    """Yield (event, data) pairs: run, agent_step..., then approval_required | answer_delta... + final."""
    settings = settings or get_settings()
    if mode == "compare" and len(document_ids) < 2:
        raise QueryRejectedError("Comparison needs at least two documents.")
    started = time.perf_counter()
    conv_id, run_id, history = await _start_run(
        principal, question, document_ids, mode, conversation_id
    )
    yield "run", {"run_id": str(run_id), "conversation_id": str(conv_id)}

    thread = str(run_id)
    _DEPS[thread] = _deps_for(principal, settings)
    graph = build_graph().compile(checkpointer=await get_checkpointer(settings))
    config: RunnableConfig = {
        "configurable": {"thread_id": thread},
        "recursion_limit": RECURSION_LIMIT,
    }
    inp = {
        "question": question,
        "mode": mode,
        "document_ids": [str(d) for d in document_ids],
        "history": history,
        "requester_role": principal.role.value,
        "trace": [],
        "usage": [],
    }
    try:
        async for update in graph.astream(cast(Any, inp), config, stream_mode="updates"):
            for node, delta in update.items():
                if node == "__interrupt__":
                    continue
                trace = (delta or {}).get("trace") or [{}]
                yield "agent_step", {"node": node, **trace[-1]}
        snapshot = await graph.aget_state(config)
        if snapshot.next:  # paused on an interrupt -> human approval
            payload = (
                snapshot.tasks[0].interrupts[0].value
                if snapshot.tasks and snapshot.tasks[0].interrupts
                else {}
            )
            async with async_session_scope(principal.org_id) as s:
                appr = Approval(
                    org_id=principal.org_id,
                    thread_id=thread,
                    requested_by=principal.user_id,
                    action=payload.get("action", "approval"),
                    payload=payload,
                )
                s.add(appr)
                await s.commit()
            result = await _persist(
                principal,
                run_id,
                dict(snapshot.values),
                started,
                settings,
                "awaiting_approval",
                appr.id,
            )
            yield "approval_required", {**result, "payload": payload}
            return
        result = await _persist(
            principal, run_id, dict(snapshot.values), started, settings, "completed"
        )
    except Exception as exc:
        logger.exception("query_failed", run_id=thread)
        async with async_session_scope(principal.org_id) as s:
            run = await s.get(QueryRun, run_id)
            if run is not None:
                run.status, run.error = "failed", type(exc).__name__
                await s.commit()
        raise
    finally:
        _DEPS.pop(thread, None)

    # Verified answer delivered progressively (never streamed before verification).
    text = result.get("answer_markdown", "")
    for i in range(0, len(text), 80):
        yield "answer_delta", {"text": text[i : i + 80]}
    yield "final", result


async def run_query(
    principal: Principal, question: str, document_ids: list[uuid.UUID], **kw: Any
) -> dict[str, Any]:
    final: dict[str, Any] = {}
    async for event, data in run_query_events(principal, question, document_ids, **kw):
        if event in {"final", "approval_required"}:
            final = data
    return final


async def stream_query_sse(
    principal: Principal, question: str, document_ids: list[uuid.UUID], **kw: Any
) -> AsyncIterator[str]:
    try:
        async for event, data in run_query_events(principal, question, document_ids, **kw):
            yield _sse(event, data)
    except AppError as exc:
        yield _sse("error", {"title": exc.title, "status": exc.status_code, "detail": exc.detail})
    except Exception:
        yield _sse(
            "error",
            {
                "title": "Internal Server Error",
                "status": 500,
                "detail": "The query failed. Quote the run_id when reporting it.",
            },
        )


async def decide_approval(
    principal: Principal,
    approval_id: uuid.UUID,
    approved: bool,
    comment: str | None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    if ROLE_RANK[principal.role] < ROLE_RANK[Role.ADMIN]:
        from app.api.deps import ForbiddenError

        raise ForbiddenError("Only admins can decide approvals.")
    async with async_session_scope(principal.org_id) as s:
        appr = await s.get(Approval, approval_id)
        if appr is None:
            raise NotFoundError("Approval not found.")
        if appr.status is not ApprovalStatus.PENDING:
            raise ConflictError("This approval was already decided.")
        appr.status = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
        appr.decided_by, appr.decided_at, appr.comment = (
            principal.user_id,
            datetime.now(UTC),
            comment,
        )
        thread = appr.thread_id
        await audit.record(
            s,
            "approval.decide",
            org_id=principal.org_id,
            actor_id=principal.user_id,
            resource_type="approval",
            resource_id=approval_id,
            metadata={"approved": approved},
        )
        await s.commit()

    started = time.perf_counter()
    _DEPS[thread] = _deps_for(principal, settings)
    try:
        graph = build_graph().compile(checkpointer=await get_checkpointer(settings))
        config: RunnableConfig = {
            "configurable": {"thread_id": thread},
            "recursion_limit": RECURSION_LIMIT,
        }
        async for _ in graph.astream(
            Command(resume={"approved": approved, "decided_by": str(principal.user_id)}),
            config,
            stream_mode="updates",
        ):
            pass
        snapshot = await graph.aget_state(config)
    finally:
        _DEPS.pop(thread, None)
    return await _persist(
        principal, uuid.UUID(thread), dict(snapshot.values), started, settings, "completed"
    )
