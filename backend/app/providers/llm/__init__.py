from app.core.config import Settings
from app.providers.llm.base import (
    ChatModel,
    Message,
    ModelResult,
    ProviderError,
    ProviderResponseError,
    ProviderUnavailableError,
    Usage,
    VisionModel,
)


def get_vision_model(settings: Settings) -> VisionModel | None:
    """The configured vision model, or None when no provider credentials are set."""
    if settings.gemini_api_key is None:
        return None
    from app.providers.llm.gemini import GeminiClient

    return GeminiClient(
        settings.gemini_api_key.get_secret_value(), settings.vlm_model, settings.llm_timeout_seconds
    )


def get_chat_model(settings: Settings) -> ChatModel | None:
    if settings.gemini_api_key is None:
        return None
    from app.providers.llm.gemini import GeminiClient

    return GeminiClient(
        settings.gemini_api_key.get_secret_value(), settings.llm_model, settings.llm_timeout_seconds
    )


__all__ = [
    "ChatModel",
    "Message",
    "ModelResult",
    "ProviderError",
    "ProviderResponseError",
    "ProviderUnavailableError",
    "Usage",
    "VisionModel",
    "get_chat_model",
    "get_vision_model",
]
