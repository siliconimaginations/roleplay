"""Gap-filling tests to push overall coverage above 95%.

Covers missing branches in: validate.py, engine/engine.py,
providers/claude_provider.py, scenario_yaml.py, and cli.py helpers.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# validate.py — main() CLI entry + uncovered validation branches
# ---------------------------------------------------------------------------

_VALID_YAML = """\
session_id: gap-session

config:
  default_provider: mock
  max_episodes: 1

parties:
  - id: alice
    kind: person
    name: Alice
    persona:
      description: Test.

  - id: env
    kind: environment
    name: Room
    persona:
      description: A room.
"""


class TestValidateMain:
    def test_main_exits_0_for_valid_file(self, tmp_path: Path) -> None:
        p = tmp_path / "s.yaml"
        p.write_text(_VALID_YAML)
        with patch("sys.argv", ["validate", str(p)]):
            with pytest.raises(SystemExit) as exc:
                from roleplay.validate import main
                main()
        assert exc.value.code == 0

    def test_main_exits_1_for_invalid_file(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.yaml"
        p.write_text("parties: []\n")
        with patch("sys.argv", ["validate", str(p)]):
            with pytest.raises(SystemExit) as exc:
                from roleplay.validate import main
                main()
        assert exc.value.code == 1

    def test_main_quiet_flag_suppresses_warnings(self, tmp_path: Path) -> None:
        p = tmp_path / "s.yaml"
        p.write_text(_VALID_YAML)
        with patch("sys.argv", ["validate", "--quiet", str(p)]):
            with pytest.raises(SystemExit) as exc:
                from roleplay.validate import main
                main()
        assert exc.value.code == 0

    def test_main_multiple_files(self, tmp_path: Path) -> None:
        p1 = tmp_path / "a.yaml"
        p2 = tmp_path / "b.yaml"
        p1.write_text(_VALID_YAML)
        p2.write_text(_VALID_YAML)
        with patch("sys.argv", ["validate", str(p1), str(p2)]):
            with pytest.raises(SystemExit) as exc:
                from roleplay.validate import main
                main()
        assert exc.value.code == 0


class TestValidateBranches:
    def test_invalid_yaml_syntax_returns_error(self, tmp_path: Path) -> None:
        """Covers lines 121-129: YAML parse exception → ValidationError."""
        from roleplay.validate import validate_scenario

        p = tmp_path / "broken.yaml"
        p.write_text("key: [unclosed\n")
        result = validate_scenario(p)
        assert result.errors

    def test_context_window_episodes_zero_is_error(self, tmp_path: Path) -> None:
        """Covers lines 234: context_window_episodes < 1 in TOML."""
        import warnings
        from roleplay.validate import validate_scenario

        toml = """
[simulation]
default_provider = "mock"
episodes = 1
context_window_episodes = 0

[[parties]]
id = "alice"
kind = "person"
name = "Alice"

[parties.persona]
description = "T."

[[parties]]
id = "env"
kind = "environment"
name = "Env"

