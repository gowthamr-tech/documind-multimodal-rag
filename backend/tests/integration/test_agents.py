"""The agent graph end to end over a real indexed report.

A scripted ChatModel stands in for the LLM provider (no key available). Everything else is
real: retrieval, table analysis from persisted cells, the calculator, verification, citation
resolution, Postgres checkpointing, approvals and persistence. The scripted "reasoning" reads
fact ids out of the actual prompt, exactly as a real model would have to.
"""

import json
import re
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import update

from app.agents import prompts
from app.database.models import Document, Role, Sensitivity
from app.database.session import async_session_scope
from app.providers.llm.base import Message, ModelResult, Usage
from benchmarks.synthetic import ReportSpec, fmt_money, write_report
from tests.integration.helpers import add_member, auth, org_of, register, upload

pytestmark = [pytest.mark.full_pipeline, pytest.mark.slow]


class ScriptedChat:
    model_id = "scripted"

    def __init__(
        self,
        reasoning: Callable[[str], dict[str, Any]],
        intent: str = "table_numeric",
        judge: str = "supported",
    ) -> None:
        self.reasoning, self.intent, self.judge = reasoning, intent, judge
        self.calls: list[str] = []

    def generate_json(
        self, *, messages: list[Message], schema: dict[str, Any], temperature: float = 0.0
    ) -> ModelResult:
        user = messages[-1].content
        if schema is prompts.SUPERVISOR_SCHEMA:
            self.calls.append("supervisor")
            data = {
                "intent": self.intent,
                "needs_tables": True,
                "needs_charts": False,
                "needs_calculation": True,
                "is_comparison": self.intent == "compare",
                "sub_queries": [],
                "reason": "scripted",
            }
        elif schema is prompts.REASONING_SCHEMA:
            self.calls.append("reasoning")
            data = self.reasoning(user)
        else:
            self.calls.append("judge")
            n = len(re.findall(r"^CLAIM \d+:", user, re.M))
            data = {"verdicts": [{"index": i, "verdict": self.judge} for i in range(n)]}
        return ModelResult(data=data, model="scripted", usage=Usage(100, 20))

    def stream_text(self, **_: Any):  # pragma: no cover - not used
        yield ""


def _fact(prompt: str, row: str, col: str) -> tuple[str, str]:
    """Find '<fact_id> [<source>] ... > <row> > ... / <col> = ...' in the FACTS section."""
    for line in prompt.split("FACTS", 1)[-1].splitlines():
        m = re.match(r"(T\d+) \[(S\d+)\] (.*) = ", line)
        if m and row in m.group(3) and m.group(3).endswith(col):
            return m.group(1), m.group(2)
    raise AssertionError(f"fact not in prompt: {row} / {col}")


@pytest.fixture
def use_chat(monkeypatch: pytest.MonkeyPatch) -> Callable[[Any], None]:
    def _set(chat: Any) -> None:
        monkeypatch.setattr("app.agents.service.get_chat_model", lambda _s: chat)

    return _set


async def _setup(
    client: AsyncClient, tmp: Path
) -> tuple[dict[str, str], dict[str, Any], ReportSpec]:
    pdf, truth = write_report("Northwind Analytics", 7, tmp)
    owner = await register(client, "owner@example.com", "Org A")
    doc = (await upload(client, owner, pdf, data={"title": "Northwind Analytics FY2024"})).json()
    return owner, doc, ReportSpec.generate("Northwind Analytics", 7)


