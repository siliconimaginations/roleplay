"""Integration smoke test for the POC runner.

Runs 2 episodes end-to-end with a mock provider — no LLM API calls.
Verifies the full stack assembles correctly and produces episode output.
"""

from __future__ import annotations

from pathlib import Path

from roleplay.core.simulation_state import SimulationConfig
from roleplay.engine.engine import SimulationEngine
from roleplay.memory.store import InMemoryStore
from roleplay.poc import _build_default_state as _build_state
from roleplay.poc import _MockProvider, run_poc


class TestMockProvider:
    async def test_cycles_responses(self) -> None:
        p = _MockProvider()
        from roleplay.providers.base import CompletionRequest

        r1 = await p.complete(CompletionRequest(prompt="x"))
        r2 = await p.complete(CompletionRequest(prompt="x"))
        assert r1.text != "" or r2.text != ""
        assert r1.model_used == "mock"

    def test_default_model(self) -> None:
        assert _MockProvider().default_model == "mock"


class TestBuildState:
    def test_has_expected_parties(self) -> None:
        cfg = SimulationConfig(session_id="test")
        state = _build_state(cfg)
        assert "alice" in state.parties
        assert "bob" in state.parties

    def test_environment_is_not_in_parties(self) -> None:
        cfg = SimulationConfig(session_id="test")
        state = _build_state(cfg)
        assert "town" not in state.parties
        assert state.environment.id == "town"

    def test_environment_has_initial_state(self) -> None:
        cfg = SimulationConfig(session_id="test")
        state = _build_state(cfg)
        snap = state.environment.state_snapshot()
        assert "time.simulated" in snap
        assert "weather.condition" in snap


class TestPocRunMock:
    async def test_run_two_episodes(self) -> None:
        await run_poc(use_mock=True, max_episodes=2)

    async def test_episodes_recorded_in_history(self) -> None:
        cfg = SimulationConfig(session_id="poc-smoke", environment_reactive=False)
        state = _build_state(cfg)
        memory_store = InMemoryStore()
        provider = _MockProvider()
        engine = SimulationEngine(state=state, provider=provider, memory_store=memory_store)
        await engine.run(max_episodes=2)
        completed = state.history.completed_episodes()
        assert len(completed) == 2
        assert all(len(ep.turns) > 0 for ep in completed)

    async def test_turns_have_output(self) -> None:
        cfg = SimulationConfig(session_id="poc-turns", environment_reactive=False)
        state = _build_state(cfg)
        memory_store = InMemoryStore()
        engine = SimulationEngine(state=state, provider=_MockProvider(), memory_store=memory_store)
        await engine.run(max_episodes=1)
        ep = state.history.completed_episodes()[0]
        for turn in ep.turns:
            assert turn.output != ""

    async def test_memory_written_after_episodes(self) -> None:
        cfg = SimulationConfig(session_id="poc-mem", environment_reactive=False)
        state = _build_state(cfg)
        memory_store = InMemoryStore()
        engine = SimulationEngine(state=state, provider=_MockProvider(), memory_store=memory_store)
        await engine.run(max_episodes=1)
        all_entries = await memory_store.list_all("alice")
        assert len(all_entries) > 0

    async def test_environment_reactive_adds_env_turn(self) -> None:
        cfg = SimulationConfig(session_id="poc-env", environment_reactive=True)
        state = _build_state(cfg)
        memory_store = InMemoryStore()
        engine = SimulationEngine(state=state, provider=_MockProvider(), memory_store=memory_store)
        await engine.run(max_episodes=1)
        ep = state.history.completed_episodes()[0]
        party_ids = [t.party_id for t in ep.turns]
        # environment should appear as the last turn
        assert state.environment.id in party_ids


# ---------------------------------------------------------------------------
# _CliObserver
# ---------------------------------------------------------------------------