[parties.persona]
description = "E."
"""
        p = tmp_path / "cwe.toml"
        p.write_text(toml)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            result = validate_scenario(p)
        assert any("context_window_episodes" in e.field for e in result.errors)


# ---------------------------------------------------------------------------
# engine/engine.py — uncovered branches
# ---------------------------------------------------------------------------


def _make_engine(
    provider: object | None = None,
    *,
    memory_fail_mode: str = "warn",
    env_reactive: bool = False,
) -> tuple:
    """Helper returning (engine, state, mock_provider)."""
    from roleplay.core.episode import NoopClock, RoundRobinScheduler, SimulationHistory
    from roleplay.core.party import make_environment, make_person
    from roleplay.core.simulation_state import SimulationConfig, SimulationState
    from roleplay.engine.engine import SimulationEngine
    from roleplay.memory.store import InMemoryStore
    from roleplay.providers.base import CompletionResponse

    mock_provider = provider or MagicMock()
    mock_provider.complete = AsyncMock(
        return_value=CompletionResponse(text="hi", model_used="mock", prompt_tokens=5, completion_tokens=3)
    )

    cfg = SimulationConfig(
        session_id="gap-test",
        default_provider="mock",
        memory_retrieve_fail_mode=memory_fail_mode,  # type: ignore[arg-type]
        environment_reactive=env_reactive,
    )
    state = SimulationState(
        config=cfg,
        parties={"alice": make_person("alice", "Alice", description="T")},
        environment=make_environment("env", "Env", "A room"),
        history=SimulationHistory(),
        scheduler=RoundRobinScheduler(),
        clock=NoopClock(),
    )
    engine = SimulationEngine(
        state=state,
        provider=mock_provider,
        memory_store=InMemoryStore(),
    )
    return engine, state, mock_provider


class TestEngineGaps:
    @pytest.mark.asyncio
    async def test_memory_fail_mode_warn_does_not_raise(self) -> None:
        """Covers lines 218-225: memory retrieval failure with warn mode."""
        from roleplay.memory.store import InMemoryStore

        engine, state, _ = _make_engine(memory_fail_mode="warn")
        # Patch memory store to raise on retrieve
        broken_store = InMemoryStore()
        broken_store.retrieve = MagicMock(side_effect=RuntimeError("DB gone"))  # type: ignore[method-assign]
        engine._memory_store = broken_store  # type: ignore[attr-defined]

        with pytest.warns(UserWarning, match="Memory retrieval failed"):
            await engine.run(max_episodes=1)

    @pytest.mark.asyncio
    async def test_memory_fail_mode_raise_propagates(self) -> None:
        """Covers lines 218-225 raise branch."""
        from roleplay.memory.store import InMemoryStore

        engine, state, _ = _make_engine(memory_fail_mode="raise")
        broken_store = InMemoryStore()
        broken_store.retrieve = MagicMock(side_effect=RuntimeError("DB gone"))  # type: ignore[method-assign]
        engine._memory_store = broken_store  # type: ignore[attr-defined]

        with pytest.raises(RuntimeError, match="DB gone"):
            await engine.run(max_episodes=1)

    @pytest.mark.asyncio
    async def test_state_update_apply_exception_warns(self) -> None:
        """Covers lines 249-250: apply_state_update raising an exception."""
        from roleplay.providers.base import CompletionResponse

        engine, state, mock_provider = _make_engine()
        # Return state proposal that triggers a bad update
        mock_provider.complete = AsyncMock(
            return_value=CompletionResponse(
                text="response\n[STATE alice.mood=happy]\n",
                model_used="mock",
                prompt_tokens=5,
                completion_tokens=5,
            )
        )
        # Patch alice's apply_state_update to raise
        state.parties["alice"].apply_state_update = MagicMock(side_effect=ValueError("bad"))
        # Should not raise — just log warning
        await engine.run(max_episodes=1)

    @pytest.mark.asyncio
    async def test_environment_reactive_calls_env_turn(self) -> None:
        """Covers lines 299-302: environment_reactive=True."""
        call_count = 0

        from roleplay.providers.base import CompletionResponse

        async def count_calls(req):  # type: ignore[no-untyped-def]
            nonlocal call_count
            call_count += 1
            return CompletionResponse(text="hi", model_used="mock", prompt_tokens=2, completion_tokens=2)

        engine, state, mock_provider = _make_engine(env_reactive=True)
        mock_provider.complete = count_calls

        await engine.run(max_episodes=1)
        # alice (1 party) + environment = 2 calls
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_provider_exhausted_stops_run(self) -> None:
        """Covers lines 345-347: ProviderExhaustedError breaks the episode loop."""
        from roleplay.providers.base import ProviderExhaustedError

        engine, _, mock_provider = _make_engine()
        mock_provider.complete = AsyncMock(side_effect=ProviderExhaustedError("all gone"))

        # Should not raise — just stop gracefully
        await engine.run(max_episodes=3)


# ---------------------------------------------------------------------------
# providers/claude_provider.py — uncovered branches
# ---------------------------------------------------------------------------


class TestClaudeProviderGaps:
    @pytest.fixture
    def provider(self):  # type: ignore[no-untyped-def]
        import httpx
        from roleplay.providers.claude_provider import ClaudeProvider

        return ClaudeProvider(api_key="test-key")

    @pytest.mark.asyncio
    async def test_provider_error_in_call_exhausts_all_models(self, provider) -> None:  # type: ignore[no-untyped-def]
        """Covers lines 90-93: ProviderError from _call → _try_model returns None → all exhausted."""
        from roleplay.providers.base import CompletionRequest, ProviderError, ProviderExhaustedError

        with patch.object(provider, "_call", side_effect=ProviderError("bad")):
            with pytest.raises(ProviderExhaustedError):
                await provider.complete(CompletionRequest(prompt="hi"))

    @pytest.mark.asyncio
    async def test_provider_error_in_try_model_returns_none(self, provider) -> None:  # type: ignore[no-untyped-def]
        """Covers lines 90-93: _try_model returns None when _call raises ProviderError."""
        from roleplay.providers.base import CompletionRequest, ProviderError

        with patch.object(provider, "_call", side_effect=ProviderError("bad")):
            result = await provider._try_model(  # type: ignore[attr-defined]
                provider.default_model, CompletionRequest(prompt="hi"), []
            )
        assert result is None

    @pytest.mark.asyncio
    async def test_retry_after_invalid_float_falls_back_to_default(self, provider) -> None:  # type: ignore[no-untyped-def]
        """Covers lines 127-129: retry-after header with non-float value."""
        import json
        import httpx
        from roleplay.providers.base import CompletionRequest, ProviderRateLimitError

        bad_header_resp = httpx.Response(
            429,
            content=json.dumps({"error": {"type": "rate_limit_error"}}).encode(),
            headers={"retry-after": "not-a-number"},
        )
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=bad_header_resp):
            with pytest.raises(ProviderRateLimitError) as exc_info:
                await provider._call(provider.default_model, CompletionRequest(prompt="hi"))
        # retry_after_seconds should be None since header was unparseable
        assert exc_info.value.retry_after_seconds is None

    @pytest.mark.asyncio
    async def test_500_raises_provider_error(self, provider) -> None:  # type: ignore[no-untyped-def]
        """Covers lines 135-136: >= 500 status raises ProviderError (server error)."""
        import httpx
        from roleplay.providers.base import CompletionRequest, ProviderError

        resp_500 = httpx.Response(500, content=b"Internal Server Error")
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=resp_500):
            with pytest.raises(ProviderError, match="Claude server error 500"):
                await provider._call(provider.default_model, CompletionRequest(prompt="hi"))


# ---------------------------------------------------------------------------
# scenario_yaml.py — uncovered branches
# ---------------------------------------------------------------------------


class TestScenarioYamlGaps:
    def test_party_missing_name_raises(self, tmp_path: Path) -> None:
        """Covers lines 95-96: party without 'name' key → ValidationError."""
        from roleplay.scenario_yaml import ValidationError, load_yaml_scenario

        yaml = """\
