import uuid
from pathlib import Path

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.database.models import Role, VersionStatus
from app.ingestion.pipeline import run_pipeline
from app.main import create_app
from app.storage import get_storage
from tests.fixtures import factories as f
from tests.integration.helpers import add_member, auth, org_of, register, upload


async def test_upload_processes_to_ready_with_stage_history(
    client: AsyncClient, tmp_path: Path
) -> None:
    owner = await register(client, "owner@a.com", "Org A")
    resp = await upload(client, owner, f.make_pdf(tmp_path / "report.pdf", pages=3))
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["deduplicated"] is False
    assert body["version_no"] == 1

    status = await client.get(
        f"/api/v1/documents/{body['document_id']}/status", headers=auth(owner)
    )
    assert status.status_code == 200
    st = status.json()
    assert st["status"] == "ready"
    assert [s["stage"] for s in st["stages"]] == ["fetch_verify"]
    assert st["stages"][0]["status"] == "succeeded"
    assert st["stages"][0]["metrics"]["sha256_verified"] is True

    detail = (
        await client.get(f"/api/v1/documents/{body['document_id']}", headers=auth(owner))
    ).json()
    assert detail["title"] == "report"
    assert detail["current_version"]["page_count"] == 3
    assert detail["current_version"]["mime_type"] == "application/pdf"


async def test_identical_upload_is_deduplicated(client: AsyncClient, tmp_path: Path) -> None:
    owner = await register(client, "owner@a.com")
    pdf = f.make_pdf(tmp_path / "a.pdf")
    first = (await upload(client, owner, pdf)).json()
    second = (await upload(client, owner, pdf, filename="renamed.pdf")).json()
    assert second["deduplicated"] is True
    assert second["version_id"] == first["version_id"]


async def test_same_content_in_two_orgs_is_not_shared(client: AsyncClient, tmp_path: Path) -> None:
    a = await register(client, "a@a.com", "Org A")
    b = await register(client, "b@b.com", "Org B")
    pdf = f.make_pdf(tmp_path / "a.pdf")
    ra = (await upload(client, a, pdf)).json()
    rb = (await upload(client, b, pdf)).json()
    assert rb["deduplicated"] is False
    assert rb["document_id"] != ra["document_id"]


async def test_new_version_of_existing_document(client: AsyncClient, tmp_path: Path) -> None:
    owner = await register(client, "owner@a.com")
    v1 = (await upload(client, owner, f.make_pdf(tmp_path / "v1.pdf", pages=1))).json()
    v2 = (
        await upload(
            client,
            owner,
            f.make_pdf(tmp_path / "v2.pdf", pages=2),
            data={"document_id": v1["document_id"]},
        )
    ).json()
    assert v2["document_id"] == v1["document_id"]
    assert v2["version_no"] == 2
    detail = (
        await client.get(f"/api/v1/documents/{v1['document_id']}", headers=auth(owner))
    ).json()
    assert [v["version_no"] for v in detail["versions"]] == [1, 2]
    assert detail["current_version"]["id"] == v2["version_id"]


async def test_list_pagination_and_search(client: AsyncClient, tmp_path: Path) -> None:
    owner = await register(client, "owner@a.com")
    for i in range(3):
        await upload(
            client,
            owner,
            f.make_pdf(tmp_path / f"doc{i}.pdf", pages=i + 1),
            data={"title": f"Annual report {i}"},
        )
    page1 = (await client.get("/api/v1/documents?limit=2", headers=auth(owner))).json()
    assert len(page1["items"]) == 2
    assert page1["next_cursor"]
    page2 = (
        await client.get(
            f"/api/v1/documents?limit=2&cursor={page1['next_cursor']}", headers=auth(owner)
        )
    ).json()
    assert len(page2["items"]) == 1
    assert page2["next_cursor"] is None
    seen = {d["id"] for d in page1["items"] + page2["items"]}
    assert len(seen) == 3
    hits = (await client.get("/api/v1/documents?q=report%201", headers=auth(owner))).json()
    assert [d["title"] for d in hits["items"]] == ["Annual report 1"]
    bad = await client.get("/api/v1/documents?cursor=@@@", headers=auth(owner))
    assert bad.status_code == 400


async def test_cross_tenant_access_is_404(client: AsyncClient, tmp_path: Path) -> None:
    a = await register(client, "a@a.com", "Org A")
    b = await register(client, "b@b.com", "Org B")
    doc = (await upload(client, a, f.make_pdf(tmp_path / "a.pdf"))).json()
    for path in ("", "/status"):
        r = await client.get(f"/api/v1/documents/{doc['document_id']}{path}", headers=auth(b))
        assert r.status_code == 404
    assert (
        await client.delete(f"/api/v1/documents/{doc['document_id']}", headers=auth(b))
    ).status_code == 404
    assert (await client.get("/api/v1/documents", headers=auth(b))).json()["items"] == []
    # Uploading a new version *into* another org's document must also fail.
    r = await upload(
        client, b, f.make_pdf(tmp_path / "b.pdf", pages=4), data={"document_id": doc["document_id"]}
    )
    assert r.status_code == 404


