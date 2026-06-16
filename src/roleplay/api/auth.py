"""API key authentication dependency."""

from __future__ import annotations

import logging
import os

from fastapi import Header, HTTPException, status

logger = logging.getLogger(__name__)


def _get_configured_key() -> str | None:
    """Return the configured API key, or ``None`` in dev mode."""
    key = os.environ.get("ROLEPLAY_API_KEY")
    if key is None:
        logger.debug(
            "ROLEPLAY_API_KEY is not set — API auth is DISABLED. Set the env var before deploying."
        )
    return key


async def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """FastAPI dependency — validates the X-API-Key header.

    If ``ROLEPLAY_API_KEY`` env var is unset, auth is disabled (dev mode).
    """
    configured = _get_configured_key()
    if configured is None:
        return  # dev mode — no auth
    if x_api_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-API-Key header is required",
        )
    if x_api_key != configured:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key",
        )
