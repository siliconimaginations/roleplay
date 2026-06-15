"""Provider protocol and typed request/response structs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters_schema: dict[str, object]


@dataclass(frozen=True)
class CompletionRequest:
    prompt: str
    tools: tuple[ToolDefinition, ...] = ()
    max_output_tokens: int = 2_048
    temperature: float = 0.9
    stop_sequences: tuple[str, ...] = ()


@dataclass(frozen=True)
class ToolCallResult:
    tool_name: str
    arguments: dict[str, object]
    result: str
    error: str | None = None


@dataclass(frozen=True)
class CompletionResponse:
    text: str
    tool_calls: tuple[ToolCallResult, ...] = ()
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model_used: str = ""


class ProviderError(RuntimeError):
    """Non-retryable provider failure."""


class ProviderRateLimitError(ProviderError):
    """Single-model rate limit hit."""

    def __init__(self, message: str = "", retry_after_seconds: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class ProviderExhaustedError(ProviderError):
    """All models in the fallback chain are rate-limited or failed."""

    def __init__(self, message: str = "", attempted_models: list[str] | None = None) -> None:
        super().__init__(message)
        self.attempted_models: list[str] = attempted_models or []


class Provider(Protocol):
    async def complete(self, request: CompletionRequest) -> CompletionResponse: ...

    @property
    def default_model(self) -> str: ...
