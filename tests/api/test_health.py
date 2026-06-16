"""Tests for the /health endpoint."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from httpx import AsyncClient


class TestHealth:
    async def test_health_returns_ok(self, client: AsyncClient) -> None:
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    async def test_health_no_auth_required(self, client_with_key: AsyncClient) -> None:
        """Health endpoint must not require API key."""
        resp = await client_with_key.get("/health")
        assert resp.status_code == 200
