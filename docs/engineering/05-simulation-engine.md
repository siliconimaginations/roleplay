# Simulation Engine

## Purpose

The Simulation Engine is the runtime that drives a simulation forward. It owns
the episode loop: determining which parties speak, calling the LLM provider for
each party's turn, applying state updates, writing memories, advancing simulated
time, and persisting progress. It is the only layer that coordinates all other
subsystems (core domain, memory engine, LLM providers, persistence) in one
place.

---

## Scope

**In scope:**
- `SimulationState` — the complete in-memory snapshot of a running simulation
- `SimulationEngine` — the async loop that drives episodes
- Episode lifecycle: open → turns → environment update → close → persist → compact
- Turn execution: prompt assembly, provider call, output parsing, state application
- `ObserverHook` protocol — human intervention injection points
- Cross-party passive memory injection policy
- Prompt context assembly (memory + episode history + environment + persona)
- Error recovery: provider failure, state validation errors

**Out of scope:**
- LLM provider implementations (see `06-provider-abstraction`)
- Persistence I/O (see `07-persistence`)
- Memory scoring/compaction internals (see `04-memory-engine`)
- CLI command parsing (see `08-cli`)
- `TurnScheduler` and `SimulatedTimeClock` implementations (see `03-episode-model`)

---

## Key Concepts / Domain Model

### SimulationState

`SimulationState` is the single source of truth for a running simulation. It is
mutable and owned by the engine. All subsystems receive references to it.

```python
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone

from roleplay.core.party import Party
from roleplay.core.episode import Episode, SimulationHistory, TurnScheduler, SimulatedTimeClock


@dataclass
class SimulationConfig:
    """All tunable parameters for a simulation run. Loaded from YAML."""
    session_id: str
    context_window_episodes: int = 10          # How many past episodes each party sees
    memory_max_entries: int = 20               # Max memory entries per party per turn
    memory_char_budget: int = 4_000            # Max chars of memory injected per turn
    memory_write_mode: str = "template"        # "template" | "llm"
    compaction_threshold: int = 200
    compaction_batch_size: int = 50
    compaction_importance_floor: float = 0.7
    compaction_char_limit: int = 80_000
    forgetting_enabled: bool = False
    forgetting_idle_episodes: int = 100
    memory_retrieve_fail_mode: str = "raise"   # "raise" | "empty"
    retrieval_weights: dict[str, float] = field(
        default_factory=lambda: {"alpha": 0.5, "beta": 0.25, "gamma": 0.15, "delta": 0.10}
    )


@dataclass
class SimulationState:
    config: SimulationConfig
    parties: dict[str, Party]           # All non-environment parties keyed by id
    environment: Party                  # Exactly one ENVIRONMENT party
    history: SimulationHistory
    scheduler: TurnScheduler
    clock: SimulatedTimeClock
    started_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def party_ids(self) -> list[str]:
        return list(self.parties.keys())

    def get_party(self, party_id: str) -> Party:
        """Raise KeyError if party_id not found."""
        ...

    def all_parties_including_env(self) -> list[Party]:
        """Returns all parties plus the environment, in registration order."""
        ...
```

### SimulationEngine

```python
class SimulationEngine:
    def __init__(
        self,
        state: SimulationState,
        provider: Provider,             # From providers layer
        memory_store: MemoryStore,
        persistence: PersistenceLayer,
        observer: ObserverHook | None = None,
    ) -> None: ...

    async def run_episode(self) -> Episode:
        """Execute one complete episode and return it (closed)."""
        ...

    async def run(self, max_episodes: int | None = None) -> None:
        """Run episodes until max_episodes reached or observer halts."""
        ...
```

### ObserverHook

`ObserverHook` is the human intervention interface. It is called at defined
points in each episode. If no observer is configured, the engine runs fully
autonomously.

