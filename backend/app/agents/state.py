"""Typed shared state for the document-intelligence graph (ADR 0007).

Everything in the state is JSON-serialisable so it can be checkpointed in Postgres and resumed
after a human-approval interrupt or a worker restart.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict

Intent = Literal["lookup", "table_numeric", "chart", "compare", "summarize", "out_of_scope"]

MAX_VERIFY_ATTEMPTS = 2
RECURSION_LIMIT = 25


class Plan(TypedDict, total=False):
    intent: Intent
    needs_tables: bool
    needs_charts: bool
    needs_calculation: bool
    is_comparison: bool
    sub_queries: list[str]
    reason: str
    classifier: str  # "llm" | "rules"


class GraphState(TypedDict, total=False):
    # inputs
    question: str
    mode: Literal["qa", "compare"]
    document_ids: list[str]
    history: list[dict[str, str]]
    # Who asked. Recorded at start and checkpointed: on resume, nodes re-run with the
    # *approver's* deps, so security decisions must use the requester's role from state.
    requester_role: str
    # planning
    plan: Plan
    doc_scope: list[str]
    approval: dict[str, Any] | None
    # evidence
    evidence: list[dict[str, Any]]
    table_facts: list[dict[str, Any]]
    chart_facts: list[dict[str, Any]]
    retrieval_queries: list[str]
    # reasoning + verification
    draft: dict[str, Any] | None
    computations: dict[str, dict[str, Any]]
    computation_errors: list[str]
    checks: list[dict[str, Any]]
    attempts: int
    refine_query: str | None
    # output
    final: dict[str, Any]
    # bookkeeping (reducers append across nodes)
    trace: Annotated[list[dict[str, Any]], operator.add]
    usage: Annotated[list[dict[str, int]], operator.add]