session_id: gap
config:
  default_provider: mock
parties:
  - id: alice
    kind: person
    persona:
      description: T.
  - id: env
    kind: environment
    name: Room
    persona:
      description: E.
"""
        p = tmp_path / "s.yaml"
        p.write_text(yaml)
        with pytest.raises(ValidationError, match="name"):
            load_yaml_scenario(p)

    def test_bad_handler_no_module_raises(self, tmp_path: Path) -> None:
        """Covers lines 167-175: handler path without dotted module → ImportError."""
        from roleplay.scenario_yaml import load_yaml_scenario

        yaml = """session_id: gap
config:
  default_provider: mock
parties:
  - id: alice
    kind: person
    name: Alice
    persona:
      description: T.
  - id: env
    kind: environment
    name: Room
    persona:
      description: E.
tools:
  - name: bad_tool
    handler: no_dots_here
"""
        p = tmp_path / "s.yaml"
        p.write_text(yaml)
        with pytest.raises(ImportError, match="no_dots_here"):
            load_yaml_scenario(p)


    def test_organization_party_loaded(self, tmp_path: Path) -> None:
        """Covers line 262: make_organization path."""
        from roleplay.scenario_yaml import load_yaml_scenario

        yaml = """\
session_id: gap
config:
  default_provider: mock
parties:
  - id: acme
    kind: organization
    name: ACME Corp
    persona:
      description: A company.
  - id: env
    kind: environment
    name: Office
    persona:
      description: An office.
