"""ASGI middleware: request IDs, request/latency logging, and last-resort error handling."""

import json
import re
import time
import uuid

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import INTERNAL_ERROR_DETAIL, PROBLEM_JSON, problem_body
from app.core.logging import get_logger

# Accept caller-supplied IDs only if they are short and made of safe characters; otherwise
# generate one. This prevents log injection via the header.
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

logger = get_logger("app.request")


class BodySizeLimitMiddleware:
    """Rejects request bodies larger than `max_bytes` while they stream in.

    Starlette buffers multipart uploads to disk *before* the endpoint runs, so a size check in
    the endpoint alone would still let a client fill the disk. This counts bytes as they arrive
    (Content-Length is checked first but never trusted on its own).
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = dict(scope.get("headers", [])).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.max_bytes:
            await self._reject(scope, send)
            return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _BodyTooLargeError
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _BodyTooLargeError:
            await self._reject(scope, send)

    async def _reject(self, scope: Scope, send: Send) -> None:
        body = problem_body(
            status=413,
            problem_type="payload-too-large",
            title="Payload Too Large",
            detail=f"Request body exceeds {self.max_bytes // (1024 * 1024)} MB.",
            instance=scope["path"],
            request_id=scope.get("state", {}).get("request_id"),
        )
        payload = json.dumps(body).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", PROBLEM_JSON.encode("latin-1")),
                    (b"content-length", str(len(payload)).encode("latin-1")),
                    (b"connection", b"close"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": payload})


class _BodyTooLargeError(Exception):
    pass


class RequestContextMiddleware:
    """Assigns a request ID, binds it to the log context, echoes it, and logs each request.

    It also converts unhandled exceptions into a problem+json 500. This has to happen here, not
    in a FastAPI exception handler: Starlette serves `Exception` handlers from its outermost
    ServerErrorMiddleware, which runs outside this middleware and would drop the X-Request-ID
    header.
    """

    def __init__(self, app: ASGIApp, header_name: str = "X-Request-ID") -> None:
        self.app = app
        self._header_bytes = header_name.lower().encode("latin-1")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = dict(scope.get("headers", [])).get(self._header_bytes, b"").decode("latin-1")
        request_id = incoming if _SAFE_REQUEST_ID.fullmatch(incoming) else uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        status_code = 500
        response_started = False
        start = time.perf_counter()

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status_code = message["status"]
                headers = list(message.get("headers", []))
                headers.append((self._header_bytes, request_id.encode("latin-1")))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:
            logger.exception("unhandled_exception", exc_type=type(exc).__name__)
            if response_started:
                # Headers already sent (e.g. mid-stream); nothing safe to send — let it propagate.
                raise
            body = problem_body(
                status=500,
                problem_type="internal-error",
                title="Internal Server Error",
                detail=INTERNAL_ERROR_DETAIL,
                instance=scope["path"],
                request_id=request_id,
            )
            payload = json.dumps(body).encode("utf-8")
            await send_wrapper(
                {
                    "type": "http.response.start",
                    "status": 500,
                    "headers": [
                        (b"content-type", PROBLEM_JSON.encode("latin-1")),
                        (b"content-length", str(len(payload)).encode("latin-1")),
                    ],
                }
            )
            await send_wrapper({"type": "http.response.body", "body": payload})
        finally:
            logger.info(
                "http_request",
                method=scope["method"],
                path=scope["path"],
                status=status_code,
                duration_ms=round((time.perf_counter() - start) * 1000, 2),
            )
            structlog.contextvars.clear_contextvars()
