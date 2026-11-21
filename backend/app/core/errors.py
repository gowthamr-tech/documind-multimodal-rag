"""RFC 9457 problem+json error responses.

All errors leave the API in one envelope:
    {type, title, status, detail, instance, request_id, errors?}
Unhandled exceptions are converted to a 500 problem in `RequestContextMiddleware` (so the response
still carries X-Request-ID); they are logged with a traceback and never leak internals to clients.
"""

from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger

PROBLEM_JSON = "application/problem+json"
PROBLEM_TYPE_BASE = "https://documind.ai/problems/"

logger = get_logger("app.errors")


class AppError(Exception):
    """Base class for expected, client-facing domain errors."""

    status_code: int = HTTPStatus.BAD_REQUEST
    problem_type: str = "bad-request"
    title: str = "Bad Request"

    def __init__(
        self,
        detail: str,
        *,
        extra: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.extra = extra or {}
        self.headers = headers


class NotFoundError(AppError):
    status_code = HTTPStatus.NOT_FOUND
    problem_type = "not-found"
    title = "Not Found"


class ConflictError(AppError):
    status_code = HTTPStatus.CONFLICT
    problem_type = "conflict"
    title = "Conflict"


def problem_body(
    *,
    status: int,
    problem_type: str,
    title: str,
    detail: str | None,
    instance: str,
    request_id: str | None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "type": PROBLEM_TYPE_BASE + problem_type,
        "title": title,
        "status": status,
        "detail": detail,
        "instance": instance,
        "request_id": request_id,
    }
    if extra:
        body.update(extra)
    return body


INTERNAL_ERROR_DETAIL = "An unexpected error occurred. Quote the request_id when reporting it."


def problem_response(
    request: Request,
    *,
    status: int,
    problem_type: str,
    title: str,
    detail: str | None = None,
    extra: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body = problem_body(
        status=status,
        problem_type=problem_type,
        title=title,
        detail=detail,
        instance=request.url.path,
        request_id=getattr(request.state, "request_id", None),
        extra=extra,
    )
    return JSONResponse(
        jsonable_encoder(body), status_code=status, media_type=PROBLEM_JSON, headers=headers
    )


async def _app_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)  # noqa: S101 - narrowing for the type checker
    return problem_response(
        request,
        status=int(exc.status_code),
        problem_type=exc.problem_type,
        title=exc.title,
        detail=exc.detail,
        extra=exc.extra,
        headers=exc.headers,
    )


async def _http_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)  # noqa: S101
    try:
        title = HTTPStatus(exc.status_code).phrase
    except ValueError:
        title = "Error"
    return problem_response(
        request,
        status=exc.status_code,
        problem_type=title.lower().replace(" ", "-"),
        title=title,
        detail=str(exc.detail) if exc.detail else None,
        headers=getattr(exc, "headers", None),
    )


async def _validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)  # noqa: S101
    errors = [
        {"loc": list(err.get("loc", ())), "msg": err.get("msg"), "type": err.get("type")}
        for err in exc.errors()
    ]
    return problem_response(
        request,
        status=HTTPStatus.UNPROCESSABLE_ENTITY,
        problem_type="validation-error",
        title="Validation Error",
        detail="The request is invalid.",
        extra={"errors": errors},
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
