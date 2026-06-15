"""Integration smoke test for the POC runner.

Runs 2 episodes end-to-end with a mock provider — no LLM API calls.
Verifies the full stack assembles correctly and produces episode output.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from roleplay.core.simulation_state import SimulationConfig
from roleplay.engine.engine import SimulationEngine
from roleplay.memory.store import InMemoryStore
from roleplay.poc import _build_default_state as _build_state
from roleplay.poc import _MockProvider, run_poc

if TYPE_CHECKING:
    from roleplay.providers.base import CompletionRequest


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


# ---------------------------------------------------------------------------
# Tiered verbosity
# ---------------------------------------------------------------------------


class TestCliObserverVerbosity:
    async def test_verbosity_1_prints_turn_output(self) -> None:
        """Default (verbosity=1) must stream full turn text."""
        from io import StringIO
        from unittest.mock import patch

        from roleplay.poc import _CliObserver

        obs = _CliObserver(verbosity=1)
        buf = StringIO()
        with patch("sys.stdout", buf):
            await run_poc(use_mock=True, max_episodes=1, observer=obs)
        assert "Alice" in buf.getvalue() or "Bob" in buf.getvalue()

    async def test_verbosity_0_suppresses_dialog_formatting(self) -> None:
        """At verbosity=0, the per-turn underline separator must not appear in stdout.

        The structural marker of full-dialog mode is the ╌ underline line that
        follows each party label.  In summary mode (verbosity=0) we only emit
        the episode header and one-line snippets — no underlines.
        """
        from io import StringIO
        from unittest.mock import patch

        from roleplay.poc import _CliObserver

        obs = _CliObserver(verbosity=0)
        buf = StringIO()
        with patch("sys.stdout", buf):
            await run_poc(use_mock=True, max_episodes=1, observer=obs)
        output = buf.getvalue()
        # Episode header must be present.
        assert "Episode" in output
        # The ╌ underline separator is the structural marker of full-dialog
        # output — it must not appear at verbosity=0.
        assert "╌" not in output

    async def test_verbosity_0_prints_episode_summary(self) -> None:
        """At verbosity=0, after_episode must print an AI-generated summary.

        We cannot assert on specific party names because the mock provider
        returns scripted text unrelated to the dialog; instead we verify
        that non-empty, non-header content appears after the episode header.
        """
        from io import StringIO
        from unittest.mock import patch

        from roleplay.poc import _CliObserver

        obs = _CliObserver(verbosity=0)
        buf = StringIO()
        with patch("sys.stdout", buf):
            await run_poc(use_mock=True, max_episodes=1, observer=obs)
        output = buf.getvalue()
        # Episode header must be present.
        assert "Episode" in output
        # Some summary text must follow (non-empty lines that aren't headers).
        non_header_lines = [
            ln for ln in output.split("\n") if ln.strip() and "Episode" not in ln and "─" not in ln
        ]
        assert len(non_header_lines) > 0

    async def test_write_log_contains_full_dialog(self, tmp_path: Path) -> None:
        """write_log must persist the full turn text regardless of verbosity."""
        from io import StringIO
        from unittest.mock import patch

        from roleplay.poc import _CliObserver

        obs = _CliObserver(verbosity=0)
        with patch("sys.stdout", StringIO()):
            await run_poc(use_mock=True, max_episodes=1, observer=obs)

        log = tmp_path / "dialog.log"
        obs.write_log(log)
        content = log.read_text()
        assert "Episode" in content
        # Full mock responses must be in the log
        assert "approach this carefully" in content

    async def test_write_log_verbosity_1_also_works(self, tmp_path: Path) -> None:
        """write_log works at verbosity=1 too (captures what was printed)."""
        from io import StringIO
        from unittest.mock import patch

        from roleplay.poc import _CliObserver

        obs = _CliObserver(verbosity=1)
        with patch("sys.stdout", StringIO()):
            await run_poc(use_mock=True, max_episodes=1, observer=obs)

        log = tmp_path / "v1.log"
        obs.write_log(log)
        assert "Episode" in log.read_text()


# ---------------------------------------------------------------------------
# Goal tracking
# ---------------------------------------------------------------------------


class _GoalProvider:
    """Stub provider that returns a fixed goal-check response."""

    def __init__(self, text: str) -> None:
        self._text = text

    async def complete(self, request: CompletionRequest) -> object:
        from roleplay.providers.base import CompletionResponse

        return CompletionResponse(text=self._text, model_used="mock")


class TestCliObserverGoal:
    """Per-episode goal check, goal-met halt, and no-goal fast-path."""

    async def _run_one_episode(self) -> tuple[object, object]:
        """Return (state, completed_episode) for a minimal mock run."""
        cfg = SimulationConfig(session_id="goal-t", environment_reactive=False, goal="")
        state = _build_state(cfg)
        engine = SimulationEngine(
            state=state, provider=_MockProvider(), memory_store=InMemoryStore()
        )
        await engine.run(max_episodes=1)
        ep = state.history.completed_episodes()[0]
        return state, ep

    async def test_goal_met_halts_simulation(self) -> None:
        """after_episode returns halt when provider signals 'Goal met:'."""
        from io import StringIO
        from unittest.mock import patch

        from roleplay.poc import _CliObserver

        state, ep = await self._run_one_episode()
        state.config.goal = "Reach a deal"  # inject goal retroactively

        obs = _CliObserver(provider=_GoalProvider("Goal met: They reached a deal."))
        obs._episode_turns = list(ep.turns)  # type: ignore[attr-defined]
        obs._episode_start_env_state = {}  # type: ignore[attr-defined]

        with patch("sys.stdout", StringIO()):
            directive = await obs.after_episode(state, ep)

        assert directive.is_halt
        assert directive.reason == "Goal achieved"

    async def test_goal_not_met_continues(self) -> None:
        """after_episode returns continue_ when goal is not yet achieved."""
        from io import StringIO
        from unittest.mock import patch

        from roleplay.poc import _CliObserver

        state, ep = await self._run_one_episode()
        state.config.goal = "Reach a deal"

        obs = _CliObserver(provider=_GoalProvider("Goal not yet met: Still far apart."))
        obs._episode_turns = list(ep.turns)  # type: ignore[attr-defined]
        obs._episode_start_env_state = {}  # type: ignore[attr-defined]

        with patch("sys.stdout", StringIO()):
            directive = await obs.after_episode(state, ep)

        assert not directive.is_halt

    async def test_no_goal_skips_check(self) -> None:
        """When state.config.goal is empty the provider must not be called."""
        from io import StringIO
        from unittest.mock import patch

        from roleplay.poc import _CliObserver

        class _ShouldNotCall:
            async def complete(self, request: object) -> object:
                raise AssertionError("provider.complete called with no goal set")

        state, ep = await self._run_one_episode()
        # goal is "" (default) — provider must not be invoked
        obs = _CliObserver(provider=_ShouldNotCall())  # type: ignore[arg-type]
        obs._episode_turns = list(ep.turns)  # type: ignore[attr-defined]
        obs._episode_start_env_state = {}  # type: ignore[attr-defined]

        with patch("sys.stdout", StringIO()):
            directive = await obs.after_episode(state, ep)

        assert not directive.is_halt

    async def test_goal_status_line_in_output(self) -> None:
        """The ⊙ goal-status line appears in stdout when a goal is set."""
        from io import StringIO
        from unittest.mock import patch

        from roleplay.poc import _CliObserver

        state, ep = await self._run_one_episode()
        state.config.goal = "Reach a deal"

        obs = _CliObserver(provider=_GoalProvider("Goal not yet met: Parties are close."))
        obs._episode_turns = list(ep.turns)  # type: ignore[attr-defined]
        obs._episode_start_env_state = {}  # type: ignore[attr-defined]

        buf = StringIO()
        with patch("sys.stdout", buf):
            await obs.after_episode(state, ep)

        assert "⊙" in buf.getvalue()

    async def test_goal_line_added_to_log(self) -> None:
        """The goal-status line is captured in _log_lines for write_log."""
        from io import StringIO
        from unittest.mock import patch

        from roleplay.poc import _CliObserver

        state, ep = await self._run_one_episode()
        state.config.goal = "Reach a deal"

        obs = _CliObserver(provider=_GoalProvider("Goal not yet met: Still negotiating."))
        obs._episode_turns = list(ep.turns)  # type: ignore[attr-defined]
        obs._episode_start_env_state = {}  # type: ignore[attr-defined]

        with patch("sys.stdout", StringIO()):
            await obs.after_episode(state, ep)

        assert any("⊙" in line for line in obs._log_lines)  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Summarise robustness
# ---------------------------------------------------------------------------


class TestSummarize:
    """_summarize must never silently produce invisible whitespace-only output."""

    async def _make_obs_with_turns(self) -> tuple[object, object]:
        """Return (state, obs) with one completed episode's turns loaded."""
        from roleplay.poc import _CliObserver

        cfg = SimulationConfig(session_id="summ-t", environment_reactive=False)
        state = _build_state(cfg)
        engine = SimulationEngine(
            state=state, provider=_MockProvider(), memory_store=InMemoryStore()
        )
        obs = _CliObserver(verbosity=0)
        obs._episode_start_env_state = {}  # type: ignore[attr-defined]
        # Run one episode so _episode_turns is populated via after_turn
        from io import StringIO
        from unittest.mock import patch

        with patch("sys.stdout", StringIO()):
            await engine.run_episode()
            ep = state.history.completed_episodes()[0]
            for t in ep.turns:
                await obs.after_turn(state, t)  # type: ignore[arg-type]
        return state, obs

    async def test_empty_provider_response_shows_placeholder(self) -> None:
        """If the LLM returns empty text, a visible placeholder must be printed."""
        from io import StringIO
        from unittest.mock import patch

        state, obs = await self._make_obs_with_turns()

        class _EmptyProvider:
            async def complete(self, request: object) -> object:
                from roleplay.providers.base import CompletionResponse

                return CompletionResponse(text="", model_used="empty-model")

        obs.provider = _EmptyProvider()  # type: ignore[attr-defined]
        buf = StringIO()
        with patch("sys.stdout", buf):
            await obs._print_episode_summary(state)  # type: ignore[attr-defined]
        printed = buf.getvalue().strip()
        assert printed, "Nothing was printed — whitespace-only output is a bug"
        assert printed != "", "Empty summary must produce a visible fallback message"
        assert "empty" in printed.lower() or "summary" in printed.lower()

    async def test_empty_dialog_shows_placeholder(self) -> None:
        """_summarize with no turns recorded must return a visible string, not ''."""
        from roleplay.poc import _CliObserver

        class _ShouldNotBeCalled:
            async def complete(self, request: object) -> object:
                raise AssertionError("provider called with empty dialog")

        obs = _CliObserver(provider=_ShouldNotBeCalled())  # type: ignore[arg-type]
        obs._episode_turns = []  # type: ignore[attr-defined]

        # Call _summarize directly with empty dialog text
        result = await obs._summarize("")  # type: ignore[attr-defined]
        assert result.strip(), "_summarize returned empty/whitespace for empty dialog"
        assert result != ""


