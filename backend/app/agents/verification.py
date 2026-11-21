"""Claim verification (ADR 0007).

Deterministic checks run for every answer:
  1. citation existence — each cited source id must be one the server put in the context;
  2. numeric grounding — every number in a claim must appear in a cited source, or be the
     output (or an operand) of a recorded calculator result;
  3. lexical support — content words of the claim must overlap the cited text.
An optional LLM entailment judge can *downgrade* a claim, never upgrade it past the
deterministic result. Status: verified | partial | unsupported.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from app.agents.tools.calculator import Result

Status = Literal["verified", "partial", "unsupported"]

_NUM = re.compile(r"(?<![\w.])\(?-?\$?\d[\d,]*(?:\.\d+)?%?\)?")
_STOP = set(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "been",
        "by",
        "for",
        "from",
        "has",
        "have",
        "in",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "was",
        "were",
        "will",
        "with",
        "which",
        "who",
        "what",
        "when",
        "where",
        "how",
        "than",
        "then",
        "there",
        "their",
        "they",
        "these",
        "those",
        "into",
        "over",
        "under",
        "also",
        "more",
        "most",
        "less",
        "least",
        "per",
        "about",
        "after",
        "before",
        "during",
        "between",
        "both",
        "each",
    ]
)
# Relational/comparative vocabulary describes *computed* relationships (grounded by the
# calculator), not source facts, so it is not required to appear in the cited text.
_RELATIONAL = set(
    [
        "increase",
        "increased",
        "increases",
        "decrease",
        "decreased",
        "decreases",
        "grew",
        "grow",
        "growth",
        "rose",
        "rise",
        "fell",
        "fall",
        "declined",
        "decline",
        "change",
        "changed",
        "changes",
        "difference",
        "differ",
        "higher",
        "lower",
        "larger",
        "smaller",
        "largest",
        "smallest",
        "biggest",
        "highest",
        "lowest",
        "greater",
        "compared",
        "comparison",
        "versus",
        "year",
        "years",
        "annual",
        "quarter",
        "quarterly",
        "period",
        "total",
        "combined",
        "respectively",
        "approximately",
        "roughly",
        "percent",
        "percentage",
        "points",
        "times",
        "share",
        "ratio",
    ]
)
_YEARISH = re.compile(r"^(19|20)\d{2}$")


@dataclass(slots=True)
class Claim:
    text: str
    citation_ids: list[str]
    computation_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ClaimCheck:
    claim: Claim
    status: Status
    valid_citations: list[str]
    invalid_citations: list[str]
    ungrounded_numbers: list[str]
    lexical_support: float
    judge: str | None = None
    notes: list[str] = field(default_factory=list)


def numbers_in(text: str) -> list[Decimal]:
    out = []
    for tok in _NUM.findall(text):
        neg = (tok.startswith("(") and tok.endswith(")")) or tok.lstrip("(").startswith("-")
        digits = re.sub(r"[^\d.]", "", tok)
        if not digits or digits == ".":
            continue
        try:
            v = Decimal(digits)
        except InvalidOperation:
            continue
        out.append(-v if neg else v)
    return out


def _content_words(text: str) -> set[str]:
    return {
        w
        for w in re.findall(r"[a-z][a-z\-]{2,}", text.lower())
        if w not in _STOP and w not in _RELATIONAL
    }


def _close(a: Decimal, b: Decimal) -> bool:
    if a == b or abs(a) == abs(b):  # sign is often expressed in words ("a decrease of 1.7%")
        return True
    # tolerate display rounding: 1578.7 vs 1,578.66; 12.46% vs 12.5%
    tol = max(Decimal("0.05"), abs(b) * Decimal("0.0005"))
    return abs(abs(a) - abs(b)) <= tol


def check_claim(
    claim: Claim,
    sources: dict[str, str],
    computations: dict[str, Result],
) -> ClaimCheck:
    valid = [c for c in claim.citation_ids if c in sources]
    invalid = [c for c in claim.citation_ids if c not in sources]
    cited_text = "\n".join(sources[c] for c in valid)

    allowed = numbers_in(cited_text)
    for cid in claim.computation_ids:
        r = computations.get(cid)
        if r is None:
            continue
        allowed.append(r.value)
        allowed.append(r.value * r.scale)
        for places in (0, 1, 2):
            allowed.append(r.value.quantize(Decimal(1).scaleb(-places)))
        for o in r.operands:
            allowed.extend([o.value, o.base()])

    ungrounded = []
    for n in numbers_in(claim.text):
        if _YEARISH.match(str(abs(n))) and "." not in str(n):
            # years are grounded if they appear anywhere in cited text
            if not any(n == a for a in allowed):
                ungrounded.append(str(n))
            continue
        if not any(_close(n, a) for a in allowed):
            ungrounded.append(str(n))

    words = _content_words(claim.text)
    support = len(words & _content_words(cited_text)) / len(words) if words else 1.0

    notes = []
    if invalid:
        notes.append(f"cited unknown sources {invalid}")
    if ungrounded:
        notes.append(f"numbers not found in sources or calculations: {ungrounded}")
    if not valid:
        status: Status = "unsupported"
        notes.append("no valid citation")
    elif ungrounded:
        status = "unsupported"
    elif support < 0.35:
        status = "partial"
        notes.append(f"low lexical support ({support:.2f})")
    else:
        status = "verified"
    return ClaimCheck(claim, status, valid, invalid, ungrounded, round(support, 3), notes=notes)


def apply_judge(check: ClaimCheck, verdict: str) -> ClaimCheck:
    """Combine with an LLM entailment verdict (supported|partial|unsupported); downgrade only."""
    order = {"unsupported": 0, "partial": 1, "verified": 2}
    mapped: Status = {"supported": "verified", "partial": "partial"}.get(verdict, "unsupported")  # type: ignore[assignment]
    check.judge = verdict
    if order[mapped] < order[check.status]:
        check.notes.append(f"entailment judge: {verdict}")
        check.status = mapped
    return check


def summarise(
    checks: list[ClaimCheck],
) -> Literal["verified", "partially_verified", "insufficient_evidence"]:
    if not checks or all(c.status == "unsupported" for c in checks):
        return "insufficient_evidence"
    if all(c.status == "verified" for c in checks):
        return "verified"
    return "partially_verified"


def as_dict(c: ClaimCheck) -> dict[str, Any]:
    return {
        "text": c.claim.text,
        "citation_ids": c.valid_citations,
        "status": c.status,
        "invalid_citations": c.invalid_citations,
        "ungrounded_numbers": c.ungrounded_numbers,
        "lexical_support": c.lexical_support,
        "judge": c.judge,
        "notes": c.notes,
        "computation_ids": c.claim.computation_ids,
    }
