"""Provider-neutral interfaces for generative models (ADR 0009).

`ChatModel` serves text generation (agents); `VisionModel` serves image understanding (charts,
figures). Both return structured JSON validated against a schema, plus token usage for cost
accounting. Implementations must raise `ProviderError` subclasses, never return made-up output.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Protocol


class ProviderError(Exception):
    """Base for provider failures."""


class ProviderUnavailableError(ProviderError):
    """No credentials/config for the provider."""


class ProviderResponseError(ProviderError):
    """The provider answered, but not with valid structured output."""


@dataclass(slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            self.input_tokens + other.input_tokens, self.output_tokens + other.output_tokens
        )


@dataclass(slots=True)
class ModelResult:
    data: dict[str, Any]
    model: str
    usage: Usage = field(default_factory=Usage)
    raw_text: str = ""


@dataclass(slots=True)
class Message:
    role: str  # "system" | "user" | "assistant"
    content: str


class VisionModel(Protocol):
    model_id: str

    def generate_json(
        self, *, image_png: bytes, prompt: str, schema: dict[str, Any]
    ) -> ModelResult: ...


class ChatModel(Protocol):
    model_id: str

    def generate_json(
        self, *, messages: list[Message], schema: dict[str, Any], temperature: float = 0.0
    ) -> ModelResult: ...

    def stream_text(
        self, *, messages: list[Message], temperature: float = 0.2
    ) -> Iterator[str]: ...
