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


# ---------------------------------------------------------------------------
# Additional coverage tests
# ---------------------------------------------------------------------------


class TestApiObserverHookExtraCoverage:
    """Cover remaining uncovered branches in ApiObserverHook."""

    def _make_runner(self) -> SessionRunner:
        r = SessionRunner("s1")
        r.status = "running"
        return r

    def _make_state(self) -> MagicMock:
        state = MagicMock()
        state.history.completed_episodes.return_value = []
        return state

    def _make_layer(self) -> MagicMock:
        layer = MagicMock()

        async def _save_episode(session_id: object, ep: object) -> None:
            pass

        layer.save_episode = _save_episode
        return layer

    # ---- before_episode: pending injection consumed ----------------------------

    async def test_before_episode_returns_inject_directive(self) -> None:
        """Queued injection is consumed and returned as an inject directive."""
        runner = self._make_runner()
        runner._pending_injection = "A surprise announcement!"
        hook = ApiObserverHook(runner, MagicMock(), self._make_layer())

        state = self._make_state()
        directive = await hook.before_episode(state, 0)

        assert directive.is_inject
        assert runner._pending_injection is None

    # ---- _check_goal_progress: no turns / long dialog / exception --------------

    async def test_check_goal_no_turns_attribute(self) -> None:
        """Episode without 'turns' attr → (no turns to evaluate, False)."""
        runner = self._make_runner()
        hook = ApiObserverHook(runner, MagicMock(), self._make_layer())
        state = self._make_state()
        state.config.goal = "something"

        ep_no_turns = MagicMock(spec=[])  # spec=[] → no attributes
        result = await hook._check_goal_progress(state, ep_no_turns)
        assert result == ("(no turns to evaluate)", False)

    async def test_check_goal_long_dialog_truncated(self) -> None:
        """Dialog >6000 chars in goal check is truncated with marker."""
        received: list[str] = []
        provider = MagicMock()
        resp = MagicMock()
        resp.text = "GOAL NOT MET: still needs work"

        async def _complete(req: object) -> MagicMock:
            from roleplay.providers.base import CompletionRequest

            if isinstance(req, CompletionRequest):
                received.append(req.prompt)
            return resp

        provider.complete = _complete

        runner = self._make_runner()
        hook = ApiObserverHook(runner, provider, self._make_layer())
        state = self._make_state()
        state.config.goal = "reach agreement"

        ep = MagicMock()
        turn = MagicMock()
        turn.party_id = "alice"
        turn.output = "word " * 2000  # > 6000 chars
        ep.turns = [turn]
        ep.index = 0

        _status, met = await hook._check_goal_progress(state, ep)
        assert not met
        assert received and "[earlier turns omitted]" in received[0]

    async def test_check_goal_provider_exception_returns_unavailable(self) -> None:
        """Provider raises → returns (goal check unavailable, False)."""
        provider = MagicMock()

        async def _complete(req: object) -> MagicMock:
            raise RuntimeError("API down")

        provider.complete = _complete

        runner = self._make_runner()
        hook = ApiObserverHook(runner, provider, self._make_layer())
        state = self._make_state()
        state.config.goal = "reach agreement"

        ep = MagicMock()
        turn = MagicMock()
        turn.party_id = "alice"
        turn.output = "Let's try again."
        ep.turns = [turn]
        ep.index = 0

        status, met = await hook._check_goal_progress(state, ep)
        assert status == "(goal check unavailable)"
        assert not met

    # ---- after_episode: save_episode failure -----------------------------------

    async def test_after_episode_save_failure_is_logged(self) -> None:
        """Persistence failure in after_episode is swallowed (logged only)."""
        layer = MagicMock()

        async def _save_fail(session_id: object, ep: object) -> None:
            raise RuntimeError("disk full")

        layer.save_episode = _save_fail

        provider = MagicMock()
        resp = MagicMock()
        resp.text = ""

        async def _complete(req: object) -> MagicMock:
            return resp

        provider.complete = _complete

        runner = self._make_runner()
        hook = ApiObserverHook(runner, provider, layer)
        state = self._make_state()
        state.config.goal = ""
        state.history.completed_episodes.return_value = []

        ep = MagicMock()
        turn = MagicMock()
        turn.party_id = "alice"
        turn.output = "Hello."
        ep.turns = [turn]
        ep.index = 0

        # Should not raise
        await hook.after_episode(state, ep)