```python
from typing import Protocol


class ObserverHook(Protocol):
    async def before_episode(
        self,
        state: SimulationState,
        episode_index: int,
    ) -> ObserverDirective:
        """Called before the episode starts.

        Return CONTINUE to proceed normally, HALT to stop the simulation,
        or INJECT with a payload to modify state before the episode begins.
        """
        ...

    async def after_turn(
        self,
        state: SimulationState,
        turn: Turn,
    ) -> ObserverDirective:
        """Called after each turn completes (before the next turn starts)."""
        ...

    async def after_episode(
        self,
        state: SimulationState,
        episode: Episode,
    ) -> ObserverDirective:
        """Called after the episode closes (before persistence/compaction)."""
        ...


class ObserverDirective:
    """Sealed return type for observer callbacks."""

    @staticmethod
    def continue_() -> ObserverDirective: ...

    @staticmethod
    def halt(reason: str = "") -> ObserverDirective: ...

    @staticmethod
    def inject(payload: InjectionPayload) -> ObserverDirective: ...
```

### InjectionPayload

The `InjectionPayload` carries what the observer wants to change. All fields
are optional; the engine applies only those that are set.

```python
@dataclass
class InjectionPayload:
    context_override: str | None = None
    # Prepended to the current episode's context string. Use this to
    # inject an out-of-band event ("a fire breaks out in the market").

    state_updates: dict[str, dict[str, StateValue]] = field(default_factory=dict)
    # party_id → {key: value} updates applied before the next turn.

    persona_overrides: dict[str, dict[str, object]] = field(default_factory=dict)
    # party_id → Persona field overrides (description, goals, traits, etc.)

    memory_writes: list[MemoryEntry] = field(default_factory=list)
    # Entries written directly to the memory store before the next turn.
    # Use for selective amnesia (kind=EPISODIC with forget=True) or
    # injecting new knowledge.

    force_scheduler: list[str] | None = None
    # If set, overrides the scheduler's turn order for this episode only.
```

---

## Episode Lifecycle

```
before_episode(observer)
│
├── for each party_id in scheduler.schedule(...):
│   │
│   ├── [after_turn observer check on previous turn, if any]
│   ├── apply InjectionPayload (if directive was INJECT)
│   │
│   ├── assemble_prompt(party_id, state, memory_store)
│   ├── provider.complete(prompt) → raw_output
│   ├── parse_output(raw_output) → (output_text, state_proposals, tool_calls)
│   ├── validate_and_apply_state(state_proposals, state)
│   ├── Turn(party_id, output=output_text, state_update_proposals=..., ...)
│   └── episode.turns.append(turn)
│
├── environment_update(state, episode)   # env party's LLM call or rule-based
│
├── close episode (set ended_at, advance simulated time via clock)
│
├── after_episode(observer)
│
├── persist_episode(episode, state)
│
└── compact_if_needed(state, memory_store)
    └── write_episodic_memories(episode, state, memory_store)
```

If the observer returns `HALT` at any hook point, the engine stops after the
current hook (does not abandon a partially-executed turn; finishes the turn then
stops at the next hook opportunity).

---

## Prompt Assembly

The engine assembles each party's prompt from four layers, in this order:

```
[1] Persona block
    {party.to_prompt_context(include_state=True)}

[2] Environment block
    {filtered_env_state(environment, party_id)}
    (only state keys where visible_to includes party_id or "all")

[3] Memory block (retrieved from memory_store)
    Retrieved memories (most relevant first, trimmed to memory_char_budget):
    - {entry.content}
    - ...

[4] Episode history block (last context_window_episodes closed episodes)
    Episode N-k:
      {other_party}: {turn.output}
      {other_party}: {turn.output}
    ...
    Episode N-1:
      ...

[5] Current episode context
    (Turns already completed this episode, in order)
    {party_id}: {turn.output}
    ...

[6] Instruction suffix
    "You are {party.name}. Respond in character. If you propose changes to
    world state, list them as: STATE: key=value. One response only."
```

The engine enforces a total character budget (`prompt_char_budget`, default:
20 000 chars). If the assembled prompt exceeds this, layers are trimmed in
reverse priority order: episode history first (reducing window size), then
memory block (reducing `max_entries`), then current episode context (truncating
oldest turns). Persona and instruction suffix are never trimmed.

### Output parsing

The engine parses the provider's raw output string with a lightweight regex:

```
STATE: key=value
```

Lines matching this pattern are extracted as `state_update_proposals`. The
remainder is `output_text`. Tool call results are injected by the provider
layer before the turn output reaches the engine (see `06-provider-abstraction`).

### State validation and application

Proposed state updates are validated before being applied to `Party.state`:

