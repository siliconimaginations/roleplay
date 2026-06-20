"""Tests for Session CRUD endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

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

    async def test_create_missing_environment_returns_422(self, client: AsyncClient) -> None:
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

    async def test_create_missing_parties_returns_422(self, client: AsyncClient) -> None:
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

    async def test_get_nonexistent_session_returns_404(self, client: AsyncClient) -> None:
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


class TestUnicodeEdgeCases:
    """Cover the UnicodeDecodeError path in create_session."""

    @pytest.mark.asyncio
    async def test_create_session_non_utf8_body_returns_422(self, client: AsyncClient) -> None:
        """POST /sessions with invalid UTF-8 bytes → 422."""
        r = await client.post(
            "/sessions",
            content=b"\xff\xfe invalid utf-8",
            headers={"Content-Type": "text/plain"},
        )
        assert r.status_code == 422
        assert "UTF-8" in r.json()["detail"]


class TestCreateSessionDbFailure:
    """Cover the 500 path when DB write fails."""

    @pytest.mark.asyncio
    async def test_create_session_db_error_returns_500(self, client: AsyncClient) -> None:
        """If create_session raises, return 500."""
        from unittest.mock import AsyncMock, patch

        with patch(
            "roleplay.persistence.sqlite.SqlitePersistenceLayer.create_session",
            new_callable=AsyncMock,
            side_effect=RuntimeError("disk full"),
        ):
            r = await client.post("/sessions", content=MINIMAL_YAML)
        assert r.status_code == 500
        assert "disk full" in r.json()["detail"]


class TestValidateSession:
    """Tests for POST /sessions/validate."""

    @pytest.mark.asyncio
    async def test_valid_yaml_returns_valid_true(self, client: AsyncClient) -> None:
        r = await client.post("/sessions/validate", content=MINIMAL_YAML)
        assert r.status_code == 200
        body = r.json()
        assert body["valid"] is True
        assert body["errors"] == []

    @pytest.mark.asyncio
    async def test_missing_environment_returns_errors(self, client: AsyncClient) -> None:
        """Scenario with no environment party → validation error."""
        yaml_no_env = b"""\
session_id: no-env
config:
  default_provider: mock
parties:
  - id: alice
    kind: person
    name: Alice
    persona:
      description: A person.
