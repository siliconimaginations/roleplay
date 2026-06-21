"""Unit tests for SessionRunner and ApiObserverHook."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from roleplay.api.runner import ApiObserverHook, SessionRunner, _build_registry


class TestBuildRegistry:
    def test_mock_always_registered(self) -> None:
        reg = _build_registry()
        assert "mock" in reg

    def test_gemini_skipped_if_unavailable(self) -> None:
        with patch(
            "roleplay.providers.gemini.GeminiProvider.__init__",
            side_effect=RuntimeError("no key"),
        ):
            reg = _build_registry()
        assert "mock" in reg

    def test_claude_skipped_if_unavailable(self) -> None:
        with patch(
            "roleplay.providers.claude_provider.ClaudeProvider.__init__",
            side_effect=RuntimeError("no key"),
        ):
            reg = _build_registry()
        assert "mock" in reg

    def test_get_mock_provider(self) -> None:
        reg = _build_registry()
        provider = reg.get("mock")
        assert provider is not None


class TestSessionRunnerControl:
    def test_initial_state(self) -> None:
        runner = SessionRunner("s1")
        assert runner.status == "idle"
        assert runner.episodes_completed == 0
        assert runner.episodes_requested == 0
        assert runner.error is None

    def test_request_pause_sets_flag(self) -> None:
        runner = SessionRunner("s1")
        runner.request_pause()
        assert runner._pause_requested is True

    async def test_inject_stores_text(self) -> None:
        runner = SessionRunner("s1")
        await runner.inject("something dramatic")
        assert runner._pending_injection == "something dramatic"

    async def test_subscribe_returns_queue(self) -> None:
        runner = SessionRunner("s1")
        q = runner.subscribe()
        assert q in runner._subscribers

    async def test_unsubscribe_removes_queue(self) -> None:
        runner = SessionRunner("s1")
        q = runner.subscribe()
        runner.unsubscribe(q)
        assert q not in runner._subscribers

    async def test_unsubscribe_nonexistent_is_safe(self) -> None:
        runner = SessionRunner("s1")
        q: asyncio.Queue = asyncio.Queue()
        runner.unsubscribe(q)  # should not raise

    async def test_broadcast_puts_to_all_subscribers(self) -> None:
        runner = SessionRunner("s1")
        q1 = runner.subscribe()
        q2 = runner.subscribe()
        await runner._broadcast({"type": "test"})
        assert q1.get_nowait() == {"type": "test"}
        assert q2.get_nowait() == {"type": "test"}

    async def test_broadcast_drops_when_queue_full(self) -> None:
        from roleplay.api.runner import _QUEUE_MAXSIZE

        runner = SessionRunner("s1")
        q = runner.subscribe()
        for _ in range(_QUEUE_MAXSIZE):
            q.put_nowait({"type": "filler"})
        # Should not raise even when full
        await runner._broadcast({"type": "overflow"})

    def test_start_already_running_raises(self) -> None:
        runner = SessionRunner("s1")
        runner.status = "running"
        with pytest.raises(RuntimeError, match="already running"):
            runner.start(MagicMock(), MagicMock(), 1)


class TestApiObserverHook:
    def _make_runner(self) -> SessionRunner:
        r = SessionRunner("s1")
        r.status = "running"
        return r

    def _make_state(self) -> MagicMock:
        state = MagicMock()
        state.history.completed_episodes.return_value = []
        return state

    def _make_provider(self) -> MagicMock:
        provider = MagicMock()
        resp = MagicMock()
        resp.text = "Alice and Bob discussed the experiment."

        async def _complete(req: object) -> MagicMock:
            return resp

        provider.complete = _complete
        return provider

    def _make_layer(self) -> MagicMock:
        layer = MagicMock()

        async def _save_episode(session_id: object, ep: object) -> None:
            pass

        layer.save_episode = _save_episode
        return layer

    async def test_before_episode_broadcasts_start(self) -> None:
        runner = self._make_runner()
        hook = ApiObserverHook(runner, self._make_provider(), self._make_layer())
        state = self._make_state()
        q = runner.subscribe()

        directive = await hook.before_episode(state, 0)

        assert not directive.is_halt
        event = q.get_nowait()
        assert event["type"] == "episode_start"
        assert event["episode"] == 0

    async def test_before_episode_halts_when_paused(self) -> None:
        runner = self._make_runner()
        runner._pause_requested = True
        hook = ApiObserverHook(runner, self._make_provider(), self._make_layer())
        state = self._make_state()

        directive = await hook.before_episode(state, 0)

        assert directive.is_halt
        assert runner.status == "paused"
        assert runner._pause_requested is False

    async def test_after_turn_broadcasts_turn(self) -> None:
        runner = self._make_runner()
        hook = ApiObserverHook(runner, self._make_provider(), self._make_layer())
        state = self._make_state()
        q = runner.subscribe()

        turn = MagicMock()
        turn.party_id = "alice"
        turn.output = "Hello!"
        turn.state_update_proposals = {}

        directive = await hook.after_turn(state, turn)

        assert not directive.is_halt
        event = q.get_nowait()
        assert event["type"] == "turn"
        assert event["party_id"] == "alice"

    async def test_after_episode_increments_count(self) -> None:
        runner = self._make_runner()
        hook = ApiObserverHook(runner, self._make_provider(), self._make_layer())
        state = self._make_state()
        state.history.completed_episodes.return_value = [MagicMock()]
        q = runner.subscribe()

        ep = MagicMock()
        ep.turns = []  # empty — skips summary generation
        ep.index = 0
        directive = await hook.after_episode(state, ep)

        assert not directive.is_halt
        assert runner.episodes_completed == 1
        event = q.get_nowait()
        assert event["type"] == "episode_end"
        assert "summary" in event

    # ------------------------------------------------------------------
    # Goal-achievement tests
    # ------------------------------------------------------------------

    def _make_provider_with_goal_response(self, response_text: str) -> MagicMock:
        """Provider whose .complete() returns a fixed goal-check string."""
        provider = MagicMock()
        resp = MagicMock()
        resp.text = response_text

        async def _complete(req: object) -> MagicMock:
            return resp

        provider.complete = _complete
        return provider

    def _make_episode_with_turns(self) -> MagicMock:
        ep = MagicMock()
        turn = MagicMock()
        turn.party_id = "alice"
        turn.output = "We have reached full agreement on all terms."
        ep.turns = [turn]
        ep.index = 0
        return ep

    async def test_goal_met_halts_and_broadcasts(self) -> None:
        """When LLM says GOAL MET, runner halts and emits goal_achieved event."""
        provider = self._make_provider_with_goal_response(
            "GOAL MET: Alice and Bob reached full agreement."
        )
        runner = self._make_runner()
        hook = ApiObserverHook(runner, provider, self._make_layer())

        state = self._make_state()
        state.config.goal = "Reach a trade agreement"
        state.history.completed_episodes.return_value = []
        q = runner.subscribe()

        ep = self._make_episode_with_turns()
        directive = await hook.after_episode(state, ep)

        assert directive.is_halt
        assert runner.goal_achieved is True
        assert runner.goal_status.startswith("GOAL MET:")

        events = []
        while not q.empty():
            events.append(q.get_nowait())
        event_types = [e["type"] for e in events]
        assert "goal_achieved" in event_types
        goal_event = next(e for e in events if e["type"] == "goal_achieved")
        assert "GOAL MET" in goal_event["status"]

    async def test_goal_not_met_continues(self) -> None:
        """When LLM says GOAL NOT MET, simulation continues normally."""
        provider = self._make_provider_with_goal_response(
            "GOAL NOT MET: Parties haven't agreed on pricing yet."
        )
        runner = self._make_runner()
        hook = ApiObserverHook(runner, provider, self._make_layer())

        state = self._make_state()
        state.config.goal = "Reach a trade agreement"
        state.history.completed_episodes.return_value = []

        ep = self._make_episode_with_turns()
        directive = await hook.after_episode(state, ep)

        assert not directive.is_halt
        assert runner.goal_achieved is False

    async def test_no_goal_skips_check(self) -> None:
        """When config has no goal, goal check is skipped entirely."""
        provider = self._make_provider_with_goal_response("GOAL MET: irrelevant")
        runner = self._make_runner()
        hook = ApiObserverHook(runner, provider, self._make_layer())

        state = self._make_state()
        state.config.goal = ""  # no goal
        state.history.completed_episodes.return_value = []

        ep = self._make_episode_with_turns()
        directive = await hook.after_episode(state, ep)

        assert not directive.is_halt
        assert runner.goal_achieved is False

    async def test_goal_check_empty_turns_skips(self) -> None:
        """When episode has no turns, goal check is skipped."""
        called = []

        provider = MagicMock()

        async def _complete(req: object) -> MagicMock:
            called.append(True)
            resp = MagicMock()
            resp.text = "GOAL MET: x"
            return resp

        provider.complete = _complete

        runner = self._make_runner()
        hook = ApiObserverHook(runner, provider, self._make_layer())

        state = self._make_state()
        state.config.goal = "Reach agreement"
        state.history.completed_episodes.return_value = []

        ep = MagicMock()
        ep.turns = []  # empty
        ep.index = 0
        directive = await hook.after_episode(state, ep)

        assert not directive.is_halt
        assert runner.goal_achieved is False
        # complete() should not have been called for goal checking
        # (it may have been called for summary — we just care goal didn't fire)

    async def test_goal_achieved_fields_on_runner(self) -> None:
        """SessionRunner starts with goal_achieved=False, goal_status=''."""
        runner = SessionRunner("new-session")
        assert runner.goal_achieved is False
        assert runner.goal_status == ""

    # ------------------------------------------------------------------
    # Summary quality tests
    # ------------------------------------------------------------------

    async def test_lowercase_fragment_discarded(self) -> None:
        """Summary starting with lowercase is discarded as a continuation artifact."""
        provider = MagicMock()
        resp = MagicMock()
        resp.text = "in a high-stakes negotiation to meet the deadline."

        async def _complete(req: object) -> MagicMock:
            return resp

        provider.complete = _complete

        runner = self._make_runner()
        hook = ApiObserverHook(runner, provider, self._make_layer())

        state = self._make_state()
        state.config.goal = ""
        state.history.completed_episodes.return_value = []

        ep = MagicMock()
        turn = MagicMock()
        turn.party_id = "alice"
        turn.output = "Let's talk."
        ep.turns = [turn]
        ep.index = 0
        await hook.after_episode(state, ep)

        # Fragment should be discarded — summary should be empty string
        assert ep.summary == ""

    async def test_valid_summary_kept(self) -> None:
        """Summary starting with uppercase is kept as-is."""
        provider = MagicMock()
        resp = MagicMock()
        resp.text = "Alice and Bob reached a preliminary agreement on pricing."

        async def _complete(req: object) -> MagicMock:
            return resp

        provider.complete = _complete

        runner = self._make_runner()
        hook = ApiObserverHook(runner, provider, self._make_layer())

        state = self._make_state()
        state.config.goal = ""
        state.history.completed_episodes.return_value = []

        ep = MagicMock()
        turn = MagicMock()
        turn.party_id = "alice"
        turn.output = "I agree to the terms."
        ep.turns = [turn]
        ep.index = 0
        await hook.after_episode(state, ep)

        assert ep.summary == "Alice and Bob reached a preliminary agreement on pricing."

    async def test_capitalized_fragment_without_punctuation_kept(self) -> None:
        """Summary starting uppercase is kept even without terminal punctuation.

        The terminal-punctuation check was removed: the 6000-char dialog
        truncation already prevents cut-off responses, so requiring a full-stop
        was over-filtering.  A fragmentary start-cap is still better than
        showing nothing at all.
        """
        provider = MagicMock()
        resp = MagicMock()
        resp.text = "In a high-stakes negotiation to meet Google Aggressive Q3"

        async def _complete(req: object) -> MagicMock:
            return resp

        provider.complete = _complete

        runner = self._make_runner()
        hook = ApiObserverHook(runner, provider, self._make_layer())

        state = self._make_state()
        state.config.goal = ""
        state.history.completed_episodes.return_value = []

        ep = MagicMock()
        turn = MagicMock()
        turn.party_id = "alice"
        turn.output = "We need to close this deal."
        ep.turns = [turn]
        ep.index = 0
        await hook.after_episode(state, ep)

        # Starts with uppercase → kept (no terminal-punctuation requirement)
        assert ep.summary == "In a high-stakes negotiation to meet Google Aggressive Q3"

    async def test_long_dialog_truncated_in_prompt(self) -> None:
        """Dialog longer than 6000 chars is truncated before being sent to the LLM."""
        received_prompts: list[str] = []

        provider = MagicMock()
        resp = MagicMock()
        resp.text = "Parties concluded their lengthy discussion."

        async def _complete(req: object) -> MagicMock:
            from roleplay.providers.base import CompletionRequest

            if isinstance(req, CompletionRequest):
                received_prompts.append(req.prompt)
            return resp

        provider.complete = _complete

        runner = self._make_runner()
        hook = ApiObserverHook(runner, provider, self._make_layer())

        state = self._make_state()
        state.config.goal = ""
        state.history.completed_episodes.return_value = []

        ep = MagicMock()
        turn = MagicMock()
        turn.party_id = "alice"
        # Generate a very long output (> 6000 chars)
        turn.output = "word " * 2000
        ep.turns = [turn]
        ep.index = 0
        await hook.after_episode(state, ep)

        # The prompt sent to the LLM should contain the truncation marker
        assert received_prompts, "complete() was not called"
        assert "[earlier turns omitted]" in received_prompts[0]