async def test_numeric_question_computed_verified_and_cited(
    client: AsyncClient, tmp_path: Path, use_chat: Any
) -> None:
    owner, doc, spec = await _setup(client, tmp_path)
    cat, big = spec.largest_expense(2024)
    expected = fmt_money(big - spec.expenses["Cost of revenue"][2024])

    def reasoning(prompt: str) -> dict[str, Any]:
        f_big, s_big = _fact(prompt, cat, "2024")
        f_cor, _ = _fact(prompt, "Cost of revenue", "2024")
        return {
            "answer_markdown": f"The largest category was {cat.lower()} [{s_big}]. It exceeded cost of revenue by {{{{d}}}} [{s_big}].",
            "claims": [
                {
                    "text": f"The largest operating expense category in FY2024 was {cat.lower()} at USD {fmt_money(big)} million.",
                    "citation_ids": [s_big],
                },
                {
                    "text": f"{cat} exceeded cost of revenue by {{{{d}}}}.",
                    "citation_ids": [s_big],
                    "computation_ids": ["d"],
                },
            ],
            "computations": [
                {
                    "id": "d",
                    "operation": "subtract",
                    "operands": [{"fact_id": f_big}, {"fact_id": f_cor}],
                }
            ],
            "insufficient_evidence": False,
        }

    chat = ScriptedChat(reasoning)
    use_chat(chat)
    r = await client.post(
        "/api/v1/query",
        headers=auth(owner),
        json={
            "question": "Which expense category was largest in FY2024, and by how much did it exceed cost of revenue?",
            "document_ids": [doc["document_id"]],
        },
    )
    assert r.status_code == 200, r.text
    ans = r.json()
    assert expected in ans["answer_markdown"]  # number produced by the calculator, not the model
    assert ans["verification_summary"] == "verified", ans["claims"]
    assert ans["route"][:3] == ["supervisor", "approval_gate", "retrieve"]
    assert "table_analysis" in ans["route"]
    assert "chart_analysis" not in ans["route"]  # not needed -> not run
    cit = ans["citations"][0]
    assert cit["page"] == 2
    assert cit["document_id"] == doc["document_id"]
    assert cit["verification"] == "verified"
    assert ans["computations"]["d"]["operands"][0]["citation_id"] == cit["citation_id"]
    assert ans["usage"]["prompt_tokens"] == 300  # supervisor + reasoning + judge (scripted usage)
    assert chat.calls == ["supervisor", "reasoning", "judge"]

    conv = (
        await client.get(f"/api/v1/conversations/{ans['conversation_id']}", headers=auth(owner))
    ).json()
    assert [m["role"] for m in conv["messages"]] == ["user", "assistant"]
    runs = (await client.get("/api/v1/query/runs", headers=auth(owner))).json()
    assert runs[0]["status"] == "completed"
    assert runs[0]["verification_summary"] == "verified"


async def test_hallucinations_are_caught_and_retried(
    client: AsyncClient, tmp_path: Path, use_chat: Any
) -> None:
    owner, doc, _ = await _setup(client, tmp_path)

    def reasoning(prompt: str) -> dict[str, Any]:
        return {
            "answer_markdown": "Revenue was USD 9,999.9 million [S99].",
            "claims": [
                {
                    "text": "Total revenue in FY2024 was USD 9,999.9 million.",
                    "citation_ids": ["S99"],
                }
            ],
            "computations": [],
            "insufficient_evidence": False,
            "missing_information": "total revenue FY2024",
        }

    chat = ScriptedChat(reasoning, intent="lookup")
    use_chat(chat)
    ans = (
        await client.post(
            "/api/v1/query",
            headers=auth(owner),
            json={
                "question": "What was total revenue in FY2024?",
                "document_ids": [doc["document_id"]],
            },
        )
    ).json()
    assert ans["verification_summary"] == "insufficient_evidence"
    assert ans["claims"][0]["invalid_citations"] == ["S99"]
    assert ans["route"].count("retrieve") == 2  # verification triggered one bounded retry
    assert chat.calls.count("reasoning") == 2
    assert ans["citations"] == []  # nothing fabricated reaches the user as a citation
    assert any("Not fully verified" in lim for lim in ans["limitations"])


async def test_without_llm_returns_evidence_and_says_so(
    client: AsyncClient, tmp_path: Path, use_chat: Any
) -> None:
    owner, doc, _ = await _setup(client, tmp_path)
    use_chat(None)
    ans = (
        await client.post(
            "/api/v1/query",
            headers=auth(owner),
            json={
                "question": "What was total revenue in FY2024?",
                "document_ids": [doc["document_id"]],
            },
        )
    ).json()
    assert "no language model is configured" in ans["answer_markdown"]
    assert ans["verification_summary"] == "insufficient_evidence"
    assert ans["citations"]
    assert all(c["verification"] == "unverified" for c in ans["citations"])
    assert ans["plan"]["classifier"] == "rules"


