"""Tests for src/roleplay/core/episode.py."""
from __future__ import annotations

import pytest

from roleplay.core.episode import (
    Episode,
    FixedOrderScheduler,
    FormattedIncrementClock,
    LambdaClock,
    NoopClock,
    RandomOrderScheduler,
    RoundRobinScheduler,
    SimulationHistory,
    ToolCall,
    Turn,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _turn(
    party_id: str = "alice",
    index: int = 0,
    output: str = "Hello.",
    prompt_tokens: int = 10,
    completion_tokens: int = 5,
) -> Turn:
    return Turn(
        party_id=party_id,
        index=index,
        output=output,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )


def _open_episode(index: int = 0) -> Episode:
    return Episode(index=index, turns=[], simulated_time_start="Day 1")


def _closed_episode(index: int = 0, num_turns: int = 2) -> Episode:
    ep = _open_episode(index)
    for i in range(num_turns):
        ep.add_turn(_turn(index=i, prompt_tokens=10, completion_tokens=5))
    ep.close("Day 2")
    return ep


def _history_with(
    closed: int = 0, has_open: bool = False
) -> SimulationHistory:
    h = SimulationHistory()
    for i in range(closed):
        h.episodes.append(_closed_episode(index=i))
    if has_open:
        h.episodes.append(_open_episode(index=closed))
    return h


# ---------------------------------------------------------------------------
# ToolCall
# ---------------------------------------------------------------------------


class TestToolCall:
    def test_basic(self) -> None:
        tc = ToolCall(tool_name="search", arguments={"q": "cats"}, result="cats info")
        assert tc.tool_name == "search"
        assert tc.arguments == {"q": "cats"}
        assert tc.result == "cats info"
        assert tc.error is None

    def test_with_error(self) -> None:
        tc = ToolCall(
            tool_name="search",
            arguments={},
            result="error summary",
            error="TimeoutError",
        )
        assert tc.error == "TimeoutError"

    def test_frozen(self) -> None:
        tc = ToolCall(tool_name="t", arguments={}, result="r")
        with pytest.raises((AttributeError, TypeError)):
            tc.tool_name = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Turn
# ---------------------------------------------------------------------------


class TestTurn:
    def test_total_tokens(self) -> None:
        t = _turn(prompt_tokens=100, completion_tokens=50)
        assert t.total_tokens() == 150

    def test_zero_tokens(self) -> None:
        t = _turn(prompt_tokens=0, completion_tokens=0)
        assert t.total_tokens() == 0

    def test_defaults(self) -> None:
        t = Turn(party_id="bob", index=0, output="Hi")
        assert t.state_update_proposals == {}
        assert t.tool_calls == ()
        assert t.prompt_tokens == 0
        assert t.completion_tokens == 0
        assert t.timestamp is not None

    def test_frozen(self) -> None:
        t = _turn()
        with pytest.raises((AttributeError, TypeError)):
            t.output = "changed"  # type: ignore[misc]

    def test_with_tool_calls(self) -> None:
        tc = ToolCall("search", {}, "result")
        t = Turn(party_id="alice", index=0, output="ok", tool_calls=(tc,))
        assert len(t.tool_calls) == 1

    def test_with_state_proposals(self) -> None:
        t = Turn(
            party_id="alice",
            index=0,
            output="ok",
            state_update_proposals={"mood": "happy"},
        )
        assert t.state_update_proposals["mood"] == "happy"

    def test_timestamp_is_utc(self) -> None:
        t = _turn()
        assert t.timestamp.tzinfo is not None


# ---------------------------------------------------------------------------
# Episode
# ---------------------------------------------------------------------------


class TestEpisode:
    def test_is_complete_open(self) -> None:
        ep = _open_episode()
        assert not ep.is_complete()

    def test_is_complete_after_close(self) -> None:
        ep = _open_episode()
        ep.close("Day 2")
        assert ep.is_complete()

    def test_close_sets_simulated_time_end(self) -> None:
        ep = _open_episode()
        ep.close("Day 2 17:00")
        assert ep.simulated_time_end == "Day 2 17:00"

    def test_close_sets_ended_at(self) -> None:
        ep = _open_episode()
        ep.close("end")
        assert ep.ended_at is not None

    def test_close_twice_raises(self) -> None:
        ep = _open_episode()
        ep.close("t1")
        with pytest.raises(RuntimeError, match="already closed"):
            ep.close("t2")

    def test_add_turn(self) -> None:
        ep = _open_episode()
        t = _turn()
        ep.add_turn(t)
        assert len(ep.turns) == 1

    def test_add_turn_to_closed_raises(self) -> None:
        ep = _open_episode()
        ep.close("end")
        with pytest.raises(RuntimeError, match="closed"):
            ep.add_turn(_turn())

    def test_total_tokens_empty(self) -> None:
        ep = _open_episode()
        assert ep.total_tokens() == 0

    def test_total_tokens_sums_turns(self) -> None:
        ep = _open_episode()
        ep.add_turn(_turn(prompt_tokens=10, completion_tokens=5))
        ep.add_turn(_turn(prompt_tokens=20, completion_tokens=8))
        assert ep.total_tokens() == 43

    def test_single_turn_episode(self) -> None:
        ep = _open_episode()
        ep.add_turn(_turn(party_id="solo"))
        ep.close("end")
        assert ep.total_tokens() == 15
        assert ep.is_complete()


# ---------------------------------------------------------------------------
# SimulationHistory
# ---------------------------------------------------------------------------


class TestSimulationHistory:
    def test_empty(self) -> None:
        h = SimulationHistory()
        assert h.current_episode() is None
        assert h.completed_episodes() == []
        assert h.total_tokens() == 0

    def test_current_episode_open(self) -> None:
        h = _history_with(closed=0, has_open=True)
        assert h.current_episode() is not None

    def test_current_episode_when_all_closed(self) -> None:
        h = _history_with(closed=2)
        assert h.current_episode() is None

    def test_current_episode_none_when_empty(self) -> None:
        h = SimulationHistory()
        assert h.current_episode() is None

    def test_completed_episodes_ordering(self) -> None:
        h = _history_with(closed=3)
        completed = h.completed_episodes()
        assert len(completed) == 3
        assert [ep.index for ep in completed] == [0, 1, 2]

    def test_completed_excludes_open(self) -> None:
        h = _history_with(closed=2, has_open=True)
        assert len(h.completed_episodes()) == 2

    def test_context_window_fewer_than_max(self) -> None:
        h = _history_with(closed=3)
        window = h.episodes_in_context_window(10)
        assert len(window) == 3

    def test_context_window_more_than_max(self) -> None:
        h = _history_with(closed=5)
        window = h.episodes_in_context_window(3)
        assert len(window) == 3
        assert window[0].index == 2  # oldest in window
        assert window[-1].index == 4  # most recent

    def test_context_window_exact(self) -> None:
        h = _history_with(closed=3)
        assert len(h.episodes_in_context_window(3)) == 3

    def test_context_window_zero(self) -> None:
        h = _history_with(closed=5)
        assert h.episodes_in_context_window(0) == []

    def test_context_window_excludes_open(self) -> None:
        h = _history_with(closed=2, has_open=True)
        window = h.episodes_in_context_window(10)
        assert all(ep.is_complete() for ep in window)

    def test_total_tokens(self) -> None:
        h = _history_with(closed=2)  # 2 episodes x 2 turns x 15 tokens = 60
        assert h.total_tokens() == 60

    def test_total_tokens_includes_open(self) -> None:
        h = _history_with(closed=1, has_open=True)
        open_ep = h.current_episode()
        assert open_ep is not None
        open_ep.add_turn(_turn(prompt_tokens=7, completion_tokens=3))
        # closed: 30 tokens + open: 10 tokens
        assert h.total_tokens() == 40


# ---------------------------------------------------------------------------
# RoundRobinScheduler
# ---------------------------------------------------------------------------


class TestRoundRobinScheduler:
    def test_returns_all_parties(self) -> None:
        sched = RoundRobinScheduler()
        h = SimulationHistory()
        ids = ["alice", "bob", "carol"]
        result = sched.schedule(ids, episode_index=0, history=h)
        assert result == ["alice", "bob", "carol"]

    def test_same_order_every_episode(self) -> None:
        sched = RoundRobinScheduler()
        h = SimulationHistory()
        ids = ["alice", "bob"]
        r0 = sched.schedule(ids, 0, h)
        r1 = sched.schedule(ids, 1, h)
        assert r0 == r1

    def test_returns_copy(self) -> None:
        sched = RoundRobinScheduler()
        ids = ["alice", "bob"]
        result = sched.schedule(ids, 0, SimulationHistory())
        result.append("extra")
        assert len(sched.schedule(ids, 0, SimulationHistory())) == 2

    def test_single_party(self) -> None:
        sched = RoundRobinScheduler()
        assert sched.schedule(["solo"], 0, SimulationHistory()) == ["solo"]


# ---------------------------------------------------------------------------
# RandomOrderScheduler
# ---------------------------------------------------------------------------


class TestRandomOrderScheduler:
    def test_returns_all_parties(self) -> None:
        sched = RandomOrderScheduler(seed=42)
        ids = ["alice", "bob", "carol"]
        result = sched.schedule(ids, 0, SimulationHistory())
        assert set(result) == set(ids)
        assert len(result) == len(ids)

    def test_seeded_is_deterministic(self) -> None:
        ids = ["a", "b", "c", "d"]
        sched1 = RandomOrderScheduler(seed=7)
        sched2 = RandomOrderScheduler(seed=7)
        h = SimulationHistory()
        assert sched1.schedule(ids, 0, h) == sched2.schedule(ids, 0, h)

    def test_different_seeds_differ(self) -> None:
        ids = ["a", "b", "c", "d", "e"]
        results = set()
        for seed in range(20):
            sched = RandomOrderScheduler(seed=seed)
            results.add(tuple(sched.schedule(ids, 0, SimulationHistory())))
        assert len(results) > 1  # at least two different orderings

    def test_order_varies_across_episodes(self) -> None:
        sched = RandomOrderScheduler(seed=99)
        ids = ["a", "b", "c", "d", "e"]
        h = SimulationHistory()
        orders = [tuple(sched.schedule(ids, i, h)) for i in range(10)]
        assert len(set(orders)) > 1  # probabilistic — seed 99 should vary


# ---------------------------------------------------------------------------
# FixedOrderScheduler
# ---------------------------------------------------------------------------


class TestFixedOrderScheduler:
    def test_returns_configured_order(self) -> None:
        order = ["carol", "alice", "bob"]
        sched = FixedOrderScheduler(order)
        result = sched.schedule(["alice", "bob", "carol"], 0, SimulationHistory())
        assert result == ["carol", "alice", "bob"]

    def test_same_every_episode(self) -> None:
        sched = FixedOrderScheduler(["b", "a"])
        h = SimulationHistory()
        ids = ["a", "b"]
        assert sched.schedule(ids, 0, h) == sched.schedule(ids, 5, h)

    def test_returns_copy(self) -> None:
        sched = FixedOrderScheduler(["a", "b"])
        result = sched.schedule(["a", "b"], 0, SimulationHistory())
        result.append("extra")
        assert sched.schedule(["a", "b"], 0, SimulationHistory()) == ["a", "b"]

    def test_original_list_not_mutated(self) -> None:
        order = ["a", "b"]
        sched = FixedOrderScheduler(order)
        order.append("c")
        assert sched.schedule([], 0, SimulationHistory()) == ["a", "b"]


# ---------------------------------------------------------------------------
# NoopClock
# ---------------------------------------------------------------------------


class TestNoopClock:
    def test_returns_current_unchanged(self) -> None:
        clock = NoopClock()
        assert clock.advance("Day 1", 0) == "Day 1"

    def test_ignores_episode_index(self) -> None:
        clock = NoopClock()
        assert clock.advance("T", 99) == "T"


# ---------------------------------------------------------------------------
# FormattedIncrementClock
# ---------------------------------------------------------------------------


class TestFormattedIncrementClock:
    def test_advance_hours(self) -> None:
        clock = FormattedIncrementClock("hours", 1, "%Y-%m-%d %H:%M")
        assert clock.advance("2024-01-01 09:00", 0) == "2024-01-01 10:00"

    def test_advance_minutes(self) -> None:
        clock = FormattedIncrementClock("minutes", 30, "%H:%M")
        assert clock.advance("09:00", 0) == "09:30"

    def test_advance_days(self) -> None:
        clock = FormattedIncrementClock("days", 1, "%Y-%m-%d")
        assert clock.advance("2024-01-01", 0) == "2024-01-02"

    def test_advance_weeks(self) -> None:
        clock = FormattedIncrementClock("weeks", 1, "%Y-%m-%d")
        assert clock.advance("2024-01-01", 0) == "2024-01-08"

    def test_advance_seconds(self) -> None:
        clock = FormattedIncrementClock("seconds", 60, "%H:%M:%S")
        assert clock.advance("09:00:00", 0) == "09:01:00"

    def test_multiple_advances(self) -> None:
        clock = FormattedIncrementClock("hours", 2, "%H:%M")
        t = "08:00"
        for _ in range(3):
            t = clock.advance(t, 0)
        assert t == "14:00"

    def test_invalid_unit_raises_on_construction(self) -> None:
        with pytest.raises(ValueError, match="Unknown time unit"):
            FormattedIncrementClock("fortnights", 2, "%Y-%m-%d")

    def test_unparseable_current_raises(self) -> None:
        clock = FormattedIncrementClock("hours", 1, "%Y-%m-%d %H:%M")
        with pytest.raises(ValueError, match="cannot parse"):
            clock.advance("not-a-date", 0)

    def test_episode_index_ignored(self) -> None:
        clock = FormattedIncrementClock("days", 1, "%Y-%m-%d")
        r1 = clock.advance("2024-01-01", 0)
        r2 = clock.advance("2024-01-01", 99)
        assert r1 == r2


# ---------------------------------------------------------------------------
# LambdaClock
# ---------------------------------------------------------------------------


class TestLambdaClock:
    def test_delegates_to_fn(self) -> None:
        clock = LambdaClock(lambda t, i: f"Turn {i + 1}")
        assert clock.advance("anything", 0) == "Turn 1"
        assert clock.advance("anything", 4) == "Turn 5"

    def test_fn_receives_current(self) -> None:
        clock = LambdaClock(lambda t, i: t + "!")
        assert clock.advance("hello", 0) == "hello!"

    def test_fn_receives_episode_index(self) -> None:
        received: list[int] = []
        clock = LambdaClock(lambda t, i: (received.append(i), t)[1])
        clock.advance("x", 42)
        assert received == [42]