"""
        r = await client.post("/sessions/validate", content=yaml_no_env)
        assert r.status_code == 422
        body = r.json()
        assert body["valid"] is False
        assert len(body["errors"]) > 0

    @pytest.mark.asyncio
    async def test_empty_body_returns_error(self, client: AsyncClient) -> None:
        r = await client.post("/sessions/validate", content=b"   ")
        assert r.status_code == 422
        body = r.json()
        assert body["valid"] is False

    @pytest.mark.asyncio
    async def test_invalid_utf8_returns_error(self, client: AsyncClient) -> None:
        r = await client.post(
            "/sessions/validate",
            content=b"\xff\xfe bad bytes",
            headers={"Content-Type": "text/plain"},
        )
        assert r.status_code == 422
        body = r.json()
        assert body["valid"] is False
        assert any("UTF-8" in e for e in body["errors"])

    @pytest.mark.asyncio
    async def test_validate_does_not_create_session(self, client: AsyncClient) -> None:
        """Validate must not persist anything — sessions list stays empty."""
        await client.post("/sessions/validate", content=MINIMAL_YAML)
        r = await client.get("/sessions")
        assert r.status_code == 200
        assert r.json() == []


class TestExportSession:
    @pytest.mark.asyncio
    async def test_export_returns_200(self, client: AsyncClient) -> None:
        await client.post("/sessions", content=MINIMAL_YAML)
        r = await client.get("/sessions/test-session-001/export")
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_export_top_level_keys(self, client: AsyncClient) -> None:
        await client.post("/sessions", content=MINIMAL_YAML)
        body = (await client.get("/sessions/test-session-001/export")).json()
        assert body["export_version"] == "1"
        assert "exported_at" in body
        assert "session" in body
        assert "config" in body
        assert "parties" in body
        assert "environment" in body
        assert "episodes" in body

    @pytest.mark.asyncio
    async def test_export_session_metadata(self, client: AsyncClient) -> None:
        await client.post("/sessions", content=MINIMAL_YAML)
        body = (await client.get("/sessions/test-session-001/export")).json()
        sess = body["session"]
        assert sess["id"] == "test-session-001"
        assert "status" in sess
        assert sess["episode_count"] == 0  # no episodes run

    @pytest.mark.asyncio
    async def test_export_includes_parties_with_names(self, client: AsyncClient) -> None:
        await client.post("/sessions", content=MINIMAL_YAML)
        body = (await client.get("/sessions/test-session-001/export")).json()
        party_ids = {p["id"] for p in body["parties"]}
        assert "alice" in party_ids
        assert "bob" in party_ids
        # environment is in its own key, not parties list
        assert "room" not in party_ids

    @pytest.mark.asyncio
    async def test_export_environment_separate(self, client: AsyncClient) -> None:
        await client.post("/sessions", content=MINIMAL_YAML)
        body = (await client.get("/sessions/test-session-001/export")).json()
        assert body["environment"]["id"] == "room"

    @pytest.mark.asyncio
    async def test_export_episodes_empty_before_run(self, client: AsyncClient) -> None:
        await client.post("/sessions", content=MINIMAL_YAML)
        body = (await client.get("/sessions/test-session-001/export")).json()
        assert body["episodes"] == []

    @pytest.mark.asyncio
    async def test_export_nonexistent_session_returns_404(self, client: AsyncClient) -> None:
        r = await client.get("/sessions/does-not-exist/export")
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_export_config_fields(self, client: AsyncClient) -> None:
        await client.post("/sessions", content=MINIMAL_YAML)
        body = (await client.get("/sessions/test-session-001/export")).json()
        cfg = body["config"]
        assert cfg["default_provider"] == "mock"
        assert "context_window_episodes" in cfg
        assert "memory_max_entries" in cfg
        assert "environment_reactive" in cfg


class TestGetSessionYaml:
    @pytest.mark.asyncio
    async def test_yaml_returns_200(self, client: AsyncClient) -> None:
        await client.post("/sessions", content=MINIMAL_YAML)
        r = await client.get("/sessions/test-session-001/yaml")
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_yaml_returns_yaml_key(self, client: AsyncClient) -> None:
        await client.post("/sessions", content=MINIMAL_YAML)
        body = (await client.get("/sessions/test-session-001/yaml")).json()
        assert "yaml" in body
        assert isinstance(body["yaml"], str)

    @pytest.mark.asyncio
    async def test_yaml_contains_session_id(self, client: AsyncClient) -> None:
        await client.post("/sessions", content=MINIMAL_YAML)
        body = (await client.get("/sessions/test-session-001/yaml")).json()
        assert "test-session-001" in body["yaml"]

    @pytest.mark.asyncio
    async def test_yaml_contains_parties(self, client: AsyncClient) -> None:
        await client.post("/sessions", content=MINIMAL_YAML)
        body = (await client.get("/sessions/test-session-001/yaml")).json()
        assert "alice" in body["yaml"]
        assert "bob" in body["yaml"]

    @pytest.mark.asyncio
    async def test_yaml_nonexistent_session_returns_404(self, client: AsyncClient) -> None:
        r = await client.get("/sessions/does-not-exist/yaml")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Rich YAML fixture — parties have persona descriptions, named environments
# ---------------------------------------------------------------------------

_RICH_YAML = """\
session_id: "rich-session-001"
config:
  default_provider: mock
parties:
  - id: alice
    kind: person
    name: Alice
    persona:
      description: "A seasoned negotiator."
      goals:
        - "Reach a fair deal"
      traits:
        - "patient"
  - id: room
    kind: environment
    name: Conference Room
    persona:
      description: "A formal meeting space."
environments:
  - id: hallway
    name: Hallway
    description: "A long corridor outside the room."
