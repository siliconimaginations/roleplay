"""Tests for SimulationEngine, prompt assembly, and state proposal parsing."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from roleplay.core.episode import NoopClock, RoundRobinScheduler, SimulationHistory
from roleplay.core.party import make_environment, make_person
from roleplay.core.simulation_state import SimulationConfig, SimulationState
from roleplay.engine.engine import SimulationEngine, _assemble_prompt, _parse_state_proposals
from roleplay.engine.observer import InjectionPayload, ObserverDirective

if TYPE_CHECKING:
    from roleplay.engine.turn import Turn
from roleplay.memory.store import InMemoryStore, MemoryEntry, MemoryKind
from roleplay.providers.base import CompletionRequest, CompletionResponse

# ---------------------------------------------------------------------------
# Mock provider
# ---------------------------------------------------------------------------


class MockProvider:
    """Returns responses from a queue, raises if exhausted."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.calls.append(request)
        if not self._responses:
            raise RuntimeError("MockProvider response queue exhausted")
        return CompletionResponse(text=self._responses.pop(0), model_used="mock")

    @property
    def default_model(self) -> str:
        return "mock"


# ---------------------------------------------------------------------------
# Mock observer
# ---------------------------------------------------------------------------


class MockObserver:
    def __init__(
        self,
        *,
        before_directives: list[ObserverDirective] | None = None,
        after_turn_directives: list[ObserverDirective] | None = None,
        after_episode_directives: list[ObserverDirective] | None = None,
    ) -> None:
        self._before = list(before_directives or [])
        self._after_turn = list(after_turn_directives or [])
        self._after_episode = list(after_episode_directives or [])
        self.before_calls: list[int] = []
        self.after_turn_calls: list[Turn] = []
        self.after_episode_calls: list[object] = []

    async def before_episode(self, state: SimulationState, episode_index: int) -> ObserverDirective:
        self.before_calls.append(episode_index)
        if self._before:
            return self._before.pop(0)
        return ObserverDirective.continue_()

    async def after_turn(self, state: SimulationState, turn: Turn) -> ObserverDirective:
        self.after_turn_calls.append(turn)
        if self._after_turn:
            return self._after_turn.pop(0)
        return ObserverDirective.continue_()

    async def after_episode(self, state: SimulationState, episode: object) -> ObserverDirective:
        self.after_episode_calls.append(episode)
        if self._after_episode:
            return self._after_episode.pop(0)
        return ObserverDirective.continue_()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_state(
    *,
    parties: dict | None = None,
    passive_observation_parties: list[str] | None = None,
    environment_reactive: bool = True,
    context_window_episodes: int = 10,
) -> SimulationState:
    alice = make_person("alice", "Alice", "A cautious merchant")
    env = make_environment("town", "Town", "A small town", [], {"time.simulated": "morning"})
    p = {"alice": alice}
    if parties:
        p.update(parties)
    cfg = SimulationConfig(
        session_id="test",
        passive_observation_parties=passive_observation_parties or [],
        environment_reactive=environment_reactive,
        context_window_episodes=context_window_episodes,
    )
    return SimulationState(
        config=cfg,
        parties=p,
        environment=env,
        history=SimulationHistory(),
        scheduler=RoundRobinScheduler(),
        clock=NoopClock(),
    )


def _engine(
    state: SimulationState,
    responses: list[str],
    observer: object | None = None,
) -> tuple[SimulationEngine, MockProvider]:
    provider = MockProvider(responses)
    mem = InMemoryStore()
    eng = SimulationEngine(state, provider, mem, observer=observer)  # type: ignore[arg-type]
    return eng, provider


# ---------------------------------------------------------------------------
# _parse_state_proposals
# ---------------------------------------------------------------------------


