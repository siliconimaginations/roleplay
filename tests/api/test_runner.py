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

    async def test_before_episode_broadcasts_start(self) -> None:
        runner = self._make_runner()
        hook = ApiObserverHook(runner, self._make_provider())
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
        hook = ApiObserverHook(runner, self._make_provider())
        state = self._make_state()

        directive = await hook.before_episode(state, 0)

        assert directive.is_halt
        assert runner.status == "paused"
        assert runner._pause_requested is False

    async def test_after_turn_broadcasts_turn(self) -> None:
        runner = self._make_runner()
        hook = ApiObserverHook(runner, self._make_provider())
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
        hook = ApiObserverHook(runner, self._make_provider())
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
