"""Health check endpoint."""

from __future__ import annotations

import os

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/health", tags=["health"])
async def health() -> JSONResponse:
    """Liveness check — no auth required.

    Returns ``auth_required: true`` when ``ROLEPLAY_API_KEY`` is set so the
    UI can decide whether to prompt for a key on startup.
    """
    auth_required = os.environ.get("ROLEPLAY_API_KEY") is not None
    return JSONResponse({"status": "ok", "auth_required": auth_required})