class TestParseStateProposals:
    def test_single_proposal(self) -> None:
        text, proposals = _parse_state_proposals("I feel great.\nSTATE: mood=happy")
        assert proposals == {"mood": "happy"}
        assert "STATE:" not in text

    def test_multiple_proposals(self) -> None:
        raw = "Some output.\nSTATE: mood=angry\nSTATE: location=harbour"
        text, proposals = _parse_state_proposals(raw)
        assert proposals["mood"] == "angry"
        assert proposals["location"] == "harbour"
        assert "STATE:" not in text

    def test_int_coercion(self) -> None:
        _, proposals = _parse_state_proposals("STATE: count=42")
        assert proposals["count"] == 42
        assert isinstance(proposals["count"], int)

    def test_float_coercion(self) -> None:
        _, proposals = _parse_state_proposals("STATE: score=3.14")
        assert abs(proposals["score"] - 3.14) < 1e-9  # type: ignore[operator]

    def test_bool_true_coercion(self) -> None:
        _, proposals = _parse_state_proposals("STATE: active=true")
        assert proposals["active"] is True

    def test_bool_false_coercion(self) -> None:
        _, proposals = _parse_state_proposals("STATE: active=false")
        assert proposals["active"] is False

    def test_bool_yes_no(self) -> None:
        _, p = _parse_state_proposals("STATE: ok=yes\nSTATE: bad=no")
        assert p["ok"] is True
        assert p["bad"] is False

    def test_no_proposals(self) -> None:
        text, proposals = _parse_state_proposals("Just some output, no state.")
        assert proposals == {}
        assert text == "Just some output, no state."

    def test_output_text_preserved(self) -> None:
        raw = "I am here.\nSTATE: mood=calm\nLet us proceed."
        text, _ = _parse_state_proposals(raw)
        assert "I am here." in text
        assert "Let us proceed." in text


# ---------------------------------------------------------------------------
# _assemble_prompt
# ---------------------------------------------------------------------------


class TestAssemblePrompt:
    def test_includes_party_name(self) -> None:
        state = _make_state()
        prompt = _assemble_prompt("alice", state, [], [], None)
        assert "Alice" in prompt

    def test_includes_environment(self) -> None:
        state = _make_state()
        prompt = _assemble_prompt("alice", state, [], None, None)
        assert "Town" in prompt

    def test_includes_memories(self) -> None:
        state = _make_state()
        mem = MemoryEntry(
            party_id="alice",
            kind=MemoryKind.EPISODIC,
            content="Alice saw Bob yesterday",
            episode_index=0,
        )
        prompt = _assemble_prompt("alice", state, [mem], [], None)
        assert "Alice saw Bob yesterday" in prompt

    def test_includes_context_override(self) -> None:
        state = _make_state()
        prompt = _assemble_prompt("alice", state, [], [], "An earthquake strikes!")
        assert "earthquake" in prompt

    def test_includes_instruction_suffix(self) -> None:
        state = _make_state()
        prompt = _assemble_prompt("alice", state, [], [], None)
        assert "Respond in character" in prompt
        assert "STATE: key=value" in prompt

    def test_budget_trims_history(self) -> None:
        """Tiny budget causes history to be trimmed."""
        state = _make_state()
        # Manually add many completed episodes to history
        from roleplay.core.episode import Episode
        from roleplay.core.episode import Turn as CoreTurn

        for i in range(5):
            ep = Episode(index=i, turns=[], simulated_time_start="t0")
            ep.add_turn(CoreTurn(party_id="alice", index=0, output="x" * 200))
            ep.close("t1")
            state.history.episodes.append(ep)
        # Very small budget
        prompt = _assemble_prompt("alice", state, [], [], None, prompt_char_budget=500)
        # Should still produce a prompt (not crash)
        assert "Alice" in prompt


# ---------------------------------------------------------------------------
# ObserverDirective
# ---------------------------------------------------------------------------


class TestObserverDirective:
    def test_continue(self) -> None:
        d = ObserverDirective.continue_()
        assert not d.is_halt
        assert not d.is_inject

    def test_halt(self) -> None:
        d = ObserverDirective.halt("done")
        assert d.is_halt
        assert d.reason == "done"

    def test_inject(self) -> None:
        payload = InjectionPayload(context_override="fire!")
        d = ObserverDirective.inject(payload)
        assert d.is_inject
        assert d.payload is payload
        assert d.payload.context_override == "fire!"

    def test_inject_payload_none_by_default(self) -> None:
        d = ObserverDirective.continue_()
        assert d.payload is None


# ---------------------------------------------------------------------------
# SimulationEngine — run_episode
# ---------------------------------------------------------------------------


