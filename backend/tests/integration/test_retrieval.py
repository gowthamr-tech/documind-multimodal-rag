"""Upload -> extract -> chunk/embed -> hybrid search with provenance, end to end."""

from pathlib import Path

import pytest
from httpx import AsyncClient

from benchmarks.synthetic import write_report
from tests.integration.helpers import auth, register, upload

pytestmark = [pytest.mark.full_pipeline, pytest.mark.slow]


async def _search(client: AsyncClient, tok: dict[str, str], **body: object) -> dict:  # type: ignore[type-arg]
    r = await client.post("/api/v1/search", json=body, headers=auth(tok))
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


async def test_indexing_and_hybrid_search(client: AsyncClient, tmp_path: Path) -> None:
    a_pdf, a_truth = write_report("Northwind Analytics", 7, tmp_path / "a")
    b_pdf, _ = write_report("Blue Harbor Systems", 11, tmp_path / "b")
    owner = await register(client, "owner@example.com", "Org A")
    a = (await upload(client, owner, a_pdf, data={"title": "Northwind Analytics FY2024"})).json()
    b = (await upload(client, owner, b_pdf, data={"title": "Blue Harbor Systems FY2024"})).json()

    st = (
        await client.get(f"/api/v1/documents/{a['document_id']}/status", headers=auth(owner))
    ).json()
    stage = next(s for s in st["stages"] if s["stage"] == "chunk_embed")
    assert stage["status"] == "succeeded"
    assert stage["metrics"]["embedded"] > 0
    assert stage["metrics"]["parents"] > 0  # parent-child strategy

    # Table question -> expense table on page 2 of the scoped document.
    res = await _search(
        client,
        owner,
        query="Which operating expense category was largest?",
        document_ids=[a["document_id"]],
    )
    top = res["hits"][0]["citation"]
    assert top["document_id"] == a["document_id"]
    assert top["page"] == 2
    largest = a_truth["facts"]["largest_expense"]["category"]
    assert any(largest in h["context"] for h in res["hits"][:3])

    # Each mode works and returns provenance.
    for mode in ("dense", "lexical", "hybrid"):
        r = await _search(client, owner, query="total revenue", mode=mode, rerank=False)
        assert r["hits"], mode
        c = r["hits"][0]["citation"]
        assert c["page"] is not None
        assert c["document_title"]
        assert c["bbox"] is None or len(c["bbox"]) == 4

    # Comparison scope: both documents represented.
    cmp = await _search(
        client,
        owner,
        query="total revenue growth",
        document_ids=[a["document_id"], b["document_id"]],
        top_k=6,
    )
    assert {h["citation"]["document_id"] for h in cmp["hits"]} == {
        a["document_id"],
        b["document_id"],
    }

    # Filters: chunk type and page range.
    tables_only = await _search(client, owner, query="revenue", chunk_types=["table_rows"])
    assert tables_only["hits"]
    assert all(h["citation"]["chunk_type"] == "table_rows" for h in tables_only["hits"])
    p3 = await _search(client, owner, query="outlook", page_min=3, page_max=3)
    assert all(h["citation"]["page"] == 3 for h in p3["hits"])

    # Tenant isolation: another org finds nothing.
    other = await register(client, "x@example.com", "Org B")
    assert (await _search(client, other, query="total revenue"))["hits"] == []

    # Deleted documents disappear from search immediately.
    await client.delete(f"/api/v1/documents/{b['document_id']}", headers=auth(owner))
    after = await _search(client, owner, query="Blue Harbor revenue")
    assert all(h["citation"]["document_id"] != b["document_id"] for h in after["hits"])