async def test_roles_enforced(client: AsyncClient, tmp_path: Path) -> None:
    owner = await register(client, "owner@a.com")
    org = org_of(owner)
    viewer = await add_member(client, org, "viewer@a.com", Role.VIEWER)
    member = await add_member(client, org, "member@a.com", Role.MEMBER)

    assert (await upload(client, viewer, f.make_pdf(tmp_path / "v.pdf"))).status_code == 403
    doc = (await upload(client, member, f.make_pdf(tmp_path / "m.pdf", pages=2))).json()
    assert (await client.get("/api/v1/documents", headers=auth(viewer))).status_code == 200
    assert (
        await client.delete(f"/api/v1/documents/{doc['document_id']}", headers=auth(member))
    ).status_code == 403
    assert (
        await client.delete(f"/api/v1/documents/{doc['document_id']}", headers=auth(owner))
    ).status_code == 204


async def test_delete_hides_purges_and_allows_reupload(client: AsyncClient, tmp_path: Path) -> None:
    owner = await register(client, "owner@a.com")
    pdf = f.make_pdf(tmp_path / "a.pdf")
    doc = (await upload(client, owner, pdf)).json()
    org = org_of(owner)
    prefix_key = f"org/{org}/docs/{doc['document_id']}/versions/{doc['version_id']}/original"
    assert get_storage().exists(prefix_key)

    assert (
        await client.delete(f"/api/v1/documents/{doc['document_id']}", headers=auth(owner))
    ).status_code == 204
    assert (
        await client.get(f"/api/v1/documents/{doc['document_id']}", headers=auth(owner))
    ).status_code == 404
    assert not get_storage().exists(prefix_key)  # purge task ran (eager in tests)

    again = (await upload(client, owner, pdf)).json()
    assert again["deduplicated"] is False
    assert again["document_id"] != doc["document_id"]


async def test_rejected_uploads_return_problem_json(client: AsyncClient, tmp_path: Path) -> None:
    owner = await register(client, "owner@a.com")
    r = await upload(client, owner, f.make_encrypted_pdf(tmp_path / "locked.pdf"))
    assert r.status_code == 415
    assert r.headers["content-type"] == "application/problem+json"
    assert "password" in r.json()["detail"]
    assert (await client.get("/api/v1/documents", headers=auth(owner))).json()["items"] == []


async def test_oversized_upload_rejected_while_streaming(
    settings: Settings, tmp_path: Path
) -> None:
    small = settings.model_copy(update={"max_upload_bytes": 64 * 1024})
    app: FastAPI = create_app(small)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        owner = await register(c, "owner@a.com")
        big = tmp_path / "big.png"
        f.make_png(
            big, size=(2000, 2000)
        )  # well over 64 KB uncompressed-ish noise-free? ensure size
        big.write_bytes(big.read_bytes() + b"\x00" * (2 * 1024 * 1024))
        r = await upload(c, owner, big)
    assert r.status_code == 413
    assert r.headers["content-type"] == "application/problem+json"


async def test_upload_requires_auth(client: AsyncClient, tmp_path: Path) -> None:
    with f.make_pdf(tmp_path / "a.pdf").open("rb") as fh:
        r = await client.post("/api/v1/documents/upload", files={"file": ("a.pdf", fh)})
    assert r.status_code == 401


async def test_pipeline_fails_cleanly_when_stored_blob_corrupted(
    client: AsyncClient, tmp_path: Path
) -> None:
    owner = await register(client, "owner@a.com")
    doc = (await upload(client, owner, f.make_pdf(tmp_path / "a.pdf"))).json()
    org = org_of(owner)
    key = f"org/{org}/docs/{doc['document_id']}/versions/{doc['version_id']}/original"
    get_storage().put_bytes(key, b"%PDF-tampered", "application/pdf")

    # Force a re-run by marking it failed (as a retry would find it).
    from sqlalchemy import update

    from app.database.models import DocumentVersion
    from app.database.session import sync_session_scope

    with sync_session_scope(org) as s:
        s.execute(update(DocumentVersion).values(status=VersionStatus.FAILED))
    assert run_pipeline(uuid.UUID(doc["version_id"]), org) is VersionStatus.FAILED

    st = (
        await client.get(f"/api/v1/documents/{doc['document_id']}/status", headers=auth(owner))
    ).json()
    assert st["status"] == "failed"
    assert "integrity" in st["error"]["message"]
    failed = [s for s in st["stages"] if s["status"] == "failed"]
    assert failed
    assert failed[-1]["error"]["trace"] is None  # expected errors carry no traceback


async def test_pipeline_is_idempotent_for_ready_versions(
    client: AsyncClient, tmp_path: Path
) -> None:
    owner = await register(client, "owner@a.com")
    doc = (await upload(client, owner, f.make_pdf(tmp_path / "a.pdf"))).json()
    org = org_of(owner)
    assert run_pipeline(uuid.UUID(doc["version_id"]), org) is VersionStatus.READY
    st = (
        await client.get(f"/api/v1/documents/{doc['document_id']}/status", headers=auth(owner))
    ).json()
    assert len(st["stages"]) == 1  # second run did no work


async def test_processing_history(client: AsyncClient, tmp_path: Path) -> None:
    owner = await register(client, "owner@a.com")
    await upload(client, owner, f.make_pdf(tmp_path / "a.pdf"))
    hist = (await client.get("/api/v1/processing/history", headers=auth(owner))).json()
    assert [j["stage"] for j in hist["items"]] == ["fetch_verify"]


async def test_readiness_checks_dependencies(client: AsyncClient) -> None:
    r = await client.get("/api/v1/health/ready")
    assert r.status_code == 200, r.text
    assert {k: v["ok"] for k, v in r.json()["checks"].items()} == {
        "database": True,
        "redis": True,
        "storage": True,
    }
