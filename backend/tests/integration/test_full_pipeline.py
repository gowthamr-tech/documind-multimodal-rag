"""Upload -> render -> Docling extraction -> persistence -> API, end to end, on a synthetic report."""

from decimal import Decimal
from pathlib import Path

import pytest
from httpx import AsyncClient

from benchmarks.synthetic import write_report
from tests.integration.helpers import auth, register, upload

pytestmark = [pytest.mark.full_pipeline, pytest.mark.slow]


async def test_full_pipeline_extracts_and_serves_structure(
    client: AsyncClient, tmp_path: Path
) -> None:
    pdf, truth = write_report("Northwind Analytics", 7, tmp_path)
    owner = await register(client, "owner@example.com", "Org A")
    doc = (await upload(client, owner, pdf)).json()
    did = doc["document_id"]

    st = (await client.get(f"/api/v1/documents/{did}/status", headers=auth(owner))).json()
    assert st["status"] == "ready", st
    assert [s["stage"] for s in st["stages"]] == [
        "fetch_verify",
        "render",
        "parse_extract",
        "analyze_figures",
        "chunk_embed",
    ]
    parse = next(s for s in st["stages"] if s["stage"] == "parse_extract")
    assert parse["metrics"]["tables"] == 2
    assert parse["metrics"]["figures"] == 2
    figs_stage = next(s for s in st["stages"] if s["stage"] == "analyze_figures")
    assert figs_stage["metrics"].get("reason") == "no_vision_model"  # honest skip without a key

    pages = (await client.get(f"/api/v1/documents/{did}/pages", headers=auth(owner))).json()
    assert [p["page_no"] for p in pages] == [1, 2, 3]
    assert all(p["has_image"] for p in pages)
    img = await client.get(f"/api/v1/documents/{did}/pages/2/image", headers=auth(owner))
    assert img.status_code == 200
    assert img.content.startswith(b"\x89PNG")

    tables = (await client.get(f"/api/v1/documents/{did}/tables", headers=auth(owner))).json()
    assert [t["caption"] for t in tables] == [t["caption"] for t in truth["tables"]]
    t2 = tables[1]
    other = next(
        c
        for c in t2["cells"]
        if c["row_path"] == ["Other income (loss)"] and c["header_path"][-1] == "2024"
    )
    assert Decimal(other["numeric_value"]) == Decimal(truth["facts"]["other_income"]["2024"])
    assert '<th colspan="2">Fiscal year</th>' in t2["html"]

    charts = (
        await client.get(f"/api/v1/documents/{did}/charts?type=chart", headers=auth(owner))
    ).json()
    assert [c["classifier_label"] for c in charts] == ["bar_chart", "line_chart"]
    assert all(c["analysis_status"] == "skipped" and "GEMINI_API_KEY" in c["error"] for c in charts)
    crop = await client.get(
        f"/api/v1/documents/{did}/charts/{charts[0]['id']}/image", headers=auth(owner)
    )
    assert crop.content.startswith(b"\x89PNG")

    elements = (
        await client.get(f"/api/v1/documents/{did}/elements?page=2", headers=auth(owner))
    ).json()
    assert any(e["text"].startswith("The largest expense category") for e in elements)

    original = await client.get(f"/api/v1/documents/{did}/file", headers=auth(owner))
    assert original.content == pdf.read_bytes()

    # Tenant isolation holds for the new endpoints too.
    other_org = await register(client, "b@example.com", "Org B")
    for path in ("tables", "charts", "pages", "pages/1/image", "elements", "file"):
        assert (
            await client.get(f"/api/v1/documents/{did}/{path}", headers=auth(other_org))
        ).status_code == 404