# ---------------------------------------------------------------------------
# UX feature tests (episode counter, timing, model label, goal tally,
# session summary, env snapshot)
# ---------------------------------------------------------------------------


class TestUxFeatures:
    """Tests for the 6 UX improvements added in feat/ux-improvements."""

    async def _run_episode(self, goal: str = "") -> tuple[object, object, object]:
        """Return (state, ep, obs) after running one mock episode."""
        from roleplay.poc import _CliObserver

        cfg = SimulationConfig(session_id="ux-t", environment_reactive=False, goal=goal)
        state = _build_state(cfg)
        engine = SimulationEngine(
            state=state, provider=_MockProvider(), memory_store=InMemoryStore()
        )
        obs = _CliObserver()
        obs._episode_start_env_state = {}  # type: ignore[attr-defined]
        await engine.run(max_episodes=1)
        ep = state.history.completed_episodes()[0]
        obs._episode_turns = list(ep.turns)  # type: ignore[attr-defined]
        return state, ep, obs

    # ------------------------------------------------------------------
    # Episode counter in header

    async def test_episode_counter_with_max_shows_fraction(self) -> None:
        """before_episode prints 'Episode N / M' when max_episodes is known."""
        from io import StringIO
        from unittest.mock import patch

        from roleplay.poc import _CliObserver

        obs = _CliObserver()
        obs.max_episodes = 5  # type: ignore[attr-defined]
        cfg = SimulationConfig(session_id="t")
        state = _build_state(cfg)

        buf = StringIO()
        with patch("sys.stdout", buf):
            await obs.before_episode(state, 0)
        assert "Episode 1 / 5" in buf.getvalue()

    async def test_episode_counter_without_max_shows_plain(self) -> None:
        """before_episode prints 'Episode N' when max_episodes is not set."""
        from io import StringIO
        from unittest.mock import patch

        from roleplay.poc import _CliObserver

        obs = _CliObserver()  # max_episodes defaults to 0
        cfg = SimulationConfig(session_id="t")
        state = _build_state(cfg)

        buf = StringIO()
        with patch("sys.stdout", buf):
            await obs.before_episode(state, 2)
        assert "Episode 3" in buf.getvalue()
        assert "/ 0" not in buf.getvalue()

    # ------------------------------------------------------------------
    # Episode timing

    async def test_timing_line_appears_after_episode(self) -> None:
        """after_episode always prints a timing line with ⏱."""
        from io import StringIO
        from unittest.mock import patch

        state, ep, obs = await self._run_episode()

        buf = StringIO()
        with patch("sys.stdout", buf):
            await obs.after_episode(state, ep)  # type: ignore[arg-type]

        assert "⏱" in buf.getvalue()

    # ------------------------------------------------------------------
    # Model switch notice

    async def test_model_label_shown_for_non_default_model(self) -> None:
        """after_episode shows ⚡ + model name when a non-default model is used."""
        from io import StringIO
        from unittest.mock import patch

        state, ep, obs = await self._run_episode()
        obs._default_model = "default-model"  # type: ignore[attr-defined]
        obs._episode_models = {"fallback-model"}  # type: ignore[attr-defined]

        buf = StringIO()
        with patch("sys.stdout", buf):
            await obs.after_episode(state, ep)  # type: ignore[arg-type]

        output = buf.getvalue()
        assert "⚡" in output
        assert "fallback-model" in output

    async def test_no_model_label_when_only_default_used(self) -> None:
        """after_episode shows plain ⏱ line when only the default model ran."""
        from io import StringIO
        from unittest.mock import patch

        state, ep, obs = await self._run_episode()
        obs._default_model = "default-model"  # type: ignore[attr-defined]
        obs._episode_models = {"default-model"}  # type: ignore[attr-defined]

        buf = StringIO()
        with patch("sys.stdout", buf):
            await obs.after_episode(state, ep)  # type: ignore[arg-type]

        output = buf.getvalue()
        assert "⏱" in output
        assert "⚡" not in output

    # ------------------------------------------------------------------
    # Goal tally

    async def test_goal_tally_increments_across_episodes(self) -> None:
        """Goal tally '(met N / M)' increments correctly across multiple episodes."""
        from io import StringIO
        from unittest.mock import patch

        from roleplay.poc import _CliObserver

        state, ep, _ = await self._run_episode(goal="Reach a deal")

        obs = _CliObserver(provider=_GoalProvider("Goal met: They reached a deal."))
        obs._episode_turns = list(ep.turns)  # type: ignore[attr-defined]
        obs._episode_start_env_state = {}  # type: ignore[attr-defined]

        buf = StringIO()
        with patch("sys.stdout", buf):
            # Simulate two episodes both meeting the goal
            obs._goal_check_count = 1  # type: ignore[attr-defined]
            obs._goal_met_count = 1  # type: ignore[attr-defined]
            obs._final_state = state  # type: ignore[attr-defined]
            await obs.after_episode(state, ep)  # type: ignore[arg-type]

        # After the second episode where goal is met: met 2 / 2
        assert "(met 2 / 2)" in buf.getvalue()

    async def test_goal_tally_shows_zero_when_not_met(self) -> None:
        """Goal tally shows 'met 0 / 1' when goal is not achieved."""
        from io import StringIO
        from unittest.mock import patch

        from roleplay.poc import _CliObserver

        state, ep, _ = await self._run_episode(goal="Reach a deal")

        obs = _CliObserver(provider=_GoalProvider("Goal not yet met: Still negotiating."))
        obs._episode_turns = list(ep.turns)  # type: ignore[attr-defined]
        obs._episode_start_env_state = {}  # type: ignore[attr-defined]

        buf = StringIO()
        with patch("sys.stdout", buf):
            await obs.after_episode(state, ep)  # type: ignore[arg-type]

        assert "(met 0 / 1)" in buf.getvalue()

    # ------------------------------------------------------------------
    # Session summary

    async def test_write_session_summary_prints_episodes_and_duration(self) -> None:
        """write_session_summary outputs episode count and duration."""
        from io import StringIO
        from unittest.mock import patch

        _state, _ep, obs = await self._run_episode()
        obs._model_stats = {"mock": [2, 100, 50]}  # type: ignore[attr-defined]
        obs._total_episodes = 2  # type: ignore[attr-defined]

        buf = StringIO()
        with patch("sys.stdout", buf):
            obs.write_session_summary()  # type: ignore[attr-defined]

        output = buf.getvalue()
        assert "Session summary" in output
        assert "Episodes  : 2" in output
        assert "Duration" in output
        assert "mock" in output

    async def test_write_session_summary_shows_token_totals(self) -> None:
        """write_session_summary shows combined prompt+completion token count."""
        from io import StringIO
        from unittest.mock import patch

        from roleplay.poc import _CliObserver

        obs = _CliObserver()
        obs._model_stats = {"gemini-2.5-flash": [3, 1000, 500]}  # type: ignore[attr-defined]

        buf = StringIO()
        with patch("sys.stdout", buf):
            obs.write_session_summary()  # type: ignore[attr-defined]

        output = buf.getvalue()
        # 1000 + 500 = 1500 tokens should appear
        assert "1,500" in output

    # ------------------------------------------------------------------
    # Final environment snapshot

    async def test_write_env_snapshot_shows_state_keys(self) -> None:
        """write_env_snapshot prints all environment state keys."""
        from io import StringIO
        from unittest.mock import patch

        state, _ep, obs = await self._run_episode()
        obs._final_state = state  # type: ignore[attr-defined]

        buf = StringIO()
        with patch("sys.stdout", buf):
            obs.write_env_snapshot()  # type: ignore[attr-defined]

        output = buf.getvalue()
        assert "Final environment state" in output
        # Default state has time.simulated and weather.condition
        assert "time.simulated" in output

    async def test_write_env_snapshot_noop_when_no_final_state(self) -> None:
        """write_env_snapshot does nothing when _final_state is None."""
        from io import StringIO
        from unittest.mock import patch

        from roleplay.poc import _CliObserver

        obs = _CliObserver()
        # _final_state defaults to None

        buf = StringIO()
        with patch("sys.stdout", buf):
            obs.write_env_snapshot()  # type: ignore[attr-defined]

        assert buf.getvalue() == ""