async def test_streaming_sse_events(client: AsyncClient, tmp_path: Path, use_chat: Any) -> None:
    owner, doc, _ = await _setup(client, tmp_path)
    use_chat(None)
    async with client.stream(
        "POST",
        "/api/v1/query",
        headers=auth(owner),
        json={
            "question": "What was total revenue?",
            "document_ids": [doc["document_id"]],
            "stream": True,
        },
    ) as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        body = "".join([chunk async for chunk in r.aiter_text()])
    events = re.findall(r"^event: (\w+)$", body, re.M)
    assert events[0] == "run"
    assert "agent_step" in events
    assert events[-1] == "final"
    final = json.loads(body.rsplit("data: ", 1)[1])
    assert final["citations"]


async def test_confidential_document_requires_admin_approval(
    client: AsyncClient, tmp_path: Path, use_chat: Any
) -> None:
    owner, doc, _ = await _setup(client, tmp_path)
    org = org_of(owner)
    async with async_session_scope(org) as s:
        await s.execute(
            update(Document)
            .where(Document.id == uuid.UUID(doc["document_id"]))
            .values(sensitivity=Sensitivity.CONFIDENTIAL)
        )
        await s.commit()
    member = await add_member(client, org, "member@example.com", Role.MEMBER)
    use_chat(None)
    pending = (
        await client.post(
            "/api/v1/query",
            headers=auth(member),
            json={"question": "What was total revenue?", "document_ids": [doc["document_id"]]},
        )
    ).json()
    assert pending["status"] == "awaiting_approval"
    approvals = (await client.get("/api/v1/approvals", headers=auth(owner))).json()
    assert approvals[0]["payload"]["documents"][0]["id"] == doc["document_id"]

    forbidden = await client.post(
        f"/api/v1/approvals/{pending['approval_id']}", headers=auth(member), json={"approved": True}
    )
    assert forbidden.status_code == 403
    done = (
        await client.post(
            f"/api/v1/approvals/{pending['approval_id']}",
            headers=auth(owner),
            json={"approved": True},
        )
    ).json()
    assert done["citations"]  # resumed from the Postgres checkpoint and completed
    again = await client.post(
        f"/api/v1/approvals/{pending['approval_id']}", headers=auth(owner), json={"approved": False}
    )
    assert again.status_code == 409

    rejected_run = (
        await client.post(
            "/api/v1/query",
            headers=auth(member),
            json={"question": "Total revenue?", "document_ids": [doc["document_id"]]},
        )
    ).json()
    out = (
        await client.post(
            f"/api/v1/approvals/{rejected_run['approval_id']}",
            headers=auth(owner),
            json={"approved": False},
        )
    ).json()
    assert "not approved" in out["answer_markdown"]
    assert out["citations"] == []


async def test_out_of_scope_skips_retrieval(
    client: AsyncClient, tmp_path: Path, use_chat: Any
) -> None:
    owner, doc, _ = await _setup(client, tmp_path)
    use_chat(ScriptedChat(lambda p: {}, intent="out_of_scope"))
    ans = (
        await client.post(
            "/api/v1/query",
            headers=auth(owner),
            json={"question": "Tell me a joke", "document_ids": [doc["document_id"]]},
        )
    ).json()
    assert ans["route"] == ["supervisor", "respond"]
    assert ans["citations"] == []


async def test_compare_requires_two_documents(client: AsyncClient, tmp_path: Path) -> None:
    owner = await register(client, "o@example.com", "Org A")
    r = await client.post(
        "/api/v1/query/compare",
        headers=auth(owner),
        json={"question": "compare", "document_ids": [str(uuid.uuid4())]},
    )
    assert r.status_code == 422
