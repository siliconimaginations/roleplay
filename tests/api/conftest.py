"""Shared fixtures for API tests."""

from __future__ import annotations

import os

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from roleplay.api.app import create_app
from roleplay.persistence.sqlite import SqlitePersistenceLayer

MINIMAL_YAML = """\
session_id: "test-session-001"
config:
  default_provider: mock
parties:
  - id: alice
    kind: person
    name: Alice
    system_prompt: "You are Alice."
  - id: bob
    kind: person
    name: Bob
    system_prompt: "You are Bob."
  - id: room
    kind: environment
    name: Test Room
    system_prompt: "A quiet room."
"""


async def _build_app_and_layer(tmp_path: object, api_key: str | None) -> tuple:
    import tempfile

    # Use /tmp so SQLite files don't land on the full /sessions disk
    _tmpdir = tempfile.mkdtemp(prefix="roleplay_test_", dir="/tmp")
    db_path = _tmpdir + "/test.db"
    old_env: dict[str, str | None] = {}

    old_env["ROLEPLAY_DB_PATH"] = os.environ.get("ROLEPLAY_DB_PATH")
    os.environ["ROLEPLAY_DB_PATH"] = db_path

    # Carefully manage ROLEPLAY_API_KEY
    old_env["ROLEPLAY_API_KEY"] = os.environ.get("ROLEPLAY_API_KEY")
    if api_key is not None:
        os.environ["ROLEPLAY_API_KEY"] = api_key
    else:
        os.environ.pop("ROLEPLAY_API_KEY", None)

    app = create_app()

    # Wire state directly — ASGITransport does not invoke the lifespan
    layer = SqlitePersistenceLayer(db_path)
    await layer.open()
    app.state.layer = layer
    app.state.runners = {}

    return app, layer, old_env


def _restore_env(old_env: dict[str, str | None]) -> None:
    for k, v in old_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


@pytest_asyncio.fixture
async def client(tmp_path: object) -> None:  # type: ignore[type-arg]
    """Async test client in dev mode (no API key required)."""
    app, layer, old_env = await _build_app_and_layer(tmp_path, api_key=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    await layer.close()
    _restore_env(old_env)


@pytest_asyncio.fixture
async def client_with_key(tmp_path: object) -> None:  # type: ignore[type-arg]
    """Async test client requiring X-API-Key: test-secret-key."""
    app, layer, old_env = await _build_app_and_layer(tmp_path, api_key="test-secret-key")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    await layer.close()
    _restore_env(old_env)
