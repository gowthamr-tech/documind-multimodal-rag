from decimal import Decimal

import pytest

from app.agents.tools.calculator import CalculationError, Operand, compute


def test_subtract_normalises_scale() -> None:
    a = Operand.of("1.2", unit="USD", scale=1_000_000_000, citation_id="S1")
    b = Operand.of("950", unit="USD", scale=1_000_000, citation_id="S2")
    r = compute("subtract", a, b)
    assert r.value * r.scale == Decimal("250000000")
    assert r.display() == "USD 0.3 billion"


def test_pct_change_against_known_answer() -> None:
    r = compute(
        "pct_change",
        Operand.of("1606.3", unit="USD", scale=10**6),
        Operand.of("1578.7", unit="USD", scale=10**6),
    )
    assert r.display() == "-1.7%"


def test_pct_change_from_negative_uses_absolute_base() -> None:
    r = compute("pct_change", Operand.of("-10"), Operand.of("-5"))
    assert r.value == Decimal("50")


def test_refuses_mixed_currencies() -> None:
    with pytest.raises(CalculationError, match="incompatible"):
        compute("add", Operand.of(1, unit="USD"), Operand.of(1, unit="EUR"))


def test_division_by_zero_and_bad_input() -> None:
    with pytest.raises(CalculationError):
        compute("ratio", Operand.of(1), Operand.of(0))
    with pytest.raises(CalculationError, match="not a number"):
        Operand.of("twelve")


def test_max_picks_largest_and_keeps_its_citation() -> None:
    r = compute(
        "max",
        Operand.of("231.2", label="G&A", citation_id="S3"),
        Operand.of("100.7", label="Cost of revenue", citation_id="S4"),
    )
    assert r.operands[0].citation_id == "S3"
    assert "G&A" in r.expression


def test_estimated_operands_propagate() -> None:
    r = compute("add", Operand.of(1, estimated=True), Operand.of(2))
    assert r.estimated is True


def test_cagr() -> None:
    r = compute("cagr", Operand.of(100), Operand.of(121), periods=2)
    assert r.display() == "10.0%"


def test_share() -> None:
    assert compute("share", Operand.of(25), Operand.of(200)).display() == "12.5%"