"""


class TestGetSessionYamlRich:
    """Cover persona/environment branches in the YAML endpoint."""

    @pytest.mark.asyncio
    async def test_yaml_includes_party_persona(self, client: AsyncClient) -> None:
        await client.post("/sessions", content=_RICH_YAML)
        body = (await client.get("/sessions/rich-session-001/yaml")).json()
        assert "A seasoned negotiator" in body["yaml"]

    @pytest.mark.asyncio
    async def test_yaml_includes_environment_persona(self, client: AsyncClient) -> None:
        await client.post("/sessions", content=_RICH_YAML)
        body = (await client.get("/sessions/rich-session-001/yaml")).json()
        assert "A formal meeting space" in body["yaml"]

    @pytest.mark.asyncio
    async def test_yaml_includes_named_environments(self, client: AsyncClient) -> None:
        # Named environments are now persisted via migration 003. Closes #90.
        await client.post("/sessions", content=_RICH_YAML)
        body = (await client.get("/sessions/rich-session-001/yaml")).json()
        assert "hallway" in body["yaml"]
        assert "A long corridor" in body["yaml"]


class TestExportSessionRich:
    """Cover persona/environment branches in the export endpoint."""

    @pytest.mark.asyncio
    async def test_export_includes_party_persona(self, client: AsyncClient) -> None:
        await client.post("/sessions", content=_RICH_YAML)
        body = (await client.get("/sessions/rich-session-001/export")).json()
        alice = next(p for p in body["parties"] if p["id"] == "alice")
        assert alice["persona"]["description"] == "A seasoned negotiator."
        assert alice["persona"]["goals"] == ["Reach a fair deal"]

    @pytest.mark.asyncio
    async def test_export_includes_environment_persona(self, client: AsyncClient) -> None:
        await client.post("/sessions", content=_RICH_YAML)
        body = (await client.get("/sessions/rich-session-001/export")).json()
        assert body["environment"]["persona"]["description"] == "A formal meeting space."

    @pytest.mark.asyncio
    async def test_export_includes_named_environments(self, client: AsyncClient) -> None:
        # Named environments are now persisted via migration 003. Closes #90.
        await client.post("/sessions", content=_RICH_YAML)
        body = (await client.get("/sessions/rich-session-001/export")).json()
        assert "environments" in body
        env_ids = [e["id"] for e in body["environments"]]
        assert "hallway" in env_ids


class TestSessionYamlAndExportEnvironmentsBranch:
    """Cover the named-environments loop in YAML/export endpoints directly.

    Named environments aren't persisted by SqlitePersistenceLayer, so we
    can't reach this branch through the full API stack.  Instead we call the
    route functions directly with a fabricated SimulationState.
    """

    def _rich_state(self) -> object:
        from roleplay.core.environment import Environment, EnvironmentRegistry
        from roleplay.core.episode import NoopClock, RoundRobinScheduler, SimulationHistory
        from roleplay.core.party import make_environment, make_person
        from roleplay.core.simulation_state import SimulationConfig, SimulationState

        return SimulationState(
            config=SimulationConfig(session_id="direct-session", default_provider="mock"),
            parties={"alice": make_person("alice", "Alice", description="A negotiator.")},
            environment=make_environment("room", "Room", "A meeting room."),
            history=SimulationHistory(),
            scheduler=RoundRobinScheduler(),
            clock=NoopClock(),
            environments=EnvironmentRegistry(
                [Environment(id="hallway", name="Hallway", description="A corridor.")]
            ),
        )

    @pytest.mark.asyncio
    async def test_yaml_environments_branch(self, app_client: object) -> None:
        from unittest.mock import AsyncMock, MagicMock

        app, client = app_client  # type: ignore[misc]
        state = self._rich_state()

        fake_layer = MagicMock()
        fake_layer.load_session = AsyncMock(return_value=state)
        app.state.layer = fake_layer

        # Use the real HTTP client so auth/request wiring is correct
        r = await client.get("/sessions/direct-session/yaml")
        assert r.status_code == 200
        assert "hallway" in r.json()["yaml"]

    @pytest.mark.asyncio
    async def test_export_environments_branch(self, app_client: object) -> None:
        from unittest.mock import AsyncMock, MagicMock

        from roleplay.core.episode import SimulationHistory

        app, client = app_client  # type: ignore[misc]
        state = self._rich_state()
        history = SimulationHistory()

        fake_layer = MagicMock()
        fake_layer.load_session = AsyncMock(return_value=state)
        fake_layer.load_history = AsyncMock(return_value=history)
        app.state.layer = fake_layer

        r = await client.get("/sessions/direct-session/export")
        assert r.status_code == 200
        body = r.json()
        assert "environments" in body
        assert body["environments"][0]["id"] == "hallway"


_STATE_YAML = """\
session_id: "state-session-001"
config:
  default_provider: mock
