import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.core.config import Environment, Settings
from app.main import create_app


def test_settings_read_prefixed_environment_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCUMIND_ENVIRONMENT", "staging")
    monkeypatch.setenv("DOCUMIND_JWT_SECRET", "s" * 40)
    monkeypatch.setenv("DOCUMIND_LOG_LEVEL", "debug")
    monkeypatch.setenv("DOCUMIND_CORS_ORIGINS", '["https://app.example.com"]')
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.environment is Environment.STAGING
    assert s.log_level == "DEBUG"
    assert s.cors_origins == ["https://app.example.com"]


def test_unprefixed_variables_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DOCUMIND_LOG_LEVEL", raising=False)
    monkeypatch.setenv("LOG_LEVEL", "ERROR")
    assert Settings(_env_file=None).log_level == "INFO"  # type: ignore[call-arg]


def test_invalid_log_level_is_rejected() -> None:
    with pytest.raises(ValidationError, match="invalid log level"):
        Settings(log_level="LOUD")


@pytest.mark.parametrize("prefix", ["api/v1", "/api/v1/", ""])
def test_invalid_api_prefix_is_rejected(prefix: str) -> None:
    with pytest.raises(ValidationError):
        Settings(api_prefix=prefix)


async def test_interactive_docs_disabled_in_production() -> None:
    prod = create_app(
        Settings(environment=Environment.PRODUCTION, log_level="WARNING", jwt_secret="p" * 40)
    )
    local = create_app(Settings(environment=Environment.LOCAL, log_level="WARNING"))
    async with AsyncClient(transport=ASGITransport(app=prod), base_url="http://t") as c:
        assert (await c.get("/api/v1/docs")).status_code == 404
        assert (await c.get("/api/v1/openapi.json")).status_code == 200
    async with AsyncClient(transport=ASGITransport(app=local), base_url="http://t") as c:
        assert (await c.get("/api/v1/docs")).status_code == 200


def test_production_requires_real_jwt_secret() -> None:
    with pytest.raises(ValidationError, match="DOCUMIND_JWT_SECRET must be set"):
        Settings(environment=Environment.PRODUCTION)


def test_short_jwt_secret_rejected() -> None:
    with pytest.raises(ValidationError, match="at least 32"):
        Settings(jwt_secret="too-short")


def test_s3_backend_requires_bucket_in_production() -> None:
    with pytest.raises(ValidationError, match="S3_BUCKET"):
        Settings(environment=Environment.PRODUCTION, jwt_secret="p" * 40, storage_backend="s3")