| Validation | Failure behaviour |
|------------|-------------------|
| Key conforms to schema (env: dot-prefix; party: any non-empty str) | Proposal silently dropped; warning logged |
| Value is `StateValue` type | Proposal dropped |
| Environment key's `visible_to` is valid | Proposal applied; visibility is a read constraint, not a write constraint |

Valid proposals are applied via `party.apply_state_update(updates, episode_index)`.
Invalid proposals are recorded in the `Turn.state_update_proposals` dict
(for auditability) but not applied to `Party.state`.

---

## Environment Update

After all party turns complete, the engine runs the **environment update** — an
LLM call (or rule-based update) where the environment party "reacts" to what
happened in the episode. The environment update can change `env.state` keys:
weather, event flags, object positions, etc.

The environment's prompt is assembled from:
- The environment's own state (all keys, no visibility filtering)
- The full list of turns from the current episode
- An instruction: "As the environment/narrator, describe what changes in the
  world as a result of these events. List state changes as STATE: key=value."

The environment update is itself a `Turn` appended to the episode with
`party_id = environment.id`.

If no environment LLM call is needed (static environment scenario), the engine
skips the environment turn. This is controlled by the `environment_reactive`
flag in `SimulationConfig` (default: `True`).

---

## Memory Write Policy

After the episode closes (including the environment turn), the engine writes
episodic memories for each party that had a turn:

**Template mode** (default):
```
{party.name} [episode {episode.index}]: {turn.output[:300]}
```

**LLM mode**: engine calls provider with a condensed summarisation prompt
(see `04-memory-engine`).

Importance is set heuristically:
- Turns that include `STATE:` proposals: `importance = 0.6`
- Environment update turn: `importance = 0.5`
- All other turns: `importance = 0.4`
- Semantic/procedural writes from `InjectionPayload.memory_writes`: whatever
  the observer set (engine does not override).

**Cross-party passive injection**: if `passive_observation_parties` is non-empty
in `SimulationConfig`, the engine also writes a memory entry for each listed
party that was not active in the episode — a brief "overheard" entry at
`importance = 0.3`. This is the passive injection mechanism described in
`04-memory-engine`; it is only used when giving the bystander an active turn
would be too expensive.

---

## Human Intervention

Human intervention is realised through `ObserverHook`. A CLI observer (see
`08-cli`) polls `stdin` between hooks and constructs `InjectionPayload` from
user commands. An API observer (see `09-api`) reads from a WebSocket message
queue. The engine is unaware of the source.

Typical intervention flows:

| User intent | Mechanism |
|-------------|-----------|
| Inject an out-of-band event ("earthquake!") | `context_override` in `InjectionPayload` |
| Change a party's goal mid-simulation | `persona_overrides` |
| Make a party forget something | `memory_writes` with `kind=EPISODIC, forgotten=True` |
| Pause and inspect state | Observer returns `HALT`; CLI prints state; user resumes |
| Force a specific speaker next | `force_scheduler` override |
| Directly modify world state | `state_updates` |

---

## Save / Load / Branching

Save and load are handled by the persistence layer (`07-persistence`). The
engine exposes:

```python
async def checkpoint(self) -> str:
    """Persist current SimulationState to the session store. Returns checkpoint_id."""
    ...
```

The engine calls `checkpoint()` automatically after every episode (configurable:
`auto_checkpoint = True`). The CLI `roleplay fork` command (see `08-cli`) calls
`checkpoint()` explicitly and then loads the session under a new `session_id`,
creating a branch. The engine itself has no branching logic — branching is a
persistence and CLI concern.

---

## Design Decisions & Rationale

1. **Engine owns the episode loop; subsystems are called, not calling.**
   The engine is the single orchestrator. Memory, providers, and persistence
   are passed in as dependencies and called by the engine — they never call the
   engine back. This makes the engine easy to test (inject mocks for all three)
   and avoids circular dependencies.

2. **`ObserverHook` is a protocol, not an event bus.**
   An event bus (emit/subscribe) would require the engine to be aware of
   subscriber registration and teardown. A synchronous protocol call is simpler
   and makes the hook points explicit in the episode lifecycle. The observer can
   be async (and is, for the CLI stdin reader).

