"""The LangGraph workflow: conditional routing, retries, limits, checkpointing, HITL.

START -> supervisor -> approval_gate -> [classify_documents] -> retrieve
      -> [table_analysis] -> [chart_analysis] -> reasoning -> verify
      -> (retry: retrieve | done: respond) -> END
out_of_scope / approval rejected short-circuit to respond.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

from app.agents import nodes
from app.agents.state import GraphState
from app.providers.llm import ProviderError

# Retry only transient failures; validation errors must surface, not loop.
_RETRY = RetryPolicy(
    max_attempts=3,
    initial_interval=0.5,
    backoff_factor=2.0,
    retry_on=(ConnectionError, TimeoutError, ProviderError),
)


# ---- routing functions: pure functions of state (unit-tested) ----
def route_after_supervisor(state: GraphState) -> str:
    return "respond" if state.get("plan", {}).get("intent") == "out_of_scope" else "approval_gate"


def route_after_approval(state: GraphState) -> str:
    approval = state.get("approval")
    if approval is not None and not approval.get("approved"):
        return "respond"
    if not state.get("document_ids"):
        return "classify_documents"
    return "retrieve"


def _has(state: GraphState, types: set[str]) -> bool:
    return any(e["chunk_type"] in types for e in state.get("evidence", []))


def route_after_retrieve(state: GraphState) -> str:
    plan = state.get("plan", {})
    if plan.get("needs_tables", True) and _has(state, {"table", "table_rows"}):
        return "table_analysis"
    if plan.get("needs_charts", True) and _has(state, {"chart"}):
        return "chart_analysis"
    return "reasoning"


def route_after_tables(state: GraphState) -> str:
    if state.get("plan", {}).get("needs_charts", True) and _has(state, {"chart"}):
        return "chart_analysis"
    return "reasoning"


def route_after_verify(state: GraphState) -> str:
    return "retrieve" if state.get("refine_query") else "respond"


def build_graph() -> StateGraph[Any]:
    g: StateGraph[Any] = StateGraph(GraphState)
    g.add_node("supervisor", nodes.supervisor, retry_policy=_RETRY)
    g.add_node("approval_gate", nodes.approval_gate)
    g.add_node("classify_documents", nodes.classify_documents, retry_policy=_RETRY)
    g.add_node("retrieve", nodes.retrieve, retry_policy=_RETRY)
    g.add_node("table_analysis", nodes.table_analysis)
    g.add_node("chart_analysis", nodes.chart_analysis)
    g.add_node("reasoning", nodes.reasoning, retry_policy=_RETRY)
    g.add_node("verify", nodes.verify, retry_policy=_RETRY)
    g.add_node("respond", nodes.respond)

    g.add_edge(START, "supervisor")
    g.add_conditional_edges("supervisor", route_after_supervisor, ["approval_gate", "respond"])
    g.add_conditional_edges(
        "approval_gate", route_after_approval, ["classify_documents", "retrieve", "respond"]
    )
    g.add_edge("classify_documents", "retrieve")
    g.add_conditional_edges(
        "retrieve", route_after_retrieve, ["table_analysis", "chart_analysis", "reasoning"]
    )
    g.add_conditional_edges("table_analysis", route_after_tables, ["chart_analysis", "reasoning"])
    g.add_edge("chart_analysis", "reasoning")
    g.add_edge("reasoning", "verify")
    g.add_conditional_edges("verify", route_after_verify, ["retrieve", "respond"])
    g.add_edge("respond", END)
    return g
