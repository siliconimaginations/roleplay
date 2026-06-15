"""Episode model — the atomic unit of simulation progress.

Turn → Episode → SimulationHistory

Episodes are **open** while turns are being added and **closed** once the
engine calls :meth:`Episode.close`.  Closed episodes are immutable by
convention; the engine never modifies them after closure.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable

    from roleplay.core.party import StateValue

# ---------------------------------------------------------------------------
# ToolCall
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolCall:
    """A single tool invocation made during a turn.

    ``arguments`` must be JSON-serialisable (the engine validates this before
    constructing the object).  ``error`` is set when the tool raised an
    exception; in that case ``result`` contains the error summary string
    injected back into the prompt.
    """

    tool_name: str
    arguments: dict[str, object]  # JSON-serialisable; mutable but not reassignable
    result: str  # Stringified result (or error summary) injected back into prompt
    error: str | None = None


# ---------------------------------------------------------------------------
# Turn
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Turn:
    """One party's contribution within an episode.

    ``state_update_proposals`` records what the LLM *proposed* to change in the
    world; the engine validates and applies (or rejects) those proposals after
    the turn completes.  The authoritative record of what actually changed is
    ``Party.state_history``.

    ``output`` must be a non-empty string — the engine raises before appending
    a turn with an empty output.
    """

    party_id: str
    index: int  # 0-based position within the episode
    output: str  # What the party said or did (LLM response text)
    state_update_proposals: dict[str, StateValue] = field(default_factory=dict)
    tool_calls: tuple[ToolCall, ...] = ()
    prompt_tokens: int = 0
    completion_tokens: int = 0
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

    def total_tokens(self) -> int:
        """Sum of prompt and completion tokens for this turn."""
        return self.prompt_tokens + self.completion_tokens


# ---------------------------------------------------------------------------
# Episode
# ---------------------------------------------------------------------------


@dataclass
class Episode:
    """An ordered sequence of Turns plus bookkeeping for world-state reconstruction.

    Lifecycle::

        ep = Episode(index=0, turns=[], simulated_time_start="Day 1 09:00")
        ep.add_turn(turn_a)
        ep.add_turn(turn_b)
        ep.close("Day 1 10:00")
        assert ep.is_complete()
    """

    index: int  # 0-based across the simulation lifetime
    turns: list[Turn]  # Ordered; appended as turns complete
    simulated_time_start: str  # time.simulated value before this episode
    simulated_time_end: str | None = None  # Set when episode closes
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    ended_at: datetime | None = None

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add_turn(self, turn: Turn) -> None:
        """Append *turn* to this episode.

        Raises:
            RuntimeError: If the episode is already closed.
        """
        if self.ended_at is not None:
            raise RuntimeError(
                f"Cannot add a turn to closed episode {self.index}. Closed episodes are immutable."
            )
        self.turns.append(turn)

    def close(self, simulated_time_end: str) -> None:
        """Close this episode, recording the end simulated time.

        Raises:
            RuntimeError: If the episode is already closed.
        """
        if self.ended_at is not None:
            raise RuntimeError(f"Episode {self.index} is already closed.")
        self.simulated_time_end = simulated_time_end
        self.ended_at = datetime.now(UTC)

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def is_complete(self) -> bool:
        """Return True once :meth:`close` has been called."""
        return self.ended_at is not None

    def total_tokens(self) -> int:
        """Sum of prompt + completion tokens across all turns."""
        return sum(t.total_tokens() for t in self.turns)


# ---------------------------------------------------------------------------
# SimulationHistory
# ---------------------------------------------------------------------------


@dataclass
class SimulationHistory:
    """Append-only record of all episodes in a simulation run."""

    episodes: list[Episode] = field(default_factory=list)

    def current_episode(self) -> Episode | None:
        """The last episode if it is still open; ``None`` otherwise."""
        if self.episodes and not self.episodes[-1].is_complete():
            return self.episodes[-1]
        return None

    def completed_episodes(self) -> list[Episode]:
        """All closed episodes, oldest first."""
        return [ep for ep in self.episodes if ep.is_complete()]

    def episodes_in_context_window(self, max_episodes: int) -> list[Episode]:
        """The most recent *max_episodes* closed episodes, oldest first.

        If *max_episodes* is 0 the list is always empty (parties see no
        episode history — only memory engine output).
        """
        if max_episodes <= 0:
            return []
        completed = self.completed_episodes()
        return completed[-max_episodes:]

    def total_tokens(self) -> int:
        """Cumulative token count across all episodes (open + closed)."""
        return sum(ep.total_tokens() for ep in self.episodes)


# ---------------------------------------------------------------------------
# TurnScheduler protocol + built-ins
# ---------------------------------------------------------------------------


class TurnScheduler(Protocol):
    """Determines which parties speak in each episode and in what order.

    Implementations must not include the environment party id in the returned
    list — the engine always handles the environment update separately.
    """

    def schedule(
        self,
        party_ids: list[str],
        episode_index: int,
        history: SimulationHistory,
    ) -> list[str]:
        """Return the ordered list of party IDs to speak this episode.

        May return a subset (parties skipping the episode) or repeat an id
        (a party speaks twice).  Must not include the environment id.
        """
        ...  # pragma: no cover


class RoundRobinScheduler:
    """All parties in registration order, every episode. The default scheduler."""

    def schedule(
        self,
        party_ids: list[str],
        episode_index: int,
        history: SimulationHistory,
    ) -> list[str]:
        return list(party_ids)


class RandomOrderScheduler:
    """All parties, shuffled each episode.

    Args:
        seed: Optional RNG seed for deterministic testing.
    """

    def __init__(self, seed: int | None = None) -> None:
        self._rng = random.Random(seed)

    def schedule(
        self,
        party_ids: list[str],
        episode_index: int,
        history: SimulationHistory,
    ) -> list[str]:
        shuffled = list(party_ids)
        self._rng.shuffle(shuffled)
        return shuffled


class FixedOrderScheduler:
    """Caller-specified sequence, repeated unchanged every episode.

    Args:
        order: The exact sequence of party IDs to return each episode.
               May be a subset of or different from the registered party list.
    """

    def __init__(self, order: list[str]) -> None:
        self._order = list(order)

    def schedule(
        self,
        party_ids: list[str],
        episode_index: int,
        history: SimulationHistory,
    ) -> list[str]:
        return list(self._order)


# ---------------------------------------------------------------------------
# SimulatedTimeClock protocol + built-ins
# ---------------------------------------------------------------------------


class SimulatedTimeClock(Protocol):
    """Converts the current simulated time string into the next one.

    The engine calls ``advance`` once per episode, after all turns complete,
    to update ``env.state["time.simulated"]``.
    """

    def advance(self, current: str, episode_index: int) -> str:
        """Return the new ``time.simulated`` value after this episode."""
        ...  # pragma: no cover


class NoopClock:
    """Returns *current* unchanged.

    Use when simulated time is not meaningful to the scenario (e.g., atemporal
    debates or abstract negotiations).
    """

    def advance(self, current: str, episode_index: int) -> str:
        return current


class FormattedIncrementClock:
    """Parses *current* as a datetime, adds *amount* of *unit*, re-formats.

    Args:
        unit: One of ``"seconds"``, ``"minutes"``, ``"hours"``,
              ``"days"``, ``"weeks"``.
        amount: How much to add per episode advance.
        fmt: A :func:`datetime.strptime` / :func:`datetime.strftime` format
             string, e.g. ``"%Y-%m-%d %H:%M"``.

    Raises:
        ValueError: On construction if *unit* is not recognised.
        ValueError: On :meth:`advance` if *current* cannot be parsed with *fmt*.

    Example::

        clock = FormattedIncrementClock("hours", 1, "%Y-%m-%d %H:%M")
        clock.advance("2024-01-01 09:00", 0)  # → "2024-01-01 10:00"
    """

    _VALID_UNITS: frozenset[str] = frozenset({"seconds", "minutes", "hours", "days", "weeks"})

    def __init__(self, unit: str, amount: int, fmt: str) -> None:
        if unit not in self._VALID_UNITS:
            raise ValueError(
                f"Unknown time unit {unit!r}. Valid units: {sorted(self._VALID_UNITS)}"
            )
        self._unit = unit
        self._amount = amount
        self._fmt = fmt

    def advance(self, current: str, episode_index: int) -> str:
        try:
            dt = datetime.strptime(current, self._fmt)
        except ValueError as exc:
            raise ValueError(
                f"FormattedIncrementClock: cannot parse {current!r} with format {self._fmt!r}"
            ) from exc

        match self._unit:
            case "seconds":
                delta = timedelta(seconds=self._amount)
            case "minutes":
                delta = timedelta(minutes=self._amount)
            case "hours":
                delta = timedelta(hours=self._amount)
            case "days":
                delta = timedelta(days=self._amount)
            case "weeks":
                delta = timedelta(weeks=self._amount)
            case _:  # pragma: no cover — validated in __init__
                raise AssertionError(f"unreachable unit: {self._unit!r}")

        return (dt + delta).strftime(self._fmt)


class LambdaClock:
    """Wraps any ``Callable[[str, int], str]`` as a :class:`SimulatedTimeClock`.

    Intended for scenario-specific logic that doesn't fit the built-in clocks::

        clock = LambdaClock(lambda t, i: f"Turn {i + 1}")
    """

    def __init__(self, fn: Callable[[str, int], str]) -> None:
        self._fn = fn

    def advance(self, current: str, episode_index: int) -> str:
        return self._fn(current, episode_index)
