"""Integration smoke test for the POC runner.

Runs 2 episodes end-to-end with a mock provider — no LLM API calls.
Verifies the full stack assembles correctly and produces episode output.
"""

from __future__ import annotations

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
        assert "weather" in snap


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
