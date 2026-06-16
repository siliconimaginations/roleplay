"""Health check endpoint."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/health", tags=["health"])
async def health() -> JSONResponse:
    """Liveness check — no auth required."""
    return JSONResponse({"status": "ok"})
