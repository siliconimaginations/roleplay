"""Tests for ProviderRegistry."""

from __future__ import annotations

import pytest

from roleplay.providers.base import CompletionRequest, CompletionResponse
from roleplay.providers.registry import ProviderRegistry


class MockProvider:
    @property
    def default_model(self) -> str:
        return "mock"

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        return CompletionResponse(text="mock response", model_used="mock")


class TestProviderRegistry:
    def test_register_and_get(self) -> None:
        reg = ProviderRegistry()
        p = MockProvider()
        reg.register("mock", p)
        assert reg.get("mock") is p

    def test_get_missing_raises_key_error(self) -> None:
        reg = ProviderRegistry()
        with pytest.raises(KeyError, match="not registered"):
            reg.get("nonexistent")

    def test_names_sorted(self) -> None:
        reg = ProviderRegistry()
        reg.register("gemini", MockProvider())
        reg.register("claude", MockProvider())
        assert reg.names() == ["claude", "gemini"]

    def test_contains(self) -> None:
        reg = ProviderRegistry()
        reg.register("gemini", MockProvider())
        assert "gemini" in reg
        assert "claude" not in reg

    def test_register_replaces(self) -> None:
        reg = ProviderRegistry()
        p1 = MockProvider()
        p2 = MockProvider()
        reg.register("x", p1)
        reg.register("x", p2)
        assert reg.get("x") is p2

    def test_error_message_lists_available(self) -> None:
        reg = ProviderRegistry()
        reg.register("gemini", MockProvider())
        with pytest.raises(KeyError, match="gemini"):
            reg.get("missing")
