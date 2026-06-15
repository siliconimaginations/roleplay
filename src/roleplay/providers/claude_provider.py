"""ClaudeProvider — calls Anthropic Claude with a model fallback chain."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field

import httpx

from roleplay.providers.base import (
    CompletionRequest,
    CompletionResponse,
    ProviderError,
    ProviderExhaustedError,
    ProviderRateLimitError,
)

logger = logging.getLogger(__name__)

_DEFAULT_MODELS = (
    "claude-haiku-4-5-20251001",
    "claude-sonnet-4-6",
    "claude-opus-4-8",
)

_RATE_LIMIT_INITIAL_WAIT = 5.0
_RATE_LIMIT_MAX_WAIT = 60.0
_RATE_LIMIT_MAX_RETRIES = 3


@dataclass
class ClaudeProvider:
    """Anthropic Claude provider with automatic model fallback.

    Rate-limit (HTTP 429 / overloaded_error) triggers exponential-backoff
    retry on the same model; after _RATE_LIMIT_MAX_RETRIES the model is
    skipped.  All models exhausted → ProviderExhaustedError.
    """

    models: tuple[str, ...] = _DEFAULT_MODELS
    api_key: str = field(default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY", ""))

    @property
    def default_model(self) -> str:
        return self.models[0]

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        attempted: list[str] = []
        for model in self.models:
            result = await self._try_model(model, request, attempted)
            if result is not None:
                return result
        raise ProviderExhaustedError(
            f"All Claude models exhausted: {attempted}",
            attempted_models=attempted,
        )

    async def _try_model(
        self,
        model: str,
        request: CompletionRequest,
        attempted: list[str],
    ) -> CompletionResponse | None:
        wait = _RATE_LIMIT_INITIAL_WAIT
        for attempt in range(_RATE_LIMIT_MAX_RETRIES + 1):
            try:
                return await self._call(model, request)
            except ProviderRateLimitError as exc:
                attempted.append(model)
                retry_after = exc.retry_after_seconds or wait
                if attempt >= _RATE_LIMIT_MAX_RETRIES:
                    logger.warning(
                        "Claude model %s rate-limited after %d retries, skipping.",
                        model,
                        _RATE_LIMIT_MAX_RETRIES,
                    )
                    return None
                logger.info(
                    "Claude rate limit on %s (attempt %d/%d), waiting %.1fs",
                    model,
                    attempt + 1,
                    _RATE_LIMIT_MAX_RETRIES,
                    retry_after,
                )
                await asyncio.sleep(retry_after)
                wait = min(wait * 2, _RATE_LIMIT_MAX_WAIT)
            except ProviderError:
                attempted.append(model)
                return None
        return None

    async def _call(self, model: str, request: CompletionRequest) -> CompletionResponse:
        if not self.api_key:
            raise ProviderError("ANTHROPIC_API_KEY not set")

        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        body: dict[str, object] = {
            "model": model,
            "max_tokens": request.max_output_tokens,
            "messages": [{"role": "user", "content": request.prompt}],
        }
        if request.temperature != 1.0:
            body["temperature"] = request.temperature
        if request.stop_sequences:
            body["stop_sequences"] = list(request.stop_sequences)

        start = time.monotonic()
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, headers=headers, json=body)

        elapsed = time.monotonic() - start
        logger.debug("Claude %s → HTTP %d in %.2fs", model, resp.status_code, elapsed)

        if resp.status_code == 429:
            retry_after: float | None = None
            try:
                ra = resp.headers.get("retry-after")
                if ra:
                    retry_after = float(ra)
            except ValueError:
                pass
            raise ProviderRateLimitError(
                f"Claude rate limit on {model}: {resp.text[:200]}",
                retry_after_seconds=retry_after,
            )

        if resp.status_code >= 500:
            raise ProviderError(
                f"Claude server error {resp.status_code} on {model}: {resp.text[:200]}"
            )

        if resp.status_code != 200:
            raise ProviderError(f"Claude error {resp.status_code} on {model}: {resp.text[:200]}")

        data = resp.json()

        # overloaded_error comes back as 200 with error type
        if data.get("type") == "error":
            err = data.get("error", {})
            if err.get("type") == "overloaded_error":
                raise ProviderRateLimitError(
                    f"Claude overloaded on {model}: {err.get('message', '')}"
                )
            raise ProviderError(f"Claude API error on {model}: {err.get('message', data)}")

        try:
            text = data["content"][0]["text"]
        except (KeyError, IndexError) as exc:
            raise ProviderError(f"Unexpected Claude response shape: {data}") from exc

        usage = data.get("usage", {})
        return CompletionResponse(
            text=text,
            prompt_tokens=usage.get("input_tokens", 0),
            completion_tokens=usage.get("output_tokens", 0),
            model_used=model,
        )