class TestRunEpisode:
    async def test_basic_episode_runs(self) -> None:
        state = _make_state(environment_reactive=False)
        eng, _provider = _engine(state, ["Hello from Alice."])
        episode = await eng.run_episode()
        assert episode.index == 0
        assert len(episode.turns) == 1
        assert episode.turns[0].output == "Hello from Alice."

    async def test_episode_added_to_history(self) -> None:
        state = _make_state(environment_reactive=False)
        eng, _ = _engine(state, ["response"])
        await eng.run_episode()
        assert len(state.history.completed_episodes()) == 1

    async def test_provider_called_per_party(self) -> None:
        bob = make_person("bob", "Bob", "A harbour master")
        state = _make_state(parties={"bob": bob}, environment_reactive=False)
        eng, provider = _engine(state, ["Alice speaks.", "Bob responds."])
        await eng.run_episode()
        # alice + bob = 2 calls (no env)
        assert len(provider.calls) == 2

    async def test_environment_update_called_last(self) -> None:
        state = _make_state(environment_reactive=True)
        eng, _provider = _engine(state, ["Alice speaks.", "Town reacts."])
        episode = await eng.run_episode()
        # alice + env = 2 turns in episode
        assert len(episode.turns) == 2
        assert episode.turns[-1].party_id == "town"

    async def test_state_proposals_applied(self) -> None:
        state = _make_state(environment_reactive=False)
        eng, _ = _engine(state, ["I feel good.\nSTATE: mood=happy"])
        await eng.run_episode()
        alice = state.get_party("alice")
        assert alice.get_state("mood") == "happy"

    async def test_episode_closed_after_run(self) -> None:
        state = _make_state(environment_reactive=False)
        eng, _ = _engine(state, ["output"])
        episode = await eng.run_episode()
        assert episode.ended_at is not None

    async def test_memories_written_after_episode(self) -> None:
        state = _make_state(environment_reactive=False)
        mem_store = InMemoryStore()
        provider = MockProvider(["Alice spoke."])
        eng = SimulationEngine(state, provider, mem_store)  # type: ignore[arg-type]
        await eng.run_episode()
        count = await mem_store.entry_count("alice")
        assert count == 1

    async def test_state_changing_turn_gets_higher_importance(self) -> None:
        state = _make_state(environment_reactive=False)
        mem_store = InMemoryStore()
        provider = MockProvider(["output\nSTATE: mood=tense"])
        eng = SimulationEngine(state, provider, mem_store)  # type: ignore[arg-type]
        await eng.run_episode()
        entries = await mem_store.list_all("alice")
        assert entries[0].importance == 0.6

    async def test_passive_observation_written(self) -> None:
        bob = make_person("bob", "Bob", "A bystander")
        state = _make_state(
            parties={"bob": bob},
            passive_observation_parties=["bob"],
            environment_reactive=False,
        )
        # Use FixedOrderScheduler so only alice speaks
        from roleplay.core.episode import FixedOrderScheduler

        state.scheduler = FixedOrderScheduler(["alice"])
        mem_store = InMemoryStore()
        provider = MockProvider(["Alice spoke."])
        eng = SimulationEngine(state, provider, mem_store)  # type: ignore[arg-type]
        await eng.run_episode()
        bob_memories = await mem_store.list_all("bob")
        assert len(bob_memories) == 1
        assert bob_memories[0].importance == 0.3

    async def test_env_reactive_false_skips_env_turn(self) -> None:
        state = _make_state(environment_reactive=False)
        eng, _provider = _engine(state, ["Alice speaks."])
        episode = await eng.run_episode()
        assert len(episode.turns) == 1
        assert episode.turns[0].party_id == "alice"


# ---------------------------------------------------------------------------
# Observer integration
# ---------------------------------------------------------------------------


