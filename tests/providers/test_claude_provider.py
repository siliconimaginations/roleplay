"""Tests for ClaudeProvider — uses httpx mocks, no real API calls."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from roleplay.providers.base import (
    CompletionRequest,
    ProviderError,
    ProviderExhaustedError,
    ProviderRateLimitError,
)
from roleplay.providers.claude_provider import (
    _DEFAULT_MODELS,
    ClaudeProvider,
)

_HAIKU = "claude-haiku-4-5-20251001"


def _make_ok_response(text: str = "Hi!", model: str = _HAIKU) -> dict[str, Any]:
    return {
        "type": "message",
        "content": [{"type": "text", "text": text}],
        "usage": {"input_tokens": 8, "output_tokens": 3},
        "model": model,
    }


def _http_resp(
    status_code: int, body: dict[str, Any], headers: dict[str, str] | None = None
) -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        content=json.dumps(body).encode(),
        headers=headers or {},
    )


class TestClaudeProviderSuccess:
    @pytest.fixture
    def provider(self) -> ClaudeProvider:
        return ClaudeProvider(api_key="test-key")

    async def test_successful_completion(self, provider: ClaudeProvider) -> None:
        resp = _http_resp(200, _make_ok_response("Hello!"))
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=resp):
            result = await provider.complete(CompletionRequest(prompt="Hi"))
        assert result.text == "Hello!"
        assert result.prompt_tokens == 8
        assert result.completion_tokens == 3
        assert result.model_used == _HAIKU

    async def test_default_model_is_first(self, provider: ClaudeProvider) -> None:
        assert provider.default_model == _DEFAULT_MODELS[0]

    async def test_missing_api_key_raises(self) -> None:
        p = ClaudeProvider(api_key="")
        with pytest.raises(ProviderError, match="ANTHROPIC_API_KEY"):
            await p._call(_HAIKU, CompletionRequest(prompt="x"))

    async def test_500_raises_provider_error(self, provider: ClaudeProvider) -> None:
        resp = _http_resp(500, {"type": "error", "error": {"type": "server_error"}})
        with (
            patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=resp),
            pytest.raises(ProviderError, match="server error"),
        ):
            await provider._call(_HAIKU, CompletionRequest(prompt="x"))

    async def test_overloaded_error_raises_rate_limit(self, provider: ClaudeProvider) -> None:
        body: dict[str, Any] = {
            "type": "error",
            "error": {"type": "overloaded_error", "message": "overloaded"},
        }
        resp = _http_resp(200, body)
        with (
            patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=resp),
            pytest.raises(ProviderRateLimitError),
        ):
            await provider._call(_HAIKU, CompletionRequest(prompt="x"))

    async def test_other_api_error_in_body(self, provider: ClaudeProvider) -> None:
        body: dict[str, Any] = {
            "type": "error",
            "error": {"type": "invalid_request_error", "message": "bad"},
        }
        resp = _http_resp(200, body)
        with (
            patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=resp),
            pytest.raises(ProviderError, match="bad"),
        ):
            await provider._call(_HAIKU, CompletionRequest(prompt="x"))

    async def test_malformed_response_raises(self, provider: ClaudeProvider) -> None:
        resp = _http_resp(200, {"type": "message", "content": []})
        with (
            patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=resp),
            pytest.raises(ProviderError, match="Unexpected"),
        ):
            await provider._call(_HAIKU, CompletionRequest(prompt="x"))

    async def test_stop_sequences_in_body(self, provider: ClaudeProvider) -> None:
        resp = _http_resp(200, _make_ok_response())
        captured: list[dict[str, Any]] = []

        async def mock_post(url: str, **kwargs: object) -> httpx.Response:
            captured.append(kwargs.get("json", {}))  # type: ignore[arg-type]
            return resp

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, side_effect=mock_post):
            await provider.complete(CompletionRequest(prompt="x", stop_sequences=("END",)))
        assert "stop_sequences" in captured[0]

    async def test_temperature_1_omitted_from_body(self, provider: ClaudeProvider) -> None:
        resp = _http_resp(200, _make_ok_response())
        captured: list[dict[str, Any]] = []

        async def mock_post(url: str, **kwargs: object) -> httpx.Response:
            captured.append(kwargs.get("json", {}))  # type: ignore[arg-type]
            return resp

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, side_effect=mock_post):
            await provider.complete(CompletionRequest(prompt="x", temperature=1.0))
        assert "temperature" not in captured[0]


class TestClaudeProviderRateLimit:
    @pytest.fixture
    def provider(self) -> ClaudeProvider:
        return ClaudeProvider(models=("haiku", "sonnet"), api_key="test-key")

    async def test_429_rate_limit(self, provider: ClaudeProvider) -> None:
        resp = _http_resp(429, {"error": "rate limited"})
        with (
            patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=resp),
            pytest.raises(ProviderRateLimitError),
        ):
            await provider._call("haiku", CompletionRequest(prompt="x"))

    async def test_fallback_after_rate_limit(self, provider: ClaudeProvider) -> None:
        ok_resp = _http_resp(200, _make_ok_response("ok", "sonnet"))
        rl_resp = _http_resp(429, {"error": "quota"})

        async def mock_post(url: str, **kwargs: object) -> httpx.Response:
            body = kwargs.get("json", {})
            if isinstance(body, dict) and body.get("model") == "haiku":
                return rl_resp
            return ok_resp

        with (
            patch("httpx.AsyncClient.post", new_callable=AsyncMock, side_effect=mock_post),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            result = await provider.complete(CompletionRequest(prompt="x"))
        assert result.text == "ok"

    async def test_all_exhausted_raises(self, provider: ClaudeProvider) -> None:
        rl_resp = _http_resp(429, {"error": "quota"})
        with (
            patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=rl_resp),
            patch("asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(ProviderExhaustedError),
        ):
            await provider.complete(CompletionRequest(prompt="x"))
