"""Tests for POST /sessions/generate endpoint."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

if TYPE_CHECKING:
    from httpx import AsyncClient


class TestGenerateSession:
    """Tests for POST /sessions/generate."""

    def _make_provider(self, yaml_text: str) -> MagicMock:
        """Return a mock Provider that echoes *yaml_text* as its completion."""
        from roleplay.providers.base import CompletionResponse

        provider = MagicMock()
        provider.complete = AsyncMock(return_value=CompletionResponse(text=yaml_text))
        return provider

    @pytest.mark.asyncio
    async def test_generate_returns_yaml(self, client: AsyncClient) -> None:
        """A non-empty prompt returns a YAML string in the response."""
        fake_yaml = "session_id: ai-gen\nparties: []\n"
        mock_provider = self._make_provider(fake_yaml)

        with (
            patch("roleplay.api.runner._build_registry") as mock_reg,
            patch(
                "roleplay.generate.generate_yaml_scenario",
                new_callable=AsyncMock,
                return_value=fake_yaml,
            ),
        ):
            registry = MagicMock()
            registry.__contains__ = MagicMock(return_value=True)
            registry.get = MagicMock(return_value=mock_provider)
            mock_reg.return_value = registry
            r = await client.post("/sessions/generate", content=b"two spies meet at a cafe")

        assert r.status_code == 200
        body = r.json()
        assert "yaml" in body
        assert isinstance(body["yaml"], str)

    @pytest.mark.asyncio
    async def test_empty_prompt_returns_422(self, client: AsyncClient) -> None:
        r = await client.post("/sessions/generate", content=b"   ")
        assert r.status_code == 422
        body = r.json()
        assert "error" in body

    @pytest.mark.asyncio
    async def test_invalid_utf8_returns_422(self, client: AsyncClient) -> None:
        r = await client.post(
            "/sessions/generate",
            content=b"\xff\xfe bad bytes",
            headers={"Content-Type": "text/plain"},
        )
        assert r.status_code == 422
        body = r.json()
        assert "error" in body

    @pytest.mark.asyncio
    async def test_provider_error_returns_422(self, client: AsyncClient) -> None:
        from roleplay.providers.base import ProviderError

        with (
            patch("roleplay.api.runner._build_registry") as mock_reg,
            patch(
                "roleplay.generate.generate_yaml_scenario",
                new_callable=AsyncMock,
                side_effect=ProviderError("quota exceeded"),
            ),
        ):
            registry = MagicMock()
            registry.__contains__ = MagicMock(return_value=True)
            registry.get = MagicMock(return_value=MagicMock())
            mock_reg.return_value = registry
            r = await client.post("/sessions/generate", content=b"a simple scenario")

        assert r.status_code == 422
        body = r.json()
        assert "error" in body
        assert "quota exceeded" in body["error"]
