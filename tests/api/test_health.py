"""Tests for the /health endpoint."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from httpx import AsyncClient


class TestHealth:
    async def test_health_returns_ok(self, client: AsyncClient) -> None:
        resp = await client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["auth_required"] is False  # no ROLEPLAY_API_KEY in test env

    async def test_health_no_auth_required(self, client_with_key: AsyncClient) -> None:
        """Health endpoint must not require API key."""
        resp = await client_with_key.get("/health")
        assert resp.status_code == 200

    async def test_health_auth_required_flag(self, client: AsyncClient) -> None:
        """auth_required reflects whether ROLEPLAY_API_KEY is set."""
        import os
        from unittest.mock import patch

        with patch.dict(os.environ, {"ROLEPLAY_API_KEY": "secret"}):
            resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["auth_required"] is True
