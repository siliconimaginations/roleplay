"""Tests for cli.py — all 7 commands + CliObserverHook + _make_registry."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from roleplay.cli import (
    CliObserverHook,
    StreamPrinter,
    _make_registry,
    app,
)

runner = CliRunner()

MINIMAL_YAML = """\
session_id: test-cli-session

config:
  default_provider: mock
  max_episodes: 1

parties:
  - id: alice
    kind: person
    name: Alice
    persona:
      description: A test character.

  - id: room
    kind: environment
    name: Test Room
    persona:
      description: A plain white room.
"""


def _write_yaml(tmp_path: Path, content: str = MINIMAL_YAML) -> Path:
    p = tmp_path / "scenario.yaml"
    p.write_text(content)
    return p


def _make_state() -> MagicMock:
    from roleplay.core.party import make_environment, make_person

    state = MagicMock()
    state.config.session_id = "test-session"
    state.config.default_provider = "mock"
    state.history.completed_episodes.return_value = []
    state.parties = {"alice": make_person("alice", "Alice", description="Test")}
    state.environment = make_environment("env", "Env", "A room")
    return state


# ---------------------------------------------------------------------------
# _make_registry
# ---------------------------------------------------------------------------


class TestMakeRegistry:
    def test_registers_gemini(self) -> None:
        assert "gemini" in _make_registry()

    def test_registers_claude(self) -> None:
        assert "claude" in _make_registry()

    def test_registers_mock(self) -> None:
        assert "mock" in _make_registry()

    def test_get_mock_returns_provider(self) -> None:
        assert _make_registry().get("mock").default_model == "mock"

    def test_unknown_provider_raises_key_error(self) -> None:
        with pytest.raises(KeyError, match="unknown-xyz"):
            _make_registry().get("unknown-xyz")


# ---------------------------------------------------------------------------
# StreamPrinter
# ---------------------------------------------------------------------------


class TestStreamPrinter:
    def test_episode_header_contains_episode_number(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        StreamPrinter().print_episode_header(0, "Day 1")
        assert "Episode 1" in capsys.readouterr().out

    def test_episode_header_with_total(self, capsys: pytest.CaptureFixture[str]) -> None:
        StreamPrinter().print_episode_header(2, "", total=5)
        assert "/ 5" in capsys.readouterr().out

    def test_turn_output_shows_party_and_text(self, capsys: pytest.CaptureFixture[str]) -> None:
        StreamPrinter().print_turn("alice", "Hello world!")
        out = capsys.readouterr().out
        assert "alice" in out
        assert "Hello world!" in out

    def test_turn_output_shows_state_changes(self, capsys: pytest.CaptureFixture[str]) -> None:
        StreamPrinter().print_turn("bob", "Fine.", state_changes="mood=happy")
        out = capsys.readouterr().out
        assert "mood=happy" in out

    def test_episode_footer_shown(self, capsys: pytest.CaptureFixture[str]) -> None:
        StreamPrinter().print_episode_footer(0, tokens=150, memories=2, simulated_time_end="t1", wall_secs=1.5)
        out = capsys.readouterr().out
        assert "150" in out


# ---------------------------------------------------------------------------
# CliObserverHook
# ---------------------------------------------------------------------------


def _make_hook(max_episodes: int = 3, interactive: bool = False) -> CliObserverHook:
    layer = AsyncMock()
    layer.save_episode = AsyncMock()
    layer.save_state = AsyncMock()
    layer.checkpoint = AsyncMock()
    return CliObserverHook(
        StreamPrinter(),
        interactive=interactive,
        max_episodes=max_episodes,
        persistence=layer,
        session_id="test-session",
    )


class TestCliObserverHook:
    @pytest.mark.asyncio
    async def test_before_episode_returns_continue(self) -> None:
        hook = _make_hook()
        directive = await hook.before_episode(_make_state(), 0)
        assert not directive.is_halt

    @pytest.mark.asyncio
    async def test_before_episode_shows_max_in_header(self, capsys: pytest.CaptureFixture[str]) -> None:
        hook = _make_hook(max_episodes=2)
        directive = await hook.before_episode(_make_state(), 0)
        assert not directive.is_halt  # halting is engine's job; hook only shows progress
        assert "/ 2" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_after_turn_continue(self) -> None:
        hook = _make_hook()
        turn = MagicMock()
        turn.party_id = "alice"
        turn.output = "Hello"
        turn.state_update_proposals = {"mood": "happy"}
        directive = await hook.after_turn(_make_state(), turn)
        assert not directive.is_halt

    @pytest.mark.asyncio
    async def test_after_turn_environment_party(self) -> None:
        hook = _make_hook()
        turn = MagicMock()
        turn.party_id = "env"  # matches environment id
        turn.output = "Wind picks up."
        turn.state_update_proposals = {}
        directive = await hook.after_turn(_make_state(), turn)
        assert not directive.is_halt

    @pytest.mark.asyncio
    async def test_after_turn_unknown_party(self) -> None:
        hook = _make_hook()
        turn = MagicMock()
        turn.party_id = "ghost"
        turn.output = "Boo"
        turn.state_update_proposals = {}
        directive = await hook.after_turn(_make_state(), turn)
        assert not directive.is_halt

    @pytest.mark.asyncio
    async def test_after_episode_saves_episode(self) -> None:
        from roleplay.core.episode import Episode, Turn as CoreTurn

        hook = _make_hook()
        ep = Episode(index=0, turns=[], simulated_time_start="t0")
        ep.add_turn(CoreTurn(party_id="alice", index=0, output="Hi",
                             prompt_tokens=10, completion_tokens=5))
        ep.close("t1")
        await hook.after_episode(_make_state(), ep)
        hook._persistence.save_episode.assert_called_once()  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_after_episode_non_episode_object(self) -> None:
        # after_episode with a non-Episode obj should not crash
        hook = _make_hook()
        directive = await hook.after_episode(_make_state(), object())
        assert not directive.is_halt

    @pytest.mark.asyncio
    async def test_after_episode_no_persistence(self) -> None:
        from roleplay.core.episode import Episode

        hook = CliObserverHook(StreamPrinter(), interactive=False, session_id="s")
        ep = Episode(index=0, turns=[], simulated_time_start="t0")
        ep.close("t1")
        directive = await hook.after_episode(_make_state(), ep)
        assert not directive.is_halt

    @pytest.mark.asyncio
    async def test_pause_flag_halts_on_quit(self) -> None:
        hook = _make_hook()
        hook._pause_flag.set()
        # _run_intervention will read from stdin — mock it to return None (quit)
        with patch.object(hook, "_run_intervention", new=AsyncMock(return_value=None)):
            directive = await hook.before_episode(_make_state(), 0)
        assert directive.is_halt

    @pytest.mark.asyncio
    async def test_pause_flag_injects_on_continue(self) -> None:
        from roleplay.engine.observer import InjectionPayload

        hook = _make_hook()
        hook._pause_flag.set()
        payload = InjectionPayload(context_override="extra context")
        with patch.object(hook, "_run_intervention", new=AsyncMock(return_value=payload)):
            directive = await hook.before_episode(_make_state(), 0)
        assert directive.is_inject


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------


def _mock_layer() -> AsyncMock:
    layer = AsyncMock()
    layer.create_session = AsyncMock()
    layer.close = AsyncMock()
    return layer


def _mock_engine() -> MagicMock:
    engine = MagicMock()
    engine.run = AsyncMock()
    return engine


class TestRunCommand:
    def test_missing_scenario_exits_1(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["run", str(tmp_path / "no_such.yaml")])
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_invalid_yaml_exits_1(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_text("parties: []\nenvironment: null\n")
        result = runner.invoke(app, ["run", str(bad)])
        assert result.exit_code == 1

    def test_unknown_provider_exits_1(self, tmp_path: Path) -> None:
        p = _write_yaml(tmp_path, MINIMAL_YAML.replace("provider: mock", "provider: no-exist"))
        result = runner.invoke(app, ["run", str(p)])
        assert result.exit_code == 1

    def test_run_succeeds_with_mock_provider(self, tmp_path: Path) -> None:
        p = _write_yaml(tmp_path)
        with (
            patch("roleplay.engine.engine.SimulationEngine") as eng_cls,
            patch("roleplay.cli._open_layer") as layer_fac,
        ):
            layer_fac.return_value = _mock_layer()
            eng_cls.return_value = _mock_engine()
            result = runner.invoke(app, ["run", str(p), "--db", str(tmp_path / "t.db")])
        assert result.exit_code == 0, result.output

    def test_run_with_episodes_flag(self, tmp_path: Path) -> None:
        p = _write_yaml(tmp_path)
        with (
            patch("roleplay.engine.engine.SimulationEngine") as eng_cls,
            patch("roleplay.cli._open_layer") as layer_fac,
        ):
            layer_fac.return_value = _mock_layer()
            eng_cls.return_value = _mock_engine()
            result = runner.invoke(
                app, ["run", str(p), "--max-episodes", "5", "--db", str(tmp_path / "t.db")]
            )
        assert result.exit_code == 0, result.output

    def test_run_provider_override(self, tmp_path: Path) -> None:
        p = _write_yaml(tmp_path)
        with (
            patch("roleplay.engine.engine.SimulationEngine") as eng_cls,
            patch("roleplay.cli._open_layer") as layer_fac,
        ):
            layer_fac.return_value = _mock_layer()
            eng_cls.return_value = _mock_engine()
            result = runner.invoke(
                app, ["run", str(p), "--provider", "mock", "--db", str(tmp_path / "t.db")]
            )
        assert result.exit_code == 0, result.output

    def test_run_engine_exception_exits_nonzero(self, tmp_path: Path) -> None:
        p = _write_yaml(tmp_path)
        with (
            patch("roleplay.engine.engine.SimulationEngine") as eng_cls,
            patch("roleplay.cli._open_layer") as layer_fac,
        ):
            layer_fac.return_value = _mock_layer()
            eng = _mock_engine()
            eng.run = AsyncMock(side_effect=RuntimeError("boom"))
            eng_cls.return_value = eng
            result = runner.invoke(app, ["run", str(p), "--db", str(tmp_path / "t.db")])
        assert result.exit_code != 0


class TestResumeCommand:
    def _state_fixture(self) -> MagicMock:
        from roleplay.core.episode import NoopClock, RoundRobinScheduler, SimulationHistory
        from roleplay.core.party import make_environment, make_person
        from roleplay.core.simulation_state import SimulationConfig, SimulationState

        return SimulationState(
            config=SimulationConfig(session_id="r-session", default_provider="mock"),
            parties={"alice": make_person("alice", "Alice", description="T")},
            environment=make_environment("env", "Env", ""),
            history=SimulationHistory(),
            scheduler=RoundRobinScheduler(),
            clock=NoopClock(),
        )

    def test_session_not_found_exits_1(self, tmp_path: Path) -> None:
        from roleplay.persistence import SessionNotFoundError

        with patch("roleplay.cli._open_layer") as layer_fac:
            layer = _mock_layer()
            layer.load_session = AsyncMock(side_effect=SessionNotFoundError("gone"))
            layer_fac.return_value = layer
            result = runner.invoke(app, ["resume", "nope", "--db", str(tmp_path / "t.db")])
        assert result.exit_code == 1

    def test_resume_succeeds(self, tmp_path: Path) -> None:
        state = self._state_fixture()
        with (
            patch("roleplay.cli._open_layer") as layer_fac,
            patch("roleplay.engine.engine.SimulationEngine") as eng_cls,
        ):
            layer = _mock_layer()
            layer.load_session = AsyncMock(return_value=state)
            layer_fac.return_value = layer
            eng_cls.return_value = _mock_engine()
            result = runner.invoke(app, ["resume", "r-session", "--db", str(tmp_path / "t.db")])
        assert result.exit_code == 0, result.output


class TestInspectCommand:
    def test_not_found_exits_1(self, tmp_path: Path) -> None:
        from roleplay.persistence import SessionNotFoundError

        with patch("roleplay.cli._open_layer") as layer_fac:
            layer = _mock_layer()
            layer.load_session = AsyncMock(side_effect=SessionNotFoundError("gone"))
            layer_fac.return_value = layer
            result = runner.invoke(app, ["inspect", "nope", "--db", str(tmp_path / "t.db")])
        assert result.exit_code == 1

    def test_inspect_prints_session_info(self, tmp_path: Path) -> None:
        export_data = {
            "session": {"last_saved_at": "2025-01-01T00:00:00"},
            "episodes": [],
            "parties": [
                {
                    "party_id": "alice",
                    "kind": "person",
                    "config_json": '{"persona": {"name": "Alice"}}',
                    "state_json": "{}",
                }
            ],
            "memories": [],
        }
        with patch("roleplay.cli._open_layer") as layer_fac:
            layer = _mock_layer()
            layer.export_json = AsyncMock(return_value=export_data)
            layer_fac.return_value = layer
            result = runner.invoke(app, ["inspect", "i-session", "--db", str(tmp_path / "t.db")])
        assert result.exit_code == 0, result.output
        assert "Alice" in result.output


class TestListCommand:
    def test_list_empty(self, tmp_path: Path) -> None:
        with patch("roleplay.cli._open_layer") as layer_fac:
            layer = _mock_layer()
            layer.list_sessions = AsyncMock(return_value=[])
            layer_fac.return_value = layer
            result = runner.invoke(app, ["list", "--db", str(tmp_path / "t.db")])
        assert result.exit_code == 0

    def test_list_json_format(self, tmp_path: Path) -> None:
        import json
        from datetime import UTC, datetime

        from roleplay.persistence.base import SessionSummary

        dt = datetime(2025, 1, 1, tzinfo=UTC)
        summary = SessionSummary(
            session_id="s1",
            parent_session_id=None,
            forked_at_episode=None,
            episode_count=2,
            party_count=1,
            status="done",
            started_at=dt,
            last_saved_at=dt,
        )
        with patch("roleplay.cli._open_layer") as layer_fac:
            layer = _mock_layer()
            layer.list_sessions = AsyncMock(return_value=[summary])
            layer_fac.return_value = layer
            result = runner.invoke(
                app, ["list", "--db", str(tmp_path / "t.db"), "--format", "json"]
            )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data[0]["session_id"] == "s1"

    def test_list_table_format(self, tmp_path: Path) -> None:
        from datetime import UTC, datetime

        from roleplay.persistence.base import SessionSummary

        dt = datetime(2025, 6, 1, tzinfo=UTC)
        summary = SessionSummary(
            session_id="table-session",
            parent_session_id=None,
            forked_at_episode=None,
            episode_count=5,
            party_count=2,
            status="done",
            started_at=dt,
            last_saved_at=dt,
        )
        with patch("roleplay.cli._open_layer") as layer_fac:
            layer = _mock_layer()
            layer.list_sessions = AsyncMock(return_value=[summary])
            layer_fac.return_value = layer
            result = runner.invoke(app, ["list", "--db", str(tmp_path / "t.db")])
        assert result.exit_code == 0
        assert "table-session" in result.output


class TestForkCommand:
    def test_not_found_exits_1(self, tmp_path: Path) -> None:
        from roleplay.persistence import SessionNotFoundError

        with patch("roleplay.cli._open_layer") as layer_fac:
            layer = _mock_layer()
            layer.fork = AsyncMock(side_effect=SessionNotFoundError("nope"))
            layer_fac.return_value = layer
            result = runner.invoke(app, ["fork", "nope", "--db", str(tmp_path / "t.db")])
        assert result.exit_code == 1

    def test_fork_succeeds_with_new_id(self, tmp_path: Path) -> None:
        with patch("roleplay.cli._open_layer") as layer_fac:
            layer = _mock_layer()
            layer.fork = AsyncMock()
            layer_fac.return_value = layer
            result = runner.invoke(
                app, ["fork", "orig", "--new-id", "fork-1", "--db", str(tmp_path / "t.db")]
            )
        assert result.exit_code == 0, result.output
        assert "fork-1" in result.output


class TestDeleteCommand:
    def test_missing_confirm_flag_exits_1(self, tmp_path: Path) -> None:
        # Without --confirm flag, delete should refuse
        result = runner.invoke(app, ["delete", "s1", "--db", str(tmp_path / "t.db")])
        assert result.exit_code == 1
        assert "--confirm" in result.output or "Safety" in result.output

    def test_not_found_exits_1(self, tmp_path: Path) -> None:
        from roleplay.persistence import SessionNotFoundError

        with patch("roleplay.cli._open_layer") as layer_fac:
            layer = _mock_layer()
            layer.delete_session = AsyncMock(side_effect=SessionNotFoundError("gone"))
            layer_fac.return_value = layer
            result = runner.invoke(
                app, ["delete", "nope", "--confirm", "--db", str(tmp_path / "t.db")]
            )
        assert result.exit_code == 1

    def test_delete_succeeds(self, tmp_path: Path) -> None:
        with patch("roleplay.cli._open_layer") as layer_fac:
            layer = _mock_layer()
            layer.delete_session = AsyncMock()
            layer_fac.return_value = layer
            result = runner.invoke(
                app, ["delete", "target", "--confirm", "--db", str(tmp_path / "t.db")]
            )
        assert result.exit_code == 0, result.output


class TestForgetCommand:
    def test_not_found_exits_nonzero(self, tmp_path: Path) -> None:
        from roleplay.persistence import SessionNotFoundError

        with patch("roleplay.cli._open_layer") as layer_fac:
            layer = _mock_layer()
            layer.delete_memory = AsyncMock(side_effect=SessionNotFoundError("nope"))
            layer_fac.return_value = layer
            result = runner.invoke(
                app,
                ["forget", "nope", "alice", "e1", "--db", str(tmp_path / "t.db")],
            )
        assert result.exit_code != 0

    def test_forget_succeeds(self, tmp_path: Path) -> None:
        with patch("roleplay.cli._open_layer") as layer_fac:
            layer = _mock_layer()
            layer.delete_memory = AsyncMock()
            layer_fac.return_value = layer
            result = runner.invoke(
                app,
                ["forget", "sess", "alice", "entry-abc", "--db", str(tmp_path / "t.db")],
            )
        assert result.exit_code == 0, result.output
        assert "entry-abc" in result.output


class TestHelpCommand:
    def test_root_help(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "roleplay" in result.output.lower()

    def test_run_help(self) -> None:
        assert runner.invoke(app, ["run", "--help"]).exit_code == 0

    def test_resume_help(self) -> None:
        assert runner.invoke(app, ["resume", "--help"]).exit_code == 0

    def test_list_help(self) -> None:
        assert runner.invoke(app, ["list", "--help"]).exit_code == 0
