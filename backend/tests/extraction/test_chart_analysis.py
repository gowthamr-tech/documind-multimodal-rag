"""Chart analysis with a scripted vision model and REAL Tesseract OCR of a real rendered chart.

The fake VLM stands in for the provider (no key available); the validation logic under test —
cross-checking reported numbers against OCR of the pixels — runs for real.
"""

from typing import Any

import pytest

from app.extraction.charts.analyze import ChartData, analyze_chart, ocr_text, validate_chart
from app.providers.llm.base import ModelResult, ProviderResponseError
from benchmarks.synthetic import SEGMENTS, ReportSpec, _bar_chart_png


class ScriptedVLM:
    model_id = "scripted"

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls = 0

    def generate_json(
        self, *, image_png: bytes, prompt: str, schema: dict[str, Any]
    ) -> ModelResult:
        self.calls += 1
        assert image_png.startswith(b"\x89PNG")
        assert "Do not invent" in prompt
        return ModelResult(data=self.payload, model=self.model_id)


@pytest.fixture(scope="module")
def chart() -> tuple[bytes, ReportSpec]:
    spec = ReportSpec.generate("Northwind Analytics", 7)
    return _bar_chart_png(spec), spec


def _payload(spec: ReportSpec, tamper: bool = False) -> dict[str, Any]:
    y0, y1 = spec.years
    series = []
    for year in (y0, y1):
        pts = []
        for i, seg in enumerate(SEGMENTS):
            v = float(spec.revenue[seg][year])
            if tamper and i % 2 == 0:
                v += 37.3  # a hallucinated value
            pts.append({"x": seg, "y": v, "value_label_visible": True})
        series.append({"name": str(year), "points": pts})
    return {
        "chart_type": "bar",
        "title": "Revenue by segment",
        "categories": SEGMENTS,
        "series": series,
        "trends": [],
        "description": "Revenue by segment for two years.",
    }


def test_correct_reading_is_verified_by_ocr(chart: tuple[bytes, ReportSpec]) -> None:
    png, spec = chart
    if ocr_text(png) is None:
        pytest.skip("tesseract not installed")
    a = analyze_chart(ScriptedVLM(_payload(spec)), png, caption="Figure 1", embedded_text=[])
    v = a.validation
    assert v["points_total"] == 8
    assert v["value_agreement"] >= 0.75, v  # OCR of small bar labels is good but not perfect
    assert v["category_agreement"] >= 0.75, v


def test_hallucinated_values_are_caught(chart: tuple[bytes, ReportSpec]) -> None:
    png, spec = chart
    if ocr_text(png) is None:
        pytest.skip("tesseract not installed")
    good = analyze_chart(
        ScriptedVLM(_payload(spec)), png, caption=None, embedded_text=[]
    ).validation
    bad = analyze_chart(
        ScriptedVLM(_payload(spec, tamper=True)), png, caption=None, embedded_text=[]
    )
    assert bad.validation["points_verified"] <= good["points_verified"] - 4  # 4 tampered points
    assert bad.values_are_estimated is True
    tampered = {(p["series"], p["x"]) for p in bad.validation["verified_points"]}
    assert (str(spec.years[0]), SEGMENTS[0]) not in tampered


def test_schema_violations_raise() -> None:
    with pytest.raises(ProviderResponseError, match="schema"):
        analyze_chart(
            ScriptedVLM({"chart_type": "hologram"}), b"\x89PNG", caption=None, embedded_text=[]
        )


def test_without_ocr_nothing_is_verified() -> None:
    chart_data = ChartData.model_validate(
        {
            "chart_type": "line",
            "categories": ["Q1"],
            "series": [
                {"name": "s", "points": [{"x": "Q1", "y": 1.0, "value_label_visible": True}]}
            ],
            "trends": [],
            "description": "",
        }
    )
    v = validate_chart(chart_data, None)
    assert v["ocr_available"] is False
    assert v["points_verified"] == 0
