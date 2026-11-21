"""Extraction metrics: table cell precision/recall/F1, OCR character/word error rates.

Cell P/R compares (row, col, normalised text) triples of span-expanded grids — so a merged
header that spans two columns must be recovered *as* spanning two columns to score fully.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass


def norm_text(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    for dash in ("\u2212", "\u2014", "\u2013"):  # minus sign, em dash, en dash
        s = s.replace(dash, "-")
    return re.sub(r"\s+", " ", s).strip().lower()


@dataclass(frozen=True, slots=True)
class PRF:
    precision: float
    recall: float
    f1: float
    tp: int
    fp: int
    fn: int


def _prf(tp: int, fp: int, fn: int) -> PRF:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return PRF(round(p, 4), round(r, 4), round(f, 4), tp, fp, fn)


def cell_prf(pred: list[list[str]], truth: list[list[str]]) -> PRF:
    """Position-aware cell matching: a cell counts only if its text appears at the same (r, c)."""

    def triples(g: list[list[str]]) -> Counter[tuple[int, int, str]]:
        return Counter(
            (r, c, norm_text(v))
            for r, row in enumerate(g)
            for c, v in enumerate(row)
            if norm_text(v)
        )

    p, t = triples(pred), triples(truth)
    tp = sum((p & t).values())
    return _prf(tp, sum(p.values()) - tp, sum(t.values()) - tp)


def content_prf(pred: list[list[str]], truth: list[list[str]]) -> PRF:
    """Position-free: did we recover the cell *values* at all (robust to row/col offsets)?"""
    p = Counter(norm_text(v) for row in pred for v in row if norm_text(v))
    t = Counter(norm_text(v) for row in truth for v in row if norm_text(v))
    tp = sum((p & t).values())
    return _prf(tp, sum(p.values()) - tp, sum(t.values()) - tp)


def _edit_distance(a: list[str] | str, b: list[str] | str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def cer(pred: str, truth: str) -> float:
    """Character error rate (edit distance / reference length) on normalised text."""
    p, t = norm_text(pred), norm_text(truth)
    return round(_edit_distance(p, t) / max(1, len(t)), 4)


def wer(pred: str, truth: str) -> float:
    p, t = norm_text(pred).split(), norm_text(truth).split()
    return round(_edit_distance(p, t) / max(1, len(t)), 4)
