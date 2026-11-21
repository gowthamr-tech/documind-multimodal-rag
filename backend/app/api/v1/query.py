import uuid
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.agents import service
from app.api.deps import PrincipalDep, SettingsDep, TenantSessionDep
from app.core.errors import NotFoundError
from app.core.rate_limit import rate_limit
from app.database.models import Approval, Conversation, Message, QueryRun

router = APIRouter(tags=["query"])
_limit = [Depends(rate_limit("query", "query_rate_limit_per_minute"))]


class QueryIn(BaseModel):
    question: str = Field(min_length=2, max_length=4000)
    document_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)
    conversation_id: uuid.UUID | None = None
    stream: bool = False


class CompareIn(QueryIn):
    document_ids: list[uuid.UUID] = Field(min_length=2, max_length=10)


class ApprovalIn(BaseModel):
    approved: bool
    comment: str | None = Field(default=None, max_length=1000)


def _respond(body: QueryIn, principal: Any, settings: Any, mode: str) -> Any:
    kw = {"mode": mode, "conversation_id": body.conversation_id, "settings": settings}
    if body.stream:
        return StreamingResponse(
            service.stream_query_sse(principal, body.question, body.document_ids, **kw),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    return None


@router.post("/query", dependencies=_limit)
async def query(body: QueryIn, principal: PrincipalDep, settings: SettingsDep) -> Any:
    """Ask a question. `stream: true` returns Server-Sent Events: run, agent_step*, then
    answer_delta* + final (verified answer), approval_required, or error."""
    streamed = _respond(body, principal, settings, "qa")
    if streamed is not None:
        return streamed
    return await service.run_query(
        principal,
        body.question,
        body.document_ids,
        mode="qa",
        conversation_id=body.conversation_id,
        settings=settings,
    )


@router.post("/query/compare", dependencies=_limit)
async def compare(body: CompareIn, principal: PrincipalDep, settings: SettingsDep) -> Any:
    """Cross-document comparison: evidence is balanced across every selected document."""
    streamed = _respond(body, principal, settings, "compare")
    if streamed is not None:
        return streamed
    return await service.run_query(
        principal,
        body.question,
        body.document_ids,
        mode="compare",
        conversation_id=body.conversation_id,
        settings=settings,
    )


@router.post("/approvals/{approval_id}")
async def decide(
    approval_id: uuid.UUID, body: ApprovalIn, principal: PrincipalDep, settings: SettingsDep
) -> Any:
    return await service.decide_approval(
        principal, approval_id, body.approved, body.comment, settings
    )


@router.get("/approvals")
async def list_approvals(
    _: PrincipalDep, session: TenantSessionDep, status: str = "pending"
) -> list[dict[str, Any]]:
    rows = (
        (
            await session.execute(
                select(Approval)
                .where(Approval.status == status)
                .order_by(Approval.created_at.desc())
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": str(a.id),
            "action": a.action,
            "payload": a.payload,
            "status": a.status.value,
            "requested_by": str(a.requested_by),
            "created_at": a.created_at.isoformat(),
        }
        for a in rows
    ]


@router.get("/conversations")
async def list_conversations(
    principal: PrincipalDep, session: TenantSessionDep
) -> list[dict[str, Any]]:
    rows = (
        (
            await session.execute(
                select(Conversation)
                .where(Conversation.user_id == principal.user_id)
                .order_by(Conversation.updated_at.desc())
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": str(c.id),
            "title": c.title,
            "document_scope": [str(d) for d in c.document_scope],
            "updated_at": c.updated_at.isoformat(),
        }
        for c in rows
    ]


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: uuid.UUID, principal: PrincipalDep, session: TenantSessionDep
) -> dict[str, Any]:
    conv = await session.get(Conversation, conversation_id)
    if conv is None or conv.user_id != principal.user_id:
        raise NotFoundError("Conversation not found.")
    msgs = (
        (
            await session.execute(
                select(Message)
                .where(Message.conversation_id == conv.id)
                .order_by(Message.created_at)
            )
        )
        .scalars()
        .all()
    )
    return {
        "id": str(conv.id),
        "title": conv.title,
        "document_scope": [str(d) for d in conv.document_scope],
        "messages": [
            {
                "id": str(m.id),
                "role": m.role,
                "content": m.content,
                "answer": m.answer,
                "created_at": m.created_at.isoformat(),
            }
            for m in msgs
        ],
    }


@router.get("/query/runs")
async def list_runs(
    _: PrincipalDep, session: TenantSessionDep, limit: int = 50
) -> list[dict[str, Any]]:
    rows = (
        (
            await session.execute(
                select(QueryRun).order_by(QueryRun.created_at.desc()).limit(min(limit, 200))
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": str(r.id),
            "question": r.question,
            "mode": r.mode,
            "status": r.status,
            "route": r.route,
            "verification_summary": r.verification_summary,
            "prompt_tokens": r.prompt_tokens,
            "completion_tokens": r.completion_tokens,
            "latency_ms": r.latency_ms,
            "cost_usd": str(r.cost_usd) if r.cost_usd is not None else None,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]
