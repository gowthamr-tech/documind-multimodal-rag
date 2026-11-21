import re

from httpx import AsyncClient

from app import __version__

PROBLEM_KEYS = {"type", "title", "status", "detail", "instance", "request_id"}


async def test_liveness_returns_ok_and_version(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/health/live")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "version": __version__}


async def test_every_response_gets_a_generated_request_id(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/health/live")
    assert re.fullmatch(r"[0-9a-f]{32}", resp.headers["x-request-id"])


async def test_safe_caller_request_id_is_echoed(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/health/live", headers={"X-Request-ID": "trace-abc_123.4"})
    assert resp.headers["x-request-id"] == "trace-abc_123.4"


async def test_unsafe_caller_request_id_is_replaced(client: AsyncClient) -> None:
    hostile = "abc def<script>"  # spaces and angle brackets are outside the allowed charset
    resp = await client.get("/api/v1/health/live", headers={"X-Request-ID": hostile})
    assert resp.headers["x-request-id"] != hostile
    assert re.fullmatch(r"[0-9a-f]{32}", resp.headers["x-request-id"])


async def test_oversized_request_id_is_replaced(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/health/live", headers={"X-Request-ID": "a" * 129})
    assert len(resp.headers["x-request-id"]) == 32


async def test_unknown_route_returns_problem_json(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/does-not-exist")
    assert resp.status_code == 404
    assert resp.headers["content-type"] == "application/problem+json"
    body = resp.json()
    assert body.keys() >= PROBLEM_KEYS
    assert body["status"] == 404
    assert body["instance"] == "/api/v1/does-not-exist"
    assert body["request_id"] == resp.headers["x-request-id"]


async def test_domain_error_maps_to_problem_with_extra_fields(client: AsyncClient) -> None:
    resp = await client.get("/_probe/not-found")
    assert resp.status_code == 404
    body = resp.json()
    assert body["type"].endswith("/not-found")
    assert body["detail"] == "document 123 not found"
    assert body["resource"] == "document"


async def test_validation_error_lists_field_errors(client: AsyncClient) -> None:
    resp = await client.post("/_probe/validate", json={"name": "x", "count": "not-a-number"})
    assert resp.status_code == 422
    assert resp.headers["content-type"] == "application/problem+json"
    body = resp.json()
    assert body["type"].endswith("/validation-error")
    assert [e["loc"] for e in body["errors"]] == [["body", "count"]]


async def test_unhandled_exception_returns_500_without_leaking_internals(
    client: AsyncClient,
) -> None:
    resp = await client.get("/_probe/crash", headers={"X-Request-ID": "crash-1"})
    assert resp.status_code == 500
    assert resp.headers["content-type"] == "application/problem+json"
    assert resp.headers["x-request-id"] == "crash-1"
    assert "hunter2" not in resp.text
    assert "RuntimeError" not in resp.text
    body = resp.json()
    assert body["type"].endswith("/internal-error")
    assert body["request_id"] == "crash-1"


async def test_cors_preflight_allows_configured_origin(client: AsyncClient) -> None:
    resp = await client.options(
        "/api/v1/health/live",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"},
    )
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "http://localhost:3000"


async def test_cors_preflight_rejects_unknown_origin(client: AsyncClient) -> None:
    resp = await client.options(
        "/api/v1/health/live",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-origin" not in resp.headers
    assert "x-request-id" in resp.headers
