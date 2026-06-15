"""Tests for GeminiProvider — uses httpx mocks, no real API calls."""

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
from roleplay.providers.gemini import (
    _DEFAULT_MODELS,
    _RATE_LIMIT_MAX_RETRIES,
    GeminiProvider,
)

_FLASH_LITE = "gemini-2.5-flash-lite"


def _make_ok_response(text: str = "Hello!", model: str = _FLASH_LITE) -> dict[str, Any]:
    return {
        "candidates": [{"content": {"parts": [{"text": text}]}}],
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5},
    }


def _make_error_response(status: str, message: str = "quota") -> dict[str, Any]:
    return {"error": {"status": status, "message": message}}


def _http_resp(
    status_code: int, body: dict[str, Any], headers: dict[str, str] | None = None
) -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        content=json.dumps(body).encode(),
        headers=headers or {},
    )


class TestGeminiProviderSuccess:
    @pytest.fixture
    def provider(self) -> GeminiProvider:
        return GeminiProvider(api_key="test-key")

    async def test_successful_completion(self, provider: GeminiProvider) -> None:
        resp = _http_resp(200, _make_ok_response("Hi there"))
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=resp):
            result = await provider.complete(CompletionRequest(prompt="Say hi"))
        assert result.text == "Hi there"
        assert result.prompt_tokens == 10
        assert result.completion_tokens == 5
        assert result.model_used == _FLASH_LITE

    async def test_default_model_is_first(self, provider: GeminiProvider) -> None:
        assert provider.default_model == _DEFAULT_MODELS[0]

    async def test_stop_sequences_included_in_body(self, provider: GeminiProvider) -> None:
        resp = _http_resp(200, _make_ok_response())
        captured: list[dict[str, Any]] = []

        async def mock_post(url: str, **kwargs: object) -> httpx.Response:
            captured.append(kwargs.get("json", {}))  # type: ignore[arg-type]
            return resp

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, side_effect=mock_post):
            await provider.complete(CompletionRequest(prompt="x", stop_sequences=("STOP", "END")))
        body = captured[0]
        assert "stopSequences" in body["generationConfig"]
        assert "STOP" in body["generationConfig"]["stopSequences"]

    async def test_missing_api_key_raises(self) -> None:
        provider = GeminiProvider(api_key="")
        with pytest.raises(ProviderError, match="GEMINI_API_KEY"):
            await provider._call(_FLASH_LITE, CompletionRequest(prompt="x"))

    async def test_500_raises_provider_error(self, provider: GeminiProvider) -> None:
        resp = _http_resp(500, {"error": "server error"})
        with (
            patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=resp),
            pytest.raises(ProviderError, match="server error"),
        ):
            await provider._call(_FLASH_LITE, CompletionRequest(prompt="x"))

    async def test_non_200_non_429_raises(self, provider: GeminiProvider) -> None:
        resp = _http_resp(403, {"error": "forbidden"})
        with (
            patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=resp),
            pytest.raises(ProviderError, match="403"),
        ):
            await provider._call(_FLASH_LITE, CompletionRequest(prompt="x"))

    async def test_malformed_response_raises(self, provider: GeminiProvider) -> None:
        resp = _http_resp(200, {"candidates": []})
        with (
            patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=resp),
            pytest.raises(ProviderError, match="Unexpected"),
        ):
            await provider._call(_FLASH_LITE, CompletionRequest(prompt="x"))

    async def test_resource_exhausted_in_200_body(self, provider: GeminiProvider) -> None:
        resp = _http_resp(200, _make_error_response("RESOURCE_EXHAUSTED"))
        with (
            patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=resp),
            pytest.raises(ProviderRateLimitError),
        ):
            await provider._call(_FLASH_LITE, CompletionRequest(prompt="x"))

    async def test_other_error_in_body_raises_provider_error(
        self, provider: GeminiProvider
    ) -> None:
        resp = _http_resp(200, _make_error_response("INVALID_ARGUMENT", "bad prompt"))
        with (
            patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=resp),
            pytest.raises(ProviderError, match="bad prompt"),
        ):
            await provider._call(_FLASH_LITE, CompletionRequest(prompt="x"))


class TestGeminiProviderRateLimit:
    @pytest.fixture
    def provider(self) -> GeminiProvider:
        return GeminiProvider(models=("model-a", "model-b"), api_key="test-key")

    async def test_429_triggers_rate_limit_error(self, provider: GeminiProvider) -> None:
        resp = _http_resp(429, {"error": "quota"})
        with (
            patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=resp),
            pytest.raises(ProviderRateLimitError),
        ):
            await provider._call("model-a", CompletionRequest(prompt="x"))

    async def test_rate_limit_retries_then_skips(self, provider: GeminiProvider) -> None:
        """After max retries on model-a, falls through to model-b."""
        ok_resp = _http_resp(200, _make_ok_response("ok", "model-b"))
        rl_resp = _http_resp(429, {"error": "quota"})
        call_count = 0

        async def mock_post(url: str, **kwargs: object) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return rl_resp if "model-a" in url else ok_resp

        with (
            patch("httpx.AsyncClient.post", new_callable=AsyncMock, side_effect=mock_post),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            result = await provider.complete(CompletionRequest(prompt="x"))

        assert result.text == "ok"
        assert result.model_used == "model-b"
        assert call_count == _RATE_LIMIT_MAX_RETRIES + 1 + 1

    async def test_all_models_exhausted_raises(self, provider: GeminiProvider) -> None:
        rl_resp = _http_resp(429, {"error": "quota"})
        with (
            patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=rl_resp),
            patch("asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(ProviderExhaustedError, match="exhausted"),
        ):
            await provider.complete(CompletionRequest(prompt="x"))

    async def test_retry_after_header_used(self, provider: GeminiProvider) -> None:
        """retry-after header value is passed to ProviderRateLimitError."""
        rl_resp = httpx.Response(
            429, content=json.dumps({"error": "quota"}).encode(), headers={"retry-after": "30"}
        )
        ok_resp = _http_resp(200, _make_ok_response("ok", "model-b"))
        sleeps: list[float] = []
        call_num = 0

        async def fake_sleep(secs: float) -> None:
            sleeps.append(secs)

        async def mock_post(url: str, **kwargs: object) -> httpx.Response:
            nonlocal call_num
            call_num += 1
            if "model-a" in url and call_num <= _RATE_LIMIT_MAX_RETRIES:
                return rl_resp
            return ok_resp

        with (
            patch("httpx.AsyncClient.post", new_callable=AsyncMock, side_effect=mock_post),
            patch("asyncio.sleep", new_callable=AsyncMock, side_effect=fake_sleep),
        ):
            await provider.complete(CompletionRequest(prompt="x"))

        assert sleeps[0] == 30.0


class TestGeminiProviderFallback:
    async def test_provider_error_skips_model(self) -> None:
        """Non-rate-limit error on model-a should skip to fallback."""
        provider = GeminiProvider(models=("bad-model", _FLASH_LITE), api_key="key")
        ok_resp = _http_resp(200, _make_ok_response("fallback ok", _FLASH_LITE))

        async def mock_post(url: str, **kwargs: object) -> httpx.Response:
            return _http_resp(400, {"error": "model not found"}) if "bad-model" in url else ok_resp

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, side_effect=mock_post):
            result = await provider.complete(CompletionRequest(prompt="x"))
        assert result.model_used == _FLASH_LITE

    async def test_custom_models_tuple(self) -> None:
        provider = GeminiProvider(models=("my-model",), api_key="key")
        assert provider.default_model == "my-model"
