"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.v1 import auth, documents, extraction, health, processing, query, search
from app.core.config import Settings, get_settings
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging
from app.core.middleware import BodySizeLimitMiddleware, RequestContextMiddleware
from app.core.rate_limit import rate_limit
from app.database.session import dispose_engines


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    from app.agents.service import close_checkpointer

    await close_checkpointer()
    redis = getattr(app.state, "redis", None)
    if redis is not None:
        await redis.aclose()
    await dispose_engines()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(level=settings.log_level, json_logs=settings.log_json)

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        openapi_url=f"{settings.api_prefix}/openapi.json",
        docs_url=None if settings.is_production else f"{settings.api_prefix}/docs",
        redoc_url=None,
        lifespan=_lifespan,
    )
    app.state.settings = settings

    # Global per-caller limit on every v1 route; specific routes add stricter buckets.
    v1 = APIRouter(
        prefix=settings.api_prefix,
        dependencies=[Depends(rate_limit("api", "rate_limit_per_minute"))],
    )
    v1.include_router(health.router)
    v1.include_router(auth.router)
    v1.include_router(documents.router)
    v1.include_router(extraction.router)
    v1.include_router(processing.router)
    v1.include_router(search.router)
    v1.include_router(query.router)
    app.include_router(v1)

    register_error_handlers(app)

    # Starlette runs the last-added middleware first, so RequestContextMiddleware wraps CORS
    # and every response — including CORS rejections — gets a request ID.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[settings.request_id_header],
    )
    # Multipart framing adds a little overhead on top of the file itself.
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_upload_bytes + 1024 * 1024)
    app.add_middleware(RequestContextMiddleware, header_name=settings.request_id_header)

    return app


app = create_app()
