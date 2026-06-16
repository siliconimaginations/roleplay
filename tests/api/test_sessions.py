"""Tests for Session CRUD endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from httpx import AsyncClient

from tests.api.conftest import MINIMAL_YAML

_YAML2 = MINIMAL_YAML.replace("test-session-001", "test-session-002")

_DUP_YAML = """\
session_id: "dup-001"
config:
  default_provider: mock
parties:
  - id: alice
    kind: person
    name: Alice
    system_prompt: "You are Alice."
  - id: room
    kind: environment
    name: Room
    system_prompt: "A room."
"""


class TestCreateSession:
    async def test_create_returns_201(self, client: AsyncClient) -> None:
        resp = await client.post("/sessions", content=MINIMAL_YAML)
        assert resp.status_code == 201
        body = resp.json()
        assert body["session_id"] == "test-session-001"
        assert body["status"] == "idle"
        assert body["episode_count"] == 0

    async def test_create_invalid_yaml_returns_422(self, client: AsyncClient) -> None:
        resp = await client.post("/sessions", content="parties: [not: valid: yaml: :")
        assert resp.status_code == 422

    async def test_create_empty_body_returns_422(self, client: AsyncClient) -> None:
        resp = await client.post("/sessions", content=b"")
        assert resp.status_code == 422

    async def test_create_missing_environment_returns_422(
        self, client: AsyncClient
    ) -> None:
        yaml_no_env = """\
session_id: "no-env"
config:
  default_provider: mock
parties:
  - id: alice
    kind: person
    name: Alice
    system_prompt: "You are Alice."
"""
        resp = await client.post("/sessions", content=yaml_no_env)
        assert resp.status_code == 422

    async def test_create_missing_parties_returns_422(
        self, client: AsyncClient
    ) -> None:
        yaml_no_parties = """\
session_id: "no-parties"
config:
  default_provider: mock
"""
        resp = await client.post("/sessions", content=yaml_no_parties)
        assert resp.status_code == 422


class TestListSessions:
    async def test_list_empty_returns_empty_list(self, client: AsyncClient) -> None:
        resp = await client.get("/sessions")
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_list_returns_created_session(self, client: AsyncClient) -> None:
        await client.post("/sessions", content=MINIMAL_YAML)
        resp = await client.get("/sessions")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["session_id"] == "test-session-001"

    async def test_list_multiple_sessions(self, client: AsyncClient) -> None:
        await client.post("/sessions", content=MINIMAL_YAML)
        await client.post("/sessions", content=_YAML2)
        resp = await client.get("/sessions")
        ids = [s["session_id"] for s in resp.json()]
        assert "test-session-001" in ids
        assert "test-session-002" in ids


class TestGetSession:
    async def test_get_session_returns_detail(self, client: AsyncClient) -> None:
        await client.post("/sessions", content=MINIMAL_YAML)
        resp = await client.get("/sessions/test-session-001")
        assert resp.status_code == 200
        body = resp.json()
        assert body["session_id"] == "test-session-001"
        assert "parties" in body
        assert "config" in body
        party_ids = {p["id"] for p in body["parties"]}
        assert "alice" in party_ids
        assert "bob" in party_ids

    async def test_get_nonexistent_session_returns_404(
        self, client: AsyncClient
    ) -> None:
        resp = await client.get("/sessions/nonexistent")
        assert resp.status_code == 404

    async def test_get_session_includes_environment(self, client: AsyncClient) -> None:
        await client.post("/sessions", content=MINIMAL_YAML)
        resp = await client.get("/sessions/test-session-001")
        body = resp.json()
        assert body["environment"] is not None
        assert body["environment"]["kind"] == "environment"

    async def test_get_session_status_is_idle(self, client: AsyncClient) -> None:
        await client.post("/sessions", content=MINIMAL_YAML)
        resp = await client.get("/sessions/test-session-001")
        assert resp.json()["status"] == "idle"


class TestDeleteSession:
    async def test_delete_session_returns_204(self, client: AsyncClient) -> None:
        await client.post("/sessions", content=MINIMAL_YAML)
        resp = await client.delete("/sessions/test-session-001")
        assert resp.status_code == 204

    async def test_delete_removes_from_list(self, client: AsyncClient) -> None:
        await client.post("/sessions", content=MINIMAL_YAML)
        await client.delete("/sessions/test-session-001")
        resp = await client.get("/sessions")
        assert resp.json() == []

    async def test_delete_nonexistent_returns_404(self, client: AsyncClient) -> None:
        resp = await client.delete("/sessions/ghost")
        assert resp.status_code == 404


class TestForkSession:
    async def test_fork_returns_201(self, client: AsyncClient) -> None:
        await client.post("/sessions", content=MINIMAL_YAML)
        resp = await client.post("/sessions/test-session-001/fork")
        assert resp.status_code == 201
        body = resp.json()
        assert body["session_id"] != "test-session-001"
        assert body["status"] == "idle"

    async def test_fork_creates_new_session_in_list(self, client: AsyncClient) -> None:
        await client.post("/sessions", content=MINIMAL_YAML)
        fork_resp = await client.post("/sessions/test-session-001/fork")
        new_id = fork_resp.json()["session_id"]
        resp = await client.get("/sessions")
        ids = [s["session_id"] for s in resp.json()]
        assert new_id in ids

    async def test_fork_nonexistent_returns_404(self, client: AsyncClient) -> None:
        resp = await client.post("/sessions/ghost/fork")
        assert resp.status_code == 404