parties:
  - id: alice
    kind: person
    name: Alice
    persona:
      description: "An explorer."
    state:
      mood: happy
      energy: 10
  - id: room
    kind: environment
    name: Room
    persona:
      description: "A dusty chamber."
    state:
      lit: true
"""


class TestYamlAndExportInitialStateBranch:
    """Cover the initial_state branches (p.state_snapshot() / env.state_snapshot())."""

    @pytest.mark.asyncio
    async def test_yaml_party_initial_state_included(self, client: AsyncClient) -> None:
        await client.post("/sessions", content=_STATE_YAML)
        body = (await client.get("/sessions/state-session-001/yaml")).json()
        # Party with state → initial_state key appears in YAML
        assert "mood" in body["yaml"] or "energy" in body["yaml"]

    @pytest.mark.asyncio
    async def test_export_party_initial_state_included(self, client: AsyncClient) -> None:
        await client.post("/sessions", content=_STATE_YAML)
        body = (await client.get("/sessions/state-session-001/export")).json()
        alice = next(p for p in body["parties"] if p["id"] == "alice")
        assert "initial_state" in alice

    @pytest.mark.asyncio
    async def test_export_environment_initial_state_included(self, client: AsyncClient) -> None:
        await client.post("/sessions", content=_STATE_YAML)
        body = (await client.get("/sessions/state-session-001/export")).json()
        assert "initial_state" in body["environment"]


class TestValidateSessionGenericException:
    """Cover the generic Exception branch in validate_session (lines 306-307)."""

    @pytest.mark.asyncio
    async def test_invalid_yaml_syntax_returns_422(self, client: AsyncClient) -> None:
        # Malformed YAML (not a ValidationError, triggers generic Exception branch)
        r = await client.post("/sessions/validate", content=b"key: {unclosed")
        assert r.status_code == 422
        body = r.json()
        assert body["valid"] is False
        assert any("Invalid YAML" in e for e in body["errors"])


class TestGetSessionHistory:
    """Cover the history endpoint loop (lines 327-353)."""

    @pytest.mark.asyncio
    async def test_history_with_completed_episodes(self, app_client: object) -> None:
        from unittest.mock import AsyncMock, MagicMock

        from roleplay.core.episode import Episode, SimulationHistory, Turn

        app, client = app_client  # type: ignore[misc]

        # Build a completed episode with turns
        ep = Episode(index=0, turns=[], simulated_time_start="t0")
        ep.turns.append(
            Turn(
                party_id="alice",
                index=0,
                output="Hello!",
                state_update_proposals={"mood": "happy"},
            )
        )
        ep.close("t1")
        ep.summary = "First episode summary."

        history = SimulationHistory()
        history.episodes.append(ep)

        fake_layer = MagicMock()
        fake_layer.load_history = AsyncMock(return_value=history)
        app.state.layer = fake_layer

        r = await client.get("/sessions/any-session/history")
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 1
        assert body[0]["episode"] == 0
        assert body[0]["done"] is True
        assert body[0]["summary"] == "First episode summary."
        assert body[0]["turns"][0]["party_id"] == "alice"
        assert body[0]["turns"][0]["output"] == "Hello!"
        assert body[0]["turns"][0]["state_update_proposals"] == {"mood": "happy"}

    @pytest.mark.asyncio
    async def test_history_not_found_returns_404(self, app_client: object) -> None:
        from unittest.mock import AsyncMock, MagicMock

        from roleplay.persistence import SessionNotFoundError

        app, client = app_client  # type: ignore[misc]
        fake_layer = MagicMock()
        fake_layer.load_history = AsyncMock(side_effect=SessionNotFoundError("gone"))
        app.state.layer = fake_layer

        r = await client.get("/sessions/missing/history")
        assert r.status_code == 404
