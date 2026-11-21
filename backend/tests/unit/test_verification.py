from decimal import Decimal

from app.agents.tools.calculator import Operand, compute
from app.agents.verification import Claim, apply_judge, check_claim, numbers_in, summarise

SOURCES = {
    "S1": "Total revenue was USD 1,578.7 million in FY2024, compared with USD 1,606.3 million in FY2023.",
    "S2": "General and administrative | Fiscal year / 2024: 237.4 (USD millions)",
}


def test_grounded_claim_is_verified() -> None:
    c = check_claim(Claim("Total revenue in FY2024 was USD 1,578.7 million.", ["S1"]), SOURCES, {})
    assert c.status == "verified"
    assert c.ungrounded_numbers == []


def test_fabricated_number_is_unsupported() -> None:
    c = check_claim(Claim("Total revenue in FY2024 was USD 1,700.0 million.", ["S1"]), SOURCES, {})
    assert c.status == "unsupported"
    assert "1700.0" in c.ungrounded_numbers


def test_fabricated_citation_is_caught() -> None:
    c = check_claim(Claim("Revenue was USD 1,578.7 million.", ["S9"]), SOURCES, {})
    assert c.status == "unsupported"
    assert c.invalid_citations == ["S9"]


def test_computed_number_is_grounded_via_calculator() -> None:
    r = compute(
        "pct_change", Operand.of("1606.3", citation_id="S1"), Operand.of("1578.7", citation_id="S1")
    )
    claim = Claim("Revenue decreased by 1.7% year over year.", ["S1"], ["calc1"])
    assert check_claim(claim, SOURCES, {"calc1": r}).status == "verified"
    # same claim without the computation record -> the 1.7 is ungrounded
    assert check_claim(Claim(claim.text, ["S1"]), SOURCES, {}).status == "unsupported"


def test_low_overlap_is_partial() -> None:
    c = check_claim(Claim("Management is optimistic about synergies 237.4.", ["S2"]), SOURCES, {})
    assert c.status == "partial"


def test_judge_only_downgrades() -> None:
    c = check_claim(Claim("Total revenue in FY2024 was USD 1,578.7 million.", ["S1"]), SOURCES, {})
    assert apply_judge(c, "partial").status == "partial"
    bad = check_claim(Claim("Revenue was USD 9.9 million.", ["S1"]), SOURCES, {})
    assert apply_judge(bad, "supported").status == "unsupported"


def test_numbers_in_parses_accounting_formats() -> None:
    assert numbers_in("loss of (21.2) and gain of $1,200.50, up 3.1%") == [
        Decimal("-21.2"),
        Decimal("1200.50"),
        Decimal("3.1"),
    ]


def test_summary_levels() -> None:
    ok = check_claim(Claim("Total revenue in FY2024 was USD 1,578.7 million.", ["S1"]), SOURCES, {})
    bad = check_claim(Claim("It was 5.", []), SOURCES, {})
    assert summarise([ok]) == "verified"
    assert summarise([ok, bad]) == "partially_verified"
    assert summarise([bad]) == "insufficient_evidence"
    assert summarise([]) == "insufficient_evidence"
