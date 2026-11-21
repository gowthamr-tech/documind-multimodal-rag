"""Deterministic financial arithmetic (ADR 0007: "arithmetic is code, not tokens").

Operands carry a value, unit (currency code / "%"), scale (1, 1e3, 1e6, 1e9) and the citation id
of the evidence they came from. Operations normalise scale before combining and refuse to mix
incompatible units — a USD figure is never silently subtracted from a EUR one.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, getcontext
from typing import Literal

getcontext().prec = 34

Operation = Literal[
    "add", "subtract", "multiply", "divide", "pct_change", "ratio", "cagr", "share", "max", "min"
]

_SCALE_WORDS = {1: "", 1_000: "thousand", 1_000_000: "million", 1_000_000_000: "billion"}


class CalculationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Operand:
    value: Decimal
    unit: str | None = None
    scale: int = 1
    citation_id: str | None = None
    label: str | None = None
    estimated: bool = False  # e.g. read off a chart

    @classmethod
    def of(cls, value: str | float | int | Decimal, **kw: object) -> Operand:
        try:
            v = Decimal(str(value).replace(",", ""))
        except InvalidOperation as exc:
            raise CalculationError(f"not a number: {value!r}") from exc
        return cls(value=v, **kw)  # type: ignore[arg-type]

    def base(self) -> Decimal:
        return self.value * self.scale


@dataclass(frozen=True, slots=True)
class Result:
    operation: Operation
    value: Decimal
    unit: str | None
    scale: int
    operands: tuple[Operand, ...]
    estimated: bool
    expression: str

    def display(self, places: int = 1) -> str:
        q = Decimal(1).scaleb(-places)
        v = self.value.quantize(q, rounding=ROUND_HALF_UP)
        if self.unit == "%":
            return f"{v}%"
        words = _SCALE_WORDS.get(self.scale, f"x{self.scale}")
        return " ".join(p for p in (self.unit, f"{v:,}", words) if p)


def _common_unit(ops: tuple[Operand, ...]) -> str | None:
    units = {o.unit for o in ops if o.unit}
    if len(units) > 1:
        raise CalculationError(f"incompatible units: {sorted(units)}")
    return units.pop() if units else None


def _display_scale(ops: tuple[Operand, ...]) -> int:
    return max(o.scale for o in ops)


def compute(operation: Operation, *operands: Operand, periods: int | None = None) -> Result:
    if not operands:
        raise CalculationError("no operands")
    ops = tuple(operands)
    est = any(o.estimated for o in ops)

    def need(n: int) -> None:
        if len(ops) != n:
            raise CalculationError(f"{operation} needs exactly {n} operands, got {len(ops)}")

    match operation:
        case "add" | "subtract":
            unit = _common_unit(ops)
            scale = _display_scale(ops)
            total = ops[0].base()
            for o in ops[1:]:
                total = total + o.base() if operation == "add" else total - o.base()
            sym = " + " if operation == "add" else " - "
            return Result(
                operation, total / scale, unit, scale, ops, est, sym.join(_fmt(o) for o in ops)
            )
        case "pct_change":
            need(2)
            _common_unit(ops)
            old, new = ops[0].base(), ops[1].base()
            if old == 0:
                raise CalculationError("percentage change from zero is undefined")
            return Result(
                operation,
                (new - old) / abs(old) * 100,
                "%",
                1,
                ops,
                est,
                f"({_fmt(ops[1])} - {_fmt(ops[0])}) / |{_fmt(ops[0])}| x 100",
            )
        case "ratio" | "divide":
            need(2)
            if ops[1].base() == 0:
                raise CalculationError("division by zero")
            same = ops[0].unit == ops[1].unit
            unit = None if same else ops[0].unit
            return Result(
                operation,
                ops[0].base() / ops[1].base(),
                unit,
                1,
                ops,
                est,
                f"{_fmt(ops[0])} / {_fmt(ops[1])}",
            )
        case "share":
            need(2)
            _common_unit(ops)
            if ops[1].base() == 0:
                raise CalculationError("share of zero total is undefined")
            return Result(
                operation,
                ops[0].base() / ops[1].base() * 100,
                "%",
                1,
                ops,
                est,
                f"{_fmt(ops[0])} / {_fmt(ops[1])} x 100",
            )
        case "multiply":
            need(2)
            return Result(
                operation,
                ops[0].base() * ops[1].base(),
                ops[0].unit or ops[1].unit,
                1,
                ops,
                est,
                f"{_fmt(ops[0])} x {_fmt(ops[1])}",
            )
        case "cagr":
            need(2)
            if not periods or periods <= 0:
                raise CalculationError("cagr needs periods > 0")
            _common_unit(ops)
            start, end = ops[0].base(), ops[1].base()
            if start <= 0 or end <= 0:
                raise CalculationError("cagr needs positive start and end values")
            rate = (float(end / start) ** (1 / periods) - 1) * 100
            return Result(
                operation,
                Decimal(str(rate)),
                "%",
                1,
                ops,
                est,
                f"({_fmt(ops[1])} / {_fmt(ops[0])})^(1/{periods}) - 1",
            )
        case "max" | "min":
            unit = _common_unit(ops)
            pick = (max if operation == "max" else min)(ops, key=lambda o: o.base())
            return Result(
                operation,
                pick.value,
                unit,
                pick.scale,
                (pick,),
                pick.estimated,
                f"{operation}({', '.join(_fmt(o) for o in ops)}) = {pick.label or _fmt(pick)}",
            )
    raise CalculationError(f"unknown operation {operation}")


def _fmt(o: Operand) -> str:
    tag = f"[{o.citation_id}]" if o.citation_id else ""
    return f"{o.label + ' ' if o.label else ''}{o.value}{tag}"