class TestObserverIntegration:
    async def test_before_episode_halt_raises(self) -> None:
        state = _make_state(environment_reactive=False)
        observer = MockObserver(before_directives=[ObserverDirective.halt("stop")])
        eng, _ = _engine(state, [], observer)
        with pytest.raises(Exception, match="stop"):
            await eng.run_episode()

    async def test_before_episode_called_with_index(self) -> None:
        state = _make_state(environment_reactive=False)
        observer = MockObserver()
        eng, _ = _engine(state, ["resp1", "env1", "resp2", "env2"], observer)
        await eng.run_episode()
        await eng.run_episode()
        assert observer.before_calls == [0, 1]

    async def test_after_turn_called(self) -> None:
        state = _make_state(environment_reactive=False)
        observer = MockObserver()
        eng, _ = _engine(state, ["response"], observer)
        await eng.run_episode()
        assert len(observer.after_turn_calls) == 1
        assert observer.after_turn_calls[0].party_id == "alice"

    async def test_after_turn_halt_stops_turns(self) -> None:
        bob = make_person("bob", "Bob", "desc")
        state = _make_state(parties={"bob": bob}, environment_reactive=False)
        observer = MockObserver(after_turn_directives=[ObserverDirective.halt("enough")])
        eng, _provider = _engine(state, ["Alice speaks.", "Bob speaks."], observer)
        episode = await eng.run_episode()
        # only alice's turn should be in episode (halt after alice)
        assert len(episode.turns) == 1

    async def test_after_episode_called(self) -> None:
        state = _make_state(environment_reactive=False)
        observer = MockObserver()
        eng, _ = _engine(state, ["response"], observer)
        await eng.run_episode()
        assert len(observer.after_episode_calls) == 1

    async def test_after_episode_halt_raises(self) -> None:
        state = _make_state(environment_reactive=False)
        observer = MockObserver(after_episode_directives=[ObserverDirective.halt("done")])
        eng, _ = _engine(state, ["response"], observer)
        with pytest.raises(RuntimeError):
            await eng.run_episode()

    async def test_inject_state_update_applied(self) -> None:
        state = _make_state(environment_reactive=False)
        payload = InjectionPayload(state_updates={"alice": {"mood": "nervous"}})
        observer = MockObserver(before_directives=[ObserverDirective.inject(payload)])
        eng, _ = _engine(state, ["Alice responds."], observer)
        await eng.run_episode()
        assert state.get_party("alice").get_state("mood") == "nervous"

    async def test_inject_context_override_in_prompt(self) -> None:
        state = _make_state(environment_reactive=False)
        payload = InjectionPayload(context_override="A fire breaks out!")
        observer = MockObserver(before_directives=[ObserverDirective.inject(payload)])
        eng, provider = _engine(state, ["Alice responds."], observer)
        await eng.run_episode()
        assert any("fire" in req.prompt for req in provider.calls)

    async def test_inject_memory_write(self) -> None:
        state = _make_state(environment_reactive=False)
        mem_store = InMemoryStore()
        entry = MemoryEntry(
            party_id="alice",
            kind=MemoryKind.SEMANTIC,
            content="Alice knows a secret",
            episode_index=0,
        )
        payload = InjectionPayload(memory_writes=[entry])
        observer = MockObserver(before_directives=[ObserverDirective.inject(payload)])
        provider = MockProvider(["response"])
        eng = SimulationEngine(state, provider, mem_store, observer=observer)  # type: ignore[arg-type]
        await eng.run_episode()
        entries = await mem_store.list_all("alice")
        contents = [e.content for e in entries]
        assert "Alice knows a secret" in contents


# ---------------------------------------------------------------------------
# SimulationEngine.run()
# ---------------------------------------------------------------------------


class TestRun:
    async def test_run_max_episodes(self) -> None:
        state = _make_state(environment_reactive=False)
        eng, _ = _engine(state, ["r1", "r2", "r3"])
        await eng.run(max_episodes=3)
        assert len(state.history.completed_episodes()) == 3

    async def test_run_halts_on_observer(self) -> None:
        state = _make_state(environment_reactive=False)
        observer = MockObserver(
            before_directives=[
                ObserverDirective.continue_(),
                ObserverDirective.halt("stop after ep 1"),
            ]
        )
        eng, _ = _engine(state, ["r1", "r2"], observer)
        await eng.run(max_episodes=5)
        assert len(state.history.completed_episodes()) == 1


# ---------------------------------------------------------------------------
# _assemble_prompt — unknown party_id fallback and budget trimming
# ---------------------------------------------------------------------------


def _make_state_for_prompt() -> SimulationState:
    alice = make_person("alice", "Alice", "A person")
    env = make_environment("world", "World", "A place")
    return SimulationState(
        config=SimulationConfig(session_id="s"),
        parties={"alice": alice},
        environment=env,
        history=SimulationHistory(),
        scheduler=RoundRobinScheduler(),
        clock=NoopClock(),
    )


