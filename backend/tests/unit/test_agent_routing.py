from app.agents.graph import (
    route_after_approval,
    route_after_retrieve,
    route_after_supervisor,
    route_after_tables,
    route_after_verify,
)
from app.agents.nodes import classify_by_rules, run_computations


def _ev(*types: str) -> list[dict]:  # type: ignore[type-arg]
    return [{"chunk_type": t} for t in types]


def test_out_of_scope_short_circuits() -> None:
    assert route_after_supervisor({"plan": {"intent": "out_of_scope"}}) == "respond"
    assert route_after_supervisor({"plan": {"intent": "lookup"}}) == "approval_gate"


def test_approval_and_scoping_routes() -> None:
    assert route_after_approval({"approval": {"approved": False}}) == "respond"
    assert route_after_approval({"approval": None, "document_ids": []}) == "classify_documents"
    assert (
        route_after_approval({"approval": {"approved": True}, "document_ids": ["d"]}) == "retrieve"
    )


def test_specialists_run_only_when_needed() -> None:
    plan = {"needs_tables": True, "needs_charts": True}
    assert route_after_retrieve({"plan": plan, "evidence": _ev("text")}) == "reasoning"
    assert (
        route_after_retrieve({"plan": plan, "evidence": _ev("table_rows", "chart")})
        == "table_analysis"
    )
    assert route_after_retrieve({"plan": plan, "evidence": _ev("chart")}) == "chart_analysis"
    assert (
        route_after_retrieve(
            {
                "plan": {"needs_tables": False, "needs_charts": False},
                "evidence": _ev("table_rows", "chart"),
            }
        )
        == "reasoning"
    )
    assert route_after_tables({"plan": plan, "evidence": _ev("table", "chart")}) == "chart_analysis"
    assert route_after_tables({"plan": plan, "evidence": _ev("table")}) == "reasoning"


def test_verify_loop() -> None:
    assert route_after_verify({"refine_query": "need 2023 figure"}) == "retrieve"
    assert route_after_verify({"refine_query": None}) == "respond"


def test_rule_router() -> None:
    p = classify_by_rules("Compare revenue growth between the two reports", 2)
    assert (p["intent"], p["is_comparison"], p["needs_calculation"]) == ("compare", True, True)
    assert classify_by_rules("Summarize the outlook section", 1)["intent"] == "summarize"
    assert classify_by_rules("What does the bar chart show?", 1)["intent"] == "chart"
    assert classify_by_rules("Who signed the agreement?", 1)["intent"] == "lookup"


def test_computations_take_values_from_facts_not_from_the_model() -> None:
    facts = {
        "T1": {
            "value": "237.4",
            "unit": "USD",
            "scale": 1_000_000,
            "source_id": "S2",
            "label": "G&A 2024",
            "estimated": False,
        },
        "T2": {
            "value": "114.0",
            "unit": "USD",
            "scale": 1_000_000,
            "source_id": "S2",
            "label": "Cost of revenue 2024",
            "estimated": False,
        },
    }
    specs = [
        {
            "id": "calc1",
            "operation": "subtract",
            "operands": [{"fact_id": "T1", "value": "999"}, {"fact_id": "T2"}],
        }
    ]  # model's "999" ignored
    res, errs = run_computations(specs, facts, {})
    assert errs == []
    assert res["calc1"].display() == "USD 123.4 million"


def test_source_operands_must_appear_in_source() -> None:
    sources = {"S1": "Total revenue was USD 1,578.7 million"}
    ok, _ = run_computations(
        [
            {
                "id": "a",
                "operation": "add",
                "operands": [
                    {"source_id": "S1", "value": "1578.7"},
                    {"source_id": "S1", "value": "1578.7"},
                ],
            }
        ],
        {},
        sources,
    )
    assert "a" in ok
    bad, errs = run_computations(
        [
            {
                "id": "b",
                "operation": "add",
                "operands": [
                    {"source_id": "S1", "value": "2000"},
                    {"source_id": "S9", "value": "1"},
                ],
            }
        ],
        {},
        sources,
    )
    assert "b" not in bad
    assert "not found in source" in errs[0]
