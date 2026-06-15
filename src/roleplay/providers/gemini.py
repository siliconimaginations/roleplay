"""GeminiProvider — calls Google Gemini with a model fallback chain."""

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

# Default fallback chain — ordered cheapest/fastest first, then by RPD headroom.
#
# Only models with confirmed free-tier RPD quota are included.  Models with
# 0/0 RPD (e.g. gemini-2.0-flash, gemini-2.5-pro) are excluded: every call
# to them immediately returns a 429, burning all retry budget for nothing.
# Approximate free-tier RPD at time of writing (verify at
# https://aistudio.google.com/apikey → Rate Limits):
#
#   gemini-2.5-flash-lite   20 RPD   fastest, cheapest
#   gemini-2.5-flash        20 RPD   better quality
#   gemini-3.5-flash        20 RPD   newer generation
#   gemini-3.1-flash-lite  500 RPD   high-quota fallback  ← key tier
#   gemma-4-26b-a4b-it    1500 RPD   very generous quota
#   gemma-4-31b-it        1500 RPD   largest open model
_DEFAULT_MODELS = (
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash",
    "gemini-3.5-flash",
    "gemini-3.1-flash-lite",
    "gemma-4-26b-a4b-it",
    "gemma-4-31b-it",
)

# Maximum number of RPM retries before a model is added to the session skip list.
# Daily-quota (RPD) exhaustion skips immediately without retrying.
_RATE_LIMIT_MAX_RETRIES = 3


@dataclass
class GeminiProvider:
    """Google Gemini provider with automatic model fallback.

    On rate-limit (429 / RESOURCE_EXHAUSTED), retries the same model with
    exponential backoff up to _RATE_LIMIT_MAX_RETRIES times, then skips to
    the next model in the chain.  When all models are exhausted, raises
    ProviderExhaustedError.

    A session-level skip list (``_session_exhausted``) prevents retrying models
    that have already hit their daily quota within the current process run —
    they are skipped immediately on subsequent :meth:`complete` calls without
    burning any retry budget.
    """

    models: tuple[str, ...] = _DEFAULT_MODELS
    api_key: str = field(default_factory=lambda: os.environ.get("GEMINI_API_KEY", ""))
    _session: object = field(default=None, init=False, repr=False)
    _session_exhausted: set[str] = field(default_factory=set, init=False, repr=False)

    @property
    def default_model(self) -> str:
        return self.models[0]

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        attempted: list[str] = []
        for model in self.models:
            if model in self._session_exhausted:
                # Already hit daily quota this session — skip without retrying.
                attempted.append(model)
                continue
            result = await self._try_model(model, request, attempted)
            if result is not None:
                return result
        raise ProviderExhaustedError(
            f"All Gemini models exhausted: {attempted}",
            attempted_models=attempted,
        )

    async def _try_model(
        self,
        model: str,
        request: CompletionRequest,
        attempted: list[str],
    ) -> CompletionResponse | None:
        """Attempt a single model with rate-limit retries.  Returns None to skip.

        Two distinct rate-limit situations:

        * **RPM / per-minute throttle** — the API includes a ``retry-after``
          header.  We honour it and retry up to ``_RATE_LIMIT_MAX_RETRIES``
          times; the minute window resets and subsequent attempts may succeed.

        * **RPD / daily quota** — no ``retry-after`` header (or a value that
          would extend beyond the session).  Waiting does nothing; the quota
          resets at midnight.  We skip immediately to the next model and add
          this one to the session skip list.
        """
        for attempt in range(_RATE_LIMIT_MAX_RETRIES + 1):
            try:
                return await self._call(model, request)
            except ProviderRateLimitError as exc:
                attempted.append(model)
                retry_after = exc.retry_after_seconds

                # No retry-after hint → daily quota exhausted; skip immediately.
                # The quota resets at midnight so no amount of waiting helps.
                if retry_after is None:
                    self._session_exhausted.add(model)
                    logger.warning(
                        "Gemini model %s daily quota exhausted, added to session skip list.",
                        model,
                    )
                    return None

                # retry-after set → RPM throttle; honour the hint.
                if attempt >= _RATE_LIMIT_MAX_RETRIES:
                    self._session_exhausted.add(model)
                    logger.warning(
                        "Gemini model %s RPM-limited after %d retries, added to session skip list.",
                        model,
                        _RATE_LIMIT_MAX_RETRIES,
                    )
                    return None

                logger.info(
                    "Gemini RPM limit on %s (attempt %d/%d), waiting %.1fs",
                    model,
                    attempt + 1,
                    _RATE_LIMIT_MAX_RETRIES,
                    retry_after,
                )
                await asyncio.sleep(retry_after)
            except ProviderError:
                attempted.append(model)
                return None
        return None  # unreachable but satisfies mypy

    async def _call(self, model: str, request: CompletionRequest) -> CompletionResponse:
        """Make a single Gemini API call via httpx."""
        if not self.api_key:
            raise ProviderError("GEMINI_API_KEY not set")

        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={self.api_key}"
        )
        gen_config: dict[str, object] = {
            "maxOutputTokens": request.max_output_tokens,
            "temperature": request.temperature,
        }
        if request.stop_sequences:
            gen_config["stopSequences"] = list(request.stop_sequences)
        body: dict[str, object] = {
            "contents": [{"role": "user", "parts": [{"text": request.prompt}]}],
            "generationConfig": gen_config,
        }

        start = time.monotonic()
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, json=body)

        elapsed = time.monotonic() - start
        logger.debug("Gemini %s → HTTP %d in %.2fs", model, resp.status_code, elapsed)

        if resp.status_code == 429:
            retry_after: float | None = None
            try:
                ra = resp.headers.get("retry-after")
                if ra:
                    retry_after = float(ra)
            except ValueError:
                pass
            raise ProviderRateLimitError(
                f"Gemini rate limit on {model}: {resp.text[:200]}",
                retry_after_seconds=retry_after,
            )

        if resp.status_code >= 500:
            raise ProviderError(
                f"Gemini server error {resp.status_code} on {model}: {resp.text[:200]}"
            )

        if resp.status_code != 200:
            raise ProviderError(f"Gemini error {resp.status_code} on {model}: {resp.text[:200]}")

        data = resp.json()

        # Check for RESOURCE_EXHAUSTED in response body (some Gemini errors come as 200+error)
        if "error" in data:
            err = data["error"]
            status = err.get("status", "")
            if status in ("RESOURCE_EXHAUSTED",):
                raise ProviderRateLimitError(f"Gemini quota on {model}: {err.get('message', '')}")
            raise ProviderError(f"Gemini API error on {model}: {err.get('message', data)}")

        try:
            candidate = data["candidates"][0]
            text = candidate["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as exc:
            raise ProviderError(f"Unexpected Gemini response shape: {data}") from exc

        usage = data.get("usageMetadata", {})
        return CompletionResponse(
            text=text,
            prompt_tokens=usage.get("promptTokenCount", 0),
            completion_tokens=usage.get("candidatesTokenCount", 0),
            model_used=model,
        )