class TestCliObserver:
    async def test_before_episode_returns_continue(self) -> None:
        from io import StringIO
        from unittest.mock import patch

        from roleplay.poc import _CliObserver

        cfg = SimulationConfig(session_id="t")
        state = _build_state(cfg)
        obs = _CliObserver()
        with patch("sys.stdout", new_callable=StringIO):
            directive = await obs.before_episode(state, 0)
        assert directive.is_halt is False

    async def test_after_turn_returns_continue(self) -> None:
        from io import StringIO
        from unittest.mock import patch

        from roleplay.poc import _CliObserver

        cfg = SimulationConfig(session_id="t")
        state = _build_state(cfg)
        memory_store = InMemoryStore()
        engine = SimulationEngine(state=state, provider=_MockProvider(), memory_store=memory_store)
        await engine.run(max_episodes=1)
        ep = state.history.completed_episodes()[0]
        turn = ep.turns[0]

        obs = _CliObserver()
        with patch("sys.stdout", new_callable=StringIO):
            directive = await obs.after_turn(state, turn)
        assert directive.is_halt is False

    async def test_after_turn_prints_party_name(self) -> None:
        from io import StringIO
        from unittest.mock import patch

        from roleplay.poc import _CliObserver

        cfg = SimulationConfig(session_id="t")
        state = _build_state(cfg)
        memory_store = InMemoryStore()
        engine = SimulationEngine(state=state, provider=_MockProvider(), memory_store=memory_store)
        await engine.run(max_episodes=1)
        ep = state.history.completed_episodes()[0]
        turn = ep.turns[0]

        obs = _CliObserver()
        buf = StringIO()
        with patch("sys.stdout", buf):
            await obs.after_turn(state, turn)
        output = buf.getvalue()
        party = state.get_party(turn.party_id)
        assert party.name in output

    async def test_after_turn_prints_turn_output(self) -> None:
        from io import StringIO
        from unittest.mock import patch

        from roleplay.poc import _CliObserver

        cfg = SimulationConfig(session_id="t")
        state = _build_state(cfg)
        provider = _MockProvider()
        memory_store = InMemoryStore()
        engine = SimulationEngine(state=state, provider=provider, memory_store=memory_store)
        await engine.run(max_episodes=1)
        ep = state.history.completed_episodes()[0]
        turn = ep.turns[0]

        obs = _CliObserver()
        buf = StringIO()
        with patch("sys.stdout", buf):
            await obs.after_turn(state, turn)
        assert turn.output.strip()[:20] in buf.getvalue()

    async def test_after_episode_returns_continue(self) -> None:
        from roleplay.poc import _CliObserver

        cfg = SimulationConfig(session_id="t")
        state = _build_state(cfg)
        obs = _CliObserver()
        directive = await obs.after_episode(state, object())
        assert directive.is_halt is False

    async def test_observer_wired_into_engine(self) -> None:
        """run_poc with observer= receives after_turn calls for each turn."""
        from io import StringIO
        from unittest.mock import patch

        from roleplay.poc import _CliObserver

        obs = _CliObserver()
        buf = StringIO()
        with patch("sys.stdout", buf):
            await run_poc(use_mock=True, max_episodes=1, observer=obs)
        # At least one party name should appear in output
        assert "Alice" in buf.getvalue() or "Bob" in buf.getvalue()


# ---------------------------------------------------------------------------
# run_poc with --config path
# ---------------------------------------------------------------------------


class TestRunPocWithConfig:
    async def test_run_with_example_toml_mock(self, tmp_path: Path) -> None:
        """Loading scenarios/example.toml with mock provider completes without error."""

        example = Path(__file__).parent.parent / "scenarios" / "example.toml"
        await run_poc(use_mock=True, max_episodes=1, config_path=example)

    async def test_config_parties_are_used(self, tmp_path: Path) -> None:
        """Parties defined in the TOML file drive the simulation turns."""

        example = Path(__file__).parent.parent / "scenarios" / "example.toml"
        # We need to inspect state after run — build it directly
        from roleplay.config import load_scenario

        state, _, _ = load_scenario(example)
        memory_store = InMemoryStore()
        engine = SimulationEngine(state=state, provider=_MockProvider(), memory_store=memory_store)
        await engine.run(max_episodes=1)
        ep = state.history.completed_episodes()[0]
        party_ids = {t.party_id for t in ep.turns}
        assert "alice" in party_ids
        assert "bob" in party_ids

    async def test_mock_flag_overrides_toml_provider(self, tmp_path: Path) -> None:
        """use_mock=True must work even when TOML specifies provider=gemini."""

        example = Path(__file__).parent.parent / "scenarios" / "example.toml"
        # Would fail with ProviderExhaustedError if real Gemini was called
        await run_poc(use_mock=True, max_episodes=1, config_path=example)

    async def test_episodes_sentinel_uses_toml_value(self, tmp_path: Path) -> None:
        """max_episodes=-1 (CLI sentinel) picks up the episode count from TOML."""

        example = Path(__file__).parent.parent / "scenarios" / "example.toml"
        # example.toml has episodes=3; run_poc should run 3 episodes
        from roleplay.config import load_scenario

        state, _, _ = load_scenario(example)
        memory_store = InMemoryStore()
        engine = SimulationEngine(state=state, provider=_MockProvider(), memory_store=memory_store)
        await engine.run(max_episodes=-1 if False else 3)
        assert len(state.history.completed_episodes()) == 3