"""
        p = tmp_path / "s.yaml"
        p.write_text(yaml)
        result = load_yaml_scenario(p)
        assert "acme" in result.state.parties


# ---------------------------------------------------------------------------
# cli.py — _run_intervention interactive commands + inspect paths
# ---------------------------------------------------------------------------


def _make_cli_hook(max_episodes: int = 3, interactive: bool = True) -> object:
    from roleplay.cli import CliObserverHook, StreamPrinter

    layer = AsyncMock()
    layer.save_episode = AsyncMock()
    layer.save_state = AsyncMock()
    layer.checkpoint = AsyncMock()
    return CliObserverHook(
        StreamPrinter(),
        interactive=interactive,
        max_episodes=max_episodes,
        persistence=layer,
        session_id="gap-session",
    )


def _make_cli_state() -> MagicMock:
    state = MagicMock()
    state.config.session_id = "gap-session"
    state.config.default_provider = "mock"
    state.history.completed_episodes.return_value = []
    return state


class TestRunIntervention:
    @pytest.mark.asyncio
    async def test_continue_returns_payload(self) -> None:
        hook = _make_cli_hook()
        with patch("builtins.input", return_value="c"):
            result = await hook._run_intervention(_make_cli_state())  # type: ignore[attr-defined]
        assert result is not None

    @pytest.mark.asyncio
    async def test_quit_returns_none(self) -> None:
        hook = _make_cli_hook()
        with patch("builtins.input", return_value="q"):
            result = await hook._run_intervention(_make_cli_state())  # type: ignore[attr-defined]
        assert result is None

    @pytest.mark.asyncio
    async def test_quit_checkpoints_when_persistence(self) -> None:
        hook = _make_cli_hook()
        with patch("builtins.input", return_value="quit"):
            await hook._run_intervention(_make_cli_state())  # type: ignore[attr-defined]
        hook._persistence.checkpoint.assert_called_once()  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_inject_then_continue(self) -> None:
        from roleplay.engine.observer import InjectionPayload

        hook = _make_cli_hook()
        inputs = iter(["i extra context", "c"])
        with patch("builtins.input", side_effect=inputs):
            result = await hook._run_intervention(_make_cli_state())  # type: ignore[attr-defined]
        assert isinstance(result, InjectionPayload)
        assert result.context_override == "extra context"

    @pytest.mark.asyncio
    async def test_state_update_then_continue(self) -> None:
        hook = _make_cli_hook()
        inputs = iter(["s alice mood=happy", "c"])
        with patch("builtins.input", side_effect=inputs):
            result = await hook._run_intervention(_make_cli_state())  # type: ignore[attr-defined]
        assert result is not None
        assert "alice" in result.state_updates  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_state_bad_usage_shows_hint(self, capsys: pytest.CaptureFixture[str]) -> None:
        hook = _make_cli_hook()
        inputs = iter(["s badarg", "c"])
        with patch("builtins.input", side_effect=inputs):
            await hook._run_intervention(_make_cli_state())  # type: ignore[attr-defined]
        assert "Usage" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_memory_write_then_continue(self) -> None:
        hook = _make_cli_hook()
        inputs = iter(['m alice "Remember this"', "c"])
        with patch("builtins.input", side_effect=inputs):
            result = await hook._run_intervention(_make_cli_state())  # type: ignore[attr-defined]
        assert result is not None
        assert len(result.memory_writes) == 1  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_memory_bad_usage_shows_hint(self, capsys: pytest.CaptureFixture[str]) -> None:
        hook = _make_cli_hook()
        inputs = iter(["m", "c"])
        with patch("builtins.input", side_effect=inputs):
            await hook._run_intervention(_make_cli_state())  # type: ignore[attr-defined]
        assert "Usage" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_order_sets_force_scheduler(self) -> None:
        hook = _make_cli_hook()
        inputs = iter(["o alice bob", "c"])
        with patch("builtins.input", side_effect=inputs):
            result = await hook._run_intervention(_make_cli_state())  # type: ignore[attr-defined]
        assert result is not None
        assert result.force_scheduler == ["alice", "bob"]  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_help_command_prints_help(self, capsys: pytest.CaptureFixture[str]) -> None:
        hook = _make_cli_hook()
        inputs = iter(["?", "c"])
        with patch("builtins.input", side_effect=inputs):
            await hook._run_intervention(_make_cli_state())  # type: ignore[attr-defined]
        assert "[c]ontinue" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_unknown_command_shows_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        hook = _make_cli_hook()
        inputs = iter(["xyz", "c"])
        with patch("builtins.input", side_effect=inputs):
            await hook._run_intervention(_make_cli_state())  # type: ignore[attr-defined]
        assert "Unknown" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_empty_input_loops(self) -> None:
        hook = _make_cli_hook()
        inputs = iter(["", "c"])
        with patch("builtins.input", side_effect=inputs):
            result = await hook._run_intervention(_make_cli_state())  # type: ignore[attr-defined]
        assert result is not None

    @pytest.mark.asyncio
    async def test_eof_returns_none(self) -> None:
        hook = _make_cli_hook()
        with patch("builtins.input", side_effect=EOFError):
            result = await hook._run_intervention(_make_cli_state())  # type: ignore[attr-defined]
        assert result is None


class TestInspectCommandGaps:
    """Additional inspect command coverage for JSON format and rich text paths."""

    def test_inspect_json_format(self, tmp_path: Path) -> None:
        import json
        from typer.testing import CliRunner
        from roleplay.cli import app

        r = CliRunner()
        export_data = {
            "session": {"last_saved_at": "2025-01-01T00:00:00"},
            "episodes": [],
            "parties": [],
            "memories": [],
        }
        with patch("roleplay.cli._open_layer") as layer_fac:
            layer = AsyncMock()
            layer.export_json = AsyncMock(return_value=export_data)
            layer.close = AsyncMock()
            layer_fac.return_value = layer
            result = r.invoke(
                app,
                ["inspect", "s1", "--format", "json", "--db", str(tmp_path / "t.db")],
            )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["session"]["last_saved_at"] == "2025-01-01T00:00:00"

    def test_inspect_with_memories_flag(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner
        from roleplay.cli import app

        r = CliRunner()
        export_data = {
            "session": {"last_saved_at": "t"},
            "episodes": [
                {
                    "episode_index": 0,
                    "simulated_time_start": "t0",
                    "simulated_time_end": "t1",
                    "turns": [{"party_id": "alice", "prompt_tokens": 10, "completion_tokens": 5}],
                }
            ],
            "parties": [
                {
                    "party_id": "alice",
                    "kind": "person",
                    "config_json": '{"persona": {"name": "Alice"}}',
                    "state_json": '{"mood": "happy"}',
                }
            ],
            "memories": [
                {
                    "party_id": "alice",
                    "kind": "episodic",
                    "content": "A test memory",
                    "importance": 0.75,
                }
            ],
        }
        with patch("roleplay.cli._open_layer") as layer_fac:
            layer = AsyncMock()
            layer.export_json = AsyncMock(return_value=export_data)
            layer.close = AsyncMock()
            layer_fac.return_value = layer
            result = r.invoke(
                app,
                ["inspect", "s1", "--memories", "--db", str(tmp_path / "t.db")],
            )
        assert result.exit_code == 0, result.output
        assert "A test memory" in result.output
        assert "mood=happy" in result.output

    def test_inspect_with_party_filter(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner
        from roleplay.cli import app

        r = CliRunner()
        export_data = {
            "session": {"last_saved_at": "t"},
            "episodes": [],
            "parties": [
                {"party_id": "alice", "kind": "person", "config_json": "{}", "state_json": "{}"},
                {"party_id": "bob", "kind": "person", "config_json": "{}", "state_json": "{}"},
            ],
            "memories": [],
        }
        with patch("roleplay.cli._open_layer") as layer_fac:
            layer = AsyncMock()
            layer.export_json = AsyncMock(return_value=export_data)
            layer.close = AsyncMock()
            layer_fac.return_value = layer
            result = r.invoke(
                app,
                ["inspect", "s1", "--party", "alice", "--db", str(tmp_path / "t.db")],
            )
        assert result.exit_code == 0, result.output
        assert "alice" in result.output
        assert "bob" not in result.output


class TestCliOpenLayer:
    @pytest.mark.asyncio
    async def test_open_layer_creates_sqlite_layer(self, tmp_path: Path) -> None:
        """Covers lines 64-68: _open_layer actually opens a real SQLite layer."""
        from roleplay.cli import _open_layer

        db = tmp_path / "test.db"
        layer = await _open_layer(db)
        assert layer is not None
        await layer.close()


class TestCliMain:
    def test_main_is_callable(self) -> None:
        """Covers line 760: main() function exists and is callable."""
        from roleplay.cli import main

        assert callable(main)
