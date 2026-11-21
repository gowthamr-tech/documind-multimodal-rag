"""Gemini adapter against recorded response shapes (no network). This verifies request
construction, parsing, retries and error mapping — NOT live model behaviour."""

import json
from typing import Any

import httpx
import pytest

from app.providers.llm.base import Message, ProviderError, ProviderResponseError
from app.providers.llm.gemini import GeminiClient


def _ok(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidates": [{"content": {"parts": [{"text": json.dumps(payload)}]}}],
        "usageMetadata": {"promptTokenCount": 120, "candidatesTokenCount": 30},
    }


def _client(handler: Any) -> GeminiClient:
    return GeminiClient(
        "test-key", "gemini-test", max_retries=2, transport=httpx.MockTransport(handler)
    )


def test_vision_request_shape_and_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"], seen["key"], seen["body"] = (
            str(req.url),
            req.headers["x-goog-api-key"],
            json.loads(req.content),
        )
        return httpx.Response(200, json=_ok({"chart_type": "bar"}))

    res = _client(handler).generate_json(
        image_png=b"\x89PNGdata", prompt="read it", schema={"type": "object"}
    )
    assert seen["url"].endswith("/models/gemini-test:generateContent")
    assert seen["key"] == "test-key"
    parts = seen["body"]["contents"][0]["parts"]
    assert parts[0]["text"] == "read it"
    assert parts[1]["inline_data"]["mime_type"] == "image/png"
    assert seen["body"]["generationConfig"]["responseMimeType"] == "application/json"
    assert res.data == {"chart_type": "bar"}
    assert (res.usage.input_tokens, res.usage.output_tokens) == (120, 30)


def test_chat_system_instruction_and_roles() -> None:
    seen: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json=_ok({"ok": True}))

    _client(handler).generate_json(
        messages=[Message("system", "be exact"), Message("user", "q"), Message("assistant", "a")],
        schema={"type": "object"},
    )
    assert seen["body"]["systemInstruction"]["parts"][0]["text"] == "be exact"
    assert [c["role"] for c in seen["body"]["contents"]] == ["user", "model"]


def test_retries_on_429_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.providers.llm.gemini.time.sleep", lambda _s: None)
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429) if calls["n"] < 3 else httpx.Response(200, json=_ok({"x": 1}))

    assert _client(handler).generate_json(messages=[Message("user", "q")], schema={}).data == {
        "x": 1
    }
    assert calls["n"] == 3


def test_non_retryable_error_raises_immediately() -> None:
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, text="bad request")

    with pytest.raises(ProviderError, match="400"):
        _client(handler).generate_json(messages=[Message("user", "q")], schema={})
    assert calls["n"] == 1


def test_blocked_or_non_json_output_is_an_error_not_a_guess() -> None:
    blocked = _client(
        lambda r: httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}})
    )
    with pytest.raises(ProviderResponseError, match="SAFETY"):
        blocked.generate_json(messages=[Message("user", "q")], schema={})
    garbage = _client(
        lambda r: httpx.Response(
            200, json={"candidates": [{"content": {"parts": [{"text": "not json"}]}}]}
        )
    )
    with pytest.raises(ProviderResponseError, match="not valid JSON"):
        garbage.generate_json(messages=[Message("user", "q")], schema={})


def test_stream_text_parses_sse() -> None:
    sse = "".join(
        f"data: {json.dumps({'candidates': [{'content': {'parts': [{'text': t}]}}]})}\n\n"
        for t in ["Hel", "lo"]
    )

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.params["alt"] == "sse"
        return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})

    assert "".join(_client(handler).stream_text(messages=[Message("user", "hi")])) == "Hello"
