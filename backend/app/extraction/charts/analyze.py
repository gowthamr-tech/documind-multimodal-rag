"""Chart understanding with a vision model + OCR cross-validation (ADR 0004).

The VLM proposes a structured reading of the chart. We then OCR the same crop with Tesseract and
check every reported number and category against the pixels' text. Only values found verbatim
in OCR are marked verified; everything else stays an *estimate*. Chart-derived numbers are never
presented as exact unless verified.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from app.providers.llm.base import ProviderResponseError, Usage, VisionModel

PROMPT_VERSION = "chart-v1"


class ChartPoint(BaseModel):
    x: str
    y: float | None = Field(description="null if the value cannot be read")
    value_label_visible: bool = Field(description="true only if the number is printed on the chart")


class ChartSeries(BaseModel):
    name: str
    points: list[ChartPoint]


class ChartAxis(BaseModel):
    label: str | None = None
    unit: str | None = None


class ChartData(BaseModel):
    chart_type: Literal["bar", "stacked_bar", "line", "area", "pie", "scatter", "other"]
    title: str | None = None
    x_axis: ChartAxis = Field(default_factory=ChartAxis)
    y_axis: ChartAxis = Field(default_factory=ChartAxis)
    categories: list[str] = Field(default_factory=list)
    series: list[ChartSeries] = Field(default_factory=list)
    trends: list[str] = Field(
        default_factory=list, description="Observations grounded in the chart"
    )
    description: str = ""


# OpenAPI-subset schema for providers' structured-output mode (no $defs / additionalProperties).
_AXIS = {
    "type": "object",
    "properties": {
        "label": {"type": "string", "nullable": True},
        "unit": {"type": "string", "nullable": True},
    },
}
CHART_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "chart_type": {
            "type": "string",
            "enum": ["bar", "stacked_bar", "line", "area", "pie", "scatter", "other"],
        },
        "title": {"type": "string", "nullable": True},
        "x_axis": _AXIS,
        "y_axis": _AXIS,
        "categories": {"type": "array", "items": {"type": "string"}},
        "series": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "points": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "x": {"type": "string"},
                                "y": {"type": "number", "nullable": True},
                                "value_label_visible": {"type": "boolean"},
                            },
                            "required": ["x", "y", "value_label_visible"],
                        },
                    },
                },
                "required": ["name", "points"],
            },
        },
        "trends": {"type": "array", "items": {"type": "string"}},
        "description": {"type": "string"},
    },
    "required": ["chart_type", "categories", "series", "trends", "description"],
}

_PROMPT = """You are extracting data from a chart image taken from a business document.
Return JSON matching the schema. Rules:
- Report only what is visible. Do not invent series, categories or values.
- For each point, if the exact number is printed on the chart set value_label_visible=true and
  copy it exactly; otherwise estimate from the axis scale and set value_label_visible=false.
- If a value cannot be determined, set y to null.
- "trends" must be factual observations supported by the plotted data (e.g. "X grew from A to B").
- Use the document caption only to disambiguate; the image is the source of truth.
Caption from the document: {caption}
Text found near/inside the figure: {embedded}"""


@dataclass(slots=True)
class ChartAnalysis:
    chart: ChartData
    validation: dict[str, Any]
    values_are_estimated: bool
    model: str
    prompt_version: str = PROMPT_VERSION
    usage: Usage = field(default_factory=Usage)


def ocr_text(image_png: bytes, timeout_s: int = 60) -> str | None:
    """Tesseract over the crop (sparse-text mode suits charts). None when unavailable."""
    exe = shutil.which("tesseract")
    if exe is None:
        return None
    proc = subprocess.run(  # noqa: S603 - fixed argv
        [exe, "stdin", "stdout", "--psm", "11"],
        input=image_png,
        capture_output=True,
        timeout=timeout_s,
        check=False,
    )
    return proc.stdout.decode("utf-8", "replace") if proc.returncode == 0 else None


_NUM = re.compile(r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?")


def _numbers(text: str) -> set[Decimal]:
    out: set[Decimal] = set()
    for tok in _NUM.findall(text):
        try:
            out.add(Decimal(tok.replace(",", "")))
        except InvalidOperation:
            continue
    return out


def validate_chart(chart: ChartData, ocr: str | None) -> dict[str, Any]:
    """Cross-check the VLM reading against OCR of the same pixels."""
    if ocr is None:
        return {
            "ocr_available": False,
            "points_total": sum(len(s.points) for s in chart.series),
            "points_verified": 0,
            "verified_points": [],
        }
    nums = _numbers(ocr)
    low = ocr.lower()
    verified, total = [], 0
    for s in chart.series:
        for p in s.points:
            if p.y is None:
                continue
            total += 1
            value = Decimal(str(p.y))
            # match at the printed precision (e.g. 345.5 vs "345.5"), tolerate float noise
            if any(abs(value - n) <= Decimal("0.05") for n in nums):
                verified.append({"series": s.name, "x": p.x, "y": p.y})
    cats = chart.categories or sorted({p.x for s in chart.series for p in s.points})
    cats_found = [c for c in cats if c.lower() in low]
    return {
        "ocr_available": True,
        "points_total": total,
        "points_verified": len(verified),
        "verified_points": verified,
        "value_agreement": round(len(verified) / total, 4) if total else None,
        "categories_total": len(cats),
        "categories_found": len(cats_found),
        "category_agreement": round(len(cats_found) / len(cats), 4) if cats else None,
    }


def analyze_chart(
    vlm: VisionModel, image_png: bytes, *, caption: str | None, embedded_text: list[str]
) -> ChartAnalysis:
    result = vlm.generate_json(
        image_png=image_png,
        prompt=_PROMPT.format(
            caption=caption or "(none)", embedded="; ".join(embedded_text) or "(none)"
        ),
        schema=CHART_SCHEMA,
    )
    try:
        chart = ChartData.model_validate(result.data)
    except ValidationError as exc:
        raise ProviderResponseError(
            f"chart output failed schema validation: {exc.error_count()} errors"
        ) from exc
    validation = validate_chart(chart, ocr_text(image_png))
    all_verified = (
        validation["points_total"] > 0
        and validation["points_verified"] == validation["points_total"]
    )
    return ChartAnalysis(
        chart=chart,
        validation=validation,
        values_are_estimated=not all_verified,
        model=result.model,
        usage=result.usage,
    )


IMAGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "description": {"type": "string"},
        "visible_text": {"type": "array", "items": {"type": "string"}},
        "kind": {"type": "string", "enum": ["diagram", "photo", "screenshot", "logo", "other"]},
    },
    "required": ["description", "visible_text", "kind"],
}


def describe_image(
    vlm: VisionModel, image_png: bytes, *, caption: str | None
) -> tuple[str, str, Usage]:
    result = vlm.generate_json(
        image_png=image_png,
        prompt=(
            "Describe this figure from a business document factually in 2-4 sentences, focusing on "
            "information a reader would search for. List any legible text exactly. "
            "Do not speculate. "
            f"Caption: {caption or '(none)'}"
        ),
        schema=IMAGE_SCHEMA,
    )
    desc = str(result.data.get("description", "")).strip()
    text = [str(t) for t in result.data.get("visible_text", [])]
    if text:
        desc += "\nVisible text: " + "; ".join(text)
    return desc, result.model, result.usage