class TestAssemblePromptEdgeCases:
    def test_unknown_party_id_in_history_uses_raw_id(self) -> None:
        """If a turn's party_id is not in state, fall back to the raw ID."""
        from roleplay.core.episode import Episode, Turn

        state = _make_state_for_prompt()
        ep = Episode(index=0, turns=[], simulated_time_start="t0")
        # Add a turn with an unknown party_id
        ep.add_turn(Turn(party_id="ghost", index=0, output="Boo!", state_update_proposals={}))
        ep.close("t1")
        state.history.episodes.append(ep)

        prompt = _assemble_prompt("alice", state, [], [], None, 10_000)
        assert "ghost" in prompt or "Boo!" in prompt

    def test_unknown_party_id_in_current_turns_uses_raw_id(self) -> None:
        """Unknown party_id in current_turns falls back to raw ID."""
        from roleplay.engine.turn import Turn as EngTurn

        state = _make_state_for_prompt()
        fake_turn = EngTurn(party_id="mystery", output="Hello", state_update_proposals={})
        prompt = _assemble_prompt("alice", state, [], [fake_turn], None, 10_000)
        assert "mystery" in prompt or "Hello" in prompt

    def test_context_override_included_in_prompt(self) -> None:
        state = _make_state_for_prompt()
        prompt = _assemble_prompt("alice", state, [], [], "A storm arrives!", 10_000)
        assert "storm" in prompt

    def test_budget_trimming_history(self) -> None:
        """When total exceeds budget, history is trimmed first."""
        from roleplay.core.episode import Episode, Turn

        state = _make_state_for_prompt()
        long_text = "X" * 500
        ep = Episode(index=0, turns=[], simulated_time_start="t0")
        ep.add_turn(Turn(party_id="alice", index=0, output=long_text, state_update_proposals={}))
        ep.close("t1")
        state.history.episodes.append(ep)

        # Tiny budget forces trimming
        prompt = _assemble_prompt("alice", state, [], [], None, 200)
        # Should not exceed budget by much (history trimmed to fit)
        assert len(prompt) < 600

    def test_budget_trimming_memory(self) -> None:
        """When history is empty, memory is trimmed instead."""
        long_mem = MemoryEntry(
            party_id="alice",
            kind=MemoryKind.SEMANTIC,
            content="M" * 500,
            episode_index=0,
        )
        state = _make_state_for_prompt()
        prompt = _assemble_prompt("alice", state, [long_mem], [], None, 200)
        assert len(prompt) < 600


# ---------------------------------------------------------------------------
# _parse_state_proposals — float and string fallback paths
# ---------------------------------------------------------------------------


class TestParseStateProposalsEdgeCases:
    def test_float_value_parsed(self) -> None:
        text = "Alice speaks.\nSTATE: confidence=0.75"
        output, proposals = _parse_state_proposals(text)
        assert proposals["confidence"] == pytest.approx(0.75)
        assert "STATE:" not in output

    def test_string_fallback_value(self) -> None:
        text = "Turn.\nSTATE: note=hello world"
        _, proposals = _parse_state_proposals(text)
        assert proposals["note"] == "hello world"


# ---------------------------------------------------------------------------
# _apply_injection — unknown party_id warnings
# ---------------------------------------------------------------------------


class TestApplyInjection:
    async def test_unknown_party_id_state_update_warns(self) -> None:
        state = _make_state(environment_reactive=False)
        payload = InjectionPayload(state_updates={"nonexistent": {"mood": "sad"}})
        observer = MockObserver(before_directives=[ObserverDirective.inject(payload)])
        eng, _ = _engine(state, ["response"], observer)
        # Should not raise — just logs a warning
        await eng.run_episode()

    async def test_unknown_party_id_persona_override_warns(self) -> None:
        state = _make_state(environment_reactive=False)
        payload = InjectionPayload(persona_overrides={"nonexistent": {"description": "X"}})
        observer = MockObserver(before_directives=[ObserverDirective.inject(payload)])
        eng, _ = _engine(state, ["response"], observer)
        # Should not raise — just logs a warning
        await eng.run_episode()
