"""Tests for API key authentication."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from httpx import AsyncClient


class TestAuth:
    async def test_no_key_configured_allows_any_request(self, client: AsyncClient) -> None:
        """Dev mode: all requests pass without auth header."""
        resp = await client.get("/sessions")
        assert resp.status_code == 200

    async def test_key_required_when_configured_missing_header(
        self, client_with_key: AsyncClient
    ) -> None:
        resp = await client_with_key.get("/sessions")
        assert resp.status_code == 401

    async def test_key_required_wrong_key(self, client_with_key: AsyncClient) -> None:
        resp = await client_with_key.get("/sessions", headers={"X-API-Key": "wrong-key"})
        assert resp.status_code == 403

    async def test_key_required_correct_key(self, client_with_key: AsyncClient) -> None:
        resp = await client_with_key.get("/sessions", headers={"X-API-Key": "test-secret-key"})
        assert resp.status_code == 200