class TestSessionRunnerRunSuccessPath:
    """Cover runner.py lines 332-334: status→done after successful _run."""

    async def test_run_sets_status_done_on_success(self) -> None:
        """Call _run() directly (no background task) and verify status→done."""
        import tempfile
        from pathlib import Path
        from unittest.mock import AsyncMock, patch

        from roleplay.api.runner import SessionRunner
        from roleplay.persistence.sqlite import SqlitePersistenceLayer
        from roleplay.scenario_yaml import load_yaml_scenario

        db_path = tempfile.mktemp(suffix=".db", dir="/tmp")
        layer = SqlitePersistenceLayer(db_path)
        await layer.open()

        yaml_text = (
            "session_id: done-test\n"
            "parties:\n"
            "  - id: alice\n"
            "    name: Alice\n"
            "    description: A person\n"
            "    kind: person\n"
        )
        p = Path(tempfile.mktemp(suffix=".yaml", dir="/tmp"))
        p.write_text(yaml_text)
        state = load_yaml_scenario(p).state
        p.unlink(missing_ok=True)
        await layer.create_session(state)

        runner = SessionRunner("done-test")
        runner.status = "running"

        mock_provider = MagicMock(spec=["complete", "default_model"])
        mock_provider.default_model = "mock"

        with patch("roleplay.api.runner._build_registry") as mock_reg:
            mock_reg.return_value.get.return_value = mock_provider
            with patch("roleplay.engine.engine.SimulationEngine.run", new=AsyncMock()):
                await runner._run(state, layer, 1)

        assert runner.status == "done"
        await layer.close()


class TestDefaultModelOverride:
    """runner.py lines 315-318: config.default_model triggers GeminiProvider rebuild."""

    async def test_run_default_model_creates_gemini_provider(self) -> None:
        """When default_model is set and provider has .models attr,
        a new GeminiProvider is built with that model first."""
        import tempfile
        from pathlib import Path
        from unittest.mock import AsyncMock, patch

        from roleplay.api.runner import SessionRunner
        from roleplay.persistence.sqlite import SqlitePersistenceLayer
        from roleplay.scenario_yaml import load_yaml_scenario

        db_path = tempfile.mktemp(suffix=".db", dir="/tmp")
        layer = SqlitePersistenceLayer(db_path)
        await layer.open()

        yaml_text = (
            "session_id: model-override\n"
            "parties:\n"
            "  - id: alice\n"
            "    name: Alice\n"
            "    description: A person\n"
            "    kind: person\n"
            "config:\n"
            "  default_model: gemini-2.0-flash-lite\n"
        )
        p = Path(tempfile.mktemp(suffix=".yaml", dir="/tmp"))
        p.write_text(yaml_text)
        state = load_yaml_scenario(p).state
        p.unlink(missing_ok=True)
        await layer.create_session(state)

        runner = SessionRunner("model-override")
        runner.status = "running"

        built_models: list[tuple] = []

        class _TrackedGemini:
            """Replacement GeminiProvider that records construction args."""

            def __init__(self, *, models: tuple) -> None:
                built_models.append(models)
                self.models = models
                self.default_model = models[0]

        # Provider mock WITH .models so the branch fires
        mock_provider = MagicMock()
        mock_provider.models = ("gemini-1.5-pro",)
        mock_provider.default_model = "gemini-1.5-pro"

        with patch("roleplay.api.runner._build_registry") as mock_reg:
            mock_reg.return_value.get.return_value = mock_provider
            # Patch where it is imported at call-time (local import inside _run)
            with (
                patch("roleplay.providers.gemini.GeminiProvider", _TrackedGemini),
                patch("roleplay.engine.engine.SimulationEngine.run", new=AsyncMock()),
            ):
                await runner._run(state, layer, 1)

        assert built_models, "GeminiProvider was not instantiated"
        assert built_models[0][0] == "gemini-2.0-flash-lite"
        await layer.close()


class TestAfterEpisodeSummaryException:
    """runner.py lines 145-146: summary generation exception is logged, not raised."""

    def _make_runner(self) -> SessionRunner:
        r = SessionRunner("s1")
        r.status = "running"
        return r

    async def test_summary_provider_exception_is_swallowed(self) -> None:
        """When provider.complete raises during summary gen, episode gets empty summary."""
        layer = MagicMock()

        async def _save_ok(session_id: object, ep: object) -> None:
            pass

        layer.save_episode = _save_ok

        provider = MagicMock()

        async def _complete_raises(req: object) -> object:
            raise RuntimeError("quota exceeded")

        provider.complete = _complete_raises

        runner = self._make_runner()
        hook = ApiObserverHook(runner, provider, layer)

        state = MagicMock()
        state.config.session_id = "s1"
        state.config.goal = ""
        state.history.completed_episodes.return_value = []

        ep = MagicMock()
        turn = MagicMock()
        turn.party_id = "alice"
        turn.output = "Let's negotiate."
        ep.turns = [turn]
        ep.index = 0

        # Should not raise; summary falls back to ""
        await hook.after_episode(state, ep)
        assert ep.summary == ""
