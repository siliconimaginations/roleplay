"""Mock provider for testing — returns deterministic canned responses."""

from __future__ import annotations

from roleplay.providers.base import CompletionRequest, CompletionResponse


class MockProvider:
    """A provider that returns a configurable canned response without any LLM call.

    Suitable for unit tests and CI — no API keys required.
    """

    def __init__(self, response_text: str = "Mock response.") -> None:
        self._response_text = response_text

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        return CompletionResponse(
            text=self._response_text,
            model_used="mock",
        )

    @property
    def default_model(self) -> str:
        return "mock"