3. **Prompt assembly trims episode history before memory.**
   Episode history is semantically redundant with memory (memories are derived
   from episodes). If the budget is tight, losing older episode history is less
   harmful than losing diverse retrieved memories, which may cover events far
   outside the context window.

4. **State proposals are validated silently (warning, not exception).**
   LLMs produce malformed output. Raising an exception on a bad `STATE:` line
   would abort the turn for a trivial formatting error. Silently dropping
   invalid proposals and logging a warning is more robust; the proposal is still
   recorded on the `Turn` for debugging.

5. **Environment update is a first-class turn.**
   Representing the environment update as a `Turn` (with `party_id =
   environment.id`) means it appears in the episode record, is visible in
   episode history prompts, and is logged consistently with party turns.
   It also means the environment can propose state changes through the same
   validated pathway as any party.

6. **`HALT` does not abandon in-progress turns.**
   If the observer returns `HALT` during `after_turn`, the engine finishes
   persisting the completed turn before stopping. Abandoning a partial turn
   would leave the episode in an inconsistent state that is hard to resume.

7. **Importance heuristics are engine-set, not LLM-set.**
   Asking the LLM to assign importance to each memory entry would add latency
   and be inconsistent. The engine applies simple rules (state-changing turns
   are more important). The scenario designer or observer can override via
   `InjectionPayload.memory_writes`.

---

## Error Handling

| Situation | Behaviour |
|-----------|-----------|
| Provider returns empty output after retries | `RuntimeError`; turn not appended; episode not closed; engine raises to caller |
| Provider rate-limit exhausted across all fallback models | `ProviderExhaustedError` (see `06`); engine raises; episode persisted in open state for resumption |
| State validation failure on proposal | Proposal dropped, warning logged; episode continues |
| Memory `retrieve()` fails with `fail_mode="empty"` | Turn proceeds with empty memory block; warning logged |
| Memory `retrieve()` fails with `fail_mode="raise"` | `RuntimeError`; turn not executed |
| `before_episode` observer raises | Engine propagates; episode not started |
| `after_episode` observer raises | Engine propagates; episode already closed but not persisted; state may be partially updated |
| Persistence failure after episode close | `RuntimeError`; episode is closed in memory but not in DB; next `run_episode()` call will detect and re-persist |
| Compaction fails (LLM error) | Warning logged; compaction skipped; memory grows until next cycle |

---

## Testing Strategy

**Unit tests (all subsystems mocked):**

- `SimulationEngine.run_episode()` with 2 parties + environment: verifies
  scheduler called, provider called per party, turns appended in order,
  environment update called last, episode closed, memory written
- Observer `HALT` during `before_episode`: episode not started
- Observer `HALT` during `after_turn`: current turn persisted; no more turns
- Observer `INJECT` with `context_override`: injected text appears in next turn's prompt
- Observer `INJECT` with `state_updates`: party state updated before next turn
- Observer `INJECT` with `force_scheduler`: turns executed in forced order
- Observer `INJECT` with `memory_writes`: entries written to mock store before next turn
- Prompt assembly: character budget enforced; episode history trimmed before memory
- State proposal parsing: valid `STATE: key=value` applied; invalid dropped with warning
- Environment update: skipped when `environment_reactive=False`
- Memory write template mode: correct content and importance for state-changing vs plain turns
- Passive injection: bystander receives entry after episode with `importance=0.3`
- `checkpoint()` called once per episode when `auto_checkpoint=True`

**Integration tests (`@pytest.mark.integration`):**

- 3-party episode end-to-end with real LLM provider (Gemini)
- 10-episode run with memory compaction triggered
- Observer pause/resume via mock stdin
- Save → fork → run diverged branch → compare histories

**Edge cases:**

- Simulation with a single party (scheduler returns one id)
- Episode where no state proposals are made
- `InjectionPayload` with all fields None (no-op inject)
- Prompt assembly where all layers fit within budget (no trimming)
- Prompt assembly where even minimum persona + suffix exceeds budget (engine raises `ConfigurationError`)

**Coverage target:** ≥ 80% for `engine/`; episode lifecycle and prompt assembly ≥ 90%.

---

## Open Questions

None blocking.

The `passive_observation_parties` mechanism is intentionally simple for now.
A richer model (e.g., environment-driven visibility graph determining which
parties observe which turns) is deferred to a future stage once the basic loop
is proven.
