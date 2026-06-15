# Episode Model

## Purpose

The Episode is the atomic unit of simulation progress. Each episode represents
one round of interaction: every participating party gets at least one turn, the
environment is updated to reflect what changed, and simulated time advances.
This document defines what an episode and a turn contain, how turn order is
determined, and how simulated time maps to episode count.

---

## Scope

**In scope:**
- `Turn` and `Episode` data structures
- `TurnScheduler` protocol (who speaks, in what order)
- `SimulatedTimeClock` protocol (how simulated time advances per episode)
- Built-in scheduler and clock implementations
- How episodes accumulate into a simulation history
- Episode context window (how many past episodes are visible to each party's prompt)

**Out of scope:**
- Prompt assembly from episode history (see `05-simulation-engine`)
- Memory compaction triggered by episode count (see `04-memory-engine`)
- Persistence of episodes to SQLite (see `07-persistence`)
- The simulation loop itself that drives episodes (see `05-simulation-engine`)

---

## Key Concepts / Domain Model

### Turn

A Turn is one party's contribution within an episode. It records what the party
said or did, any state update proposals the party's LLM output implied, any tool
calls that were made during the turn, and lightweight cost metadata.

State update proposals are **proposals only** — the engine validates and applies
them (or rejects them) after the turn completes. The Turn records what the LLM
proposed; the `StateChange` history on the Party records what actually happened.

```python
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True)
class ToolCall:
    """A single tool invocation made during a turn."""
    tool_name: str
    arguments: dict[str, object]   # JSON-serialisable
    result: str                    # Stringified result injected back into prompt
    error: str | None = None       # Set if the tool raised an exception


@dataclass(frozen=True)
class Turn:
    party_id: str
    index: int                              # 0-based position within the episode
    output: str                             # What the party said or did (LLM response)
    state_update_proposals: dict[str, StateValue] = field(default_factory=dict)
    tool_calls: tuple[ToolCall, ...] = ()
    prompt_tokens: int = 0                  # For cost tracking and context-window auditing
    completion_tokens: int = 0
    timestamp: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
```

`output` is always a non-empty string. The engine raises if the provider returns
an empty response after retries.

### Episode

An Episode is an ordered sequence of Turns plus the bookkeeping needed to
reconstruct the world state at that point in time.

```python
@dataclass
class Episode:
    index: int                              # 0-based across the simulation lifetime
    turns: list[Turn]                       # Ordered; appended as turns complete
    simulated_time_start: str              # time.simulated before this episode
    simulated_time_end: str | None = None  # Set when episode closes
    started_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    ended_at: datetime | None = None

    def is_complete(self) -> bool:
        """True once ended_at is set."""
        ...

    def total_tokens(self) -> int:
        """Sum of prompt + completion tokens across all turns."""
        ...
```

Episodes are **open** while turns are being added and **closed** once the engine
calls `close()`. Closed episodes are immutable by convention (the engine does
not modify them). The in-progress episode is always the last element of
`SimulationHistory.episodes`.

### SimulationHistory

```python
@dataclass
class SimulationHistory:
    episodes: list[Episode] = field(default_factory=list)

    def current_episode(self) -> Episode | None:
        """The last episode if it is open, else None."""
        ...

    def completed_episodes(self) -> list[Episode]:
        """All closed episodes, oldest first."""
        ...

    def episodes_in_context_window(self, max_episodes: int) -> list[Episode]:
        """The most recent `max_episodes` closed episodes."""
        ...

    def total_tokens(self) -> int:
        """Cumulative token count across all episodes."""
        ...
```

### TurnScheduler

The `TurnScheduler` protocol determines which parties speak in each episode and
in what order.

```python
from typing import Protocol

class TurnScheduler(Protocol):
    def schedule(
        self,
        party_ids: list[str],          # All non-environment parties
        episode_index: int,
        history: SimulationHistory,
    ) -> list[str]:
        """Return the ordered list of party_ids to speak this episode.

        May return a subset (some parties skip this episode) or repeat a
        party_id (a party speaks twice). Must not include the environment id.
        """
        ...
```

**Built-in schedulers:**

| Scheduler | Behaviour |
|-----------|-----------|
| `RoundRobinScheduler` | All parties in registration order, every episode. Default. |
| `RandomOrderScheduler` | All parties, shuffled each episode. |
| `FixedOrderScheduler(order)` | Caller-specified sequence, same every episode. |

The engine is responsible for calling the environment update after all party
turns complete, regardless of the scheduler.

### SimulatedTimeClock

The `SimulatedTimeClock` protocol converts an episode index and the current
simulated time string into the next simulated time string.

```python
class SimulatedTimeClock(Protocol):
    def advance(
        self,
        current: str,
        episode_index: int,
    ) -> str:
        """Return the new value of time.simulated after this episode."""
        ...
```

**Built-in clocks:**

| Clock | Behaviour |
|-------|-----------|
| `NoopClock` | Returns `current` unchanged. Use when simulated time is not meaningful. |
| `FormattedIncrementClock(unit, amount, fmt)` | Parses `current` as a `datetime`, adds `amount` of `unit`, formats with `fmt`. Raises `ValueError` if parsing fails. |
| `LambdaClock(fn)` | Wraps any `Callable[[str, int], str]`. For scenario-specific logic. |

The engine updates `env.state["time.simulated"]` and `env.state["time.episode"]`
using the configured clock after each episode closes.

### Context window

Each party's prompt includes the last `N` completed episodes from
`SimulationHistory`. `N` is the `context_window_episodes` setting on the
simulation configuration (default: 10). Older episodes are visible only through
the memory engine (compacted and retrieved by relevance — see `04-memory-engine`).

The context window is episode-based, not token-based, because episode boundaries
are semantically meaningful (one round of interaction). Token budgeting is the
memory engine's concern.

---

## Design Decisions & Rationale

1. **`Turn` and `Episode` are frozen / close-then-immutable, not mutable.**
   The simulation record should be append-only. The engine never edits a
   completed turn or closed episode — if a turn needs a correction (e.g., a
   tool call result arrives late), the engine adds a follow-up turn, not an
   edit. This makes history safe to pass to memory compaction without defensive
   copying.

2. **State update proposals live on `Turn`, not applied automatically.**
   An LLM output may propose world changes that are invalid (referencing a
   party that doesn't exist, contradicting a hard constraint). Separating
   "proposed" from "applied" lets the engine validate before committing to
   `Party.state`. The proposal is in the record for auditability.

3. **`TurnScheduler` is a protocol, not a configuration enum.**
   Scheduling logic varies more than a small set of named strategies can
   express. A protocol lets scenario designers supply a closure or a class
   without modifying the engine. The three built-in implementations cover the
   common cases; anything else is a one-liner `LambdaScheduler`.

4. **Context window is episode-count based, not token-count based.**
   Token counting requires knowing the LLM's tokeniser, which is provider-
   specific. Episode-count is provider-agnostic and maps directly to semantic
   units the scenario designer can reason about. The memory engine handles the
   token budget for long-range recall.

5. **`ToolCall` is frozen and stored on `Turn`.**
   Tool calls are part of the turn record — they affect what the party "knew"
   when producing its output. Storing them on the turn makes replay and
   debugging straightforward. The `ToolRegistry` that supplies the actual
   tool implementations lives in `06-provider-abstraction`; the episode model
   only stores the call/result pair.

6. **`SimulatedTimeClock` advances time per episode, not per turn.**
   Within an episode, multiple parties may speak. It would be unusual (and
   confusing) for time to advance between Alice's and Bob's turn in the same
   episode. Time advances once, at episode close. Scenarios that need finer
   granularity can encode elapsed time in turn outputs narratively.

7. **`SimulationHistory` is on `SimulationState` (see `05-simulation-engine`), not on `Party`.**
   History is shared across all parties. Each party's prompt gets a filtered
   view (the last N episodes, potentially with visibility-filtered turns), but
   the single source of truth lives on the simulation state, not replicated per
   party.

---

## Error Handling

| Situation | Behaviour |
|-----------|-----------|
| Provider returns empty `output` after retries | `RuntimeError` in engine; turn not appended |
| `TurnScheduler.schedule` returns the environment id | `ValueError` in engine before executing the turn |
| `FormattedIncrementClock` cannot parse `current` | `ValueError` with the unparseable value |
| `Episode.turns` appended to after `ended_at` is set | `RuntimeError` — closed episodes are immutable |
| `context_window_episodes` set to 0 | Valid; parties see no episode history (only memory) |

---

## Testing Strategy

**Unit tests:**

- `Turn` construction; `total_tokens` helper
- `Episode.is_complete()` before and after closing
- `Episode.total_tokens()` sums across turns
- `SimulationHistory.current_episode()` — open, closed, empty
- `SimulationHistory.completed_episodes()` ordering
- `SimulationHistory.episodes_in_context_window()` — fewer episodes than window,
  more episodes than window, exactly equal
- `RoundRobinScheduler` returns all parties in order every episode
- `RandomOrderScheduler` returns all parties (set equality), different order
  across episodes with high probability (probabilistic test with fixed seed)
- `FixedOrderScheduler` repeats caller-specified order
- `NoopClock` returns current unchanged
- `FormattedIncrementClock` advances by configured amount; raises on bad input
- `LambdaClock` delegates to the provided function

**Edge cases:**
- Episode with a single turn (one-party simulation)
- Turn with no tool calls, no state update proposals
- `episodes_in_context_window(0)` returns empty list
- `SimulationHistory` with no episodes (fresh simulation)

**Coverage target:** >= 90%

---

## Open Questions

None blocking.

Potential future extension: **observer hooks after each episode** (for human
intervention mode). The engine will call `ObserverHook.after_episode(state,
episode)` if one is registered. This is designed in `05-simulation-engine` and
requires no changes to the episode model itself.
