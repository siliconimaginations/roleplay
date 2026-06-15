# Party Model

## Purpose

The Party is the fundamental unit of the simulator. Every participant in a
simulation — a person, an organisation, or the environment itself — is a Party.
The Party model defines what a participant *is* (their persona), how they *are
right now* (their mutable state), and provides the interface by which the engine
turns that information into LLM prompt context.

All other modules depend on this model, so its design must be stable and minimal.

---

## Scope

**In scope:**
- `Party` dataclass: identity, kind, persona, mutable state, state change history
- `Persona` dataclass: structured, immutable description of who a party is
- `PartyKind` enum: person / organisation / environment
- State update API with append-only change log
- `to_prompt_context()`: serialise a party into text for LLM injection
- Validation rules for Party construction

**Out of scope:**
- Memory (handled by `04-memory-engine`)
- Relationships between parties (held on `SimulationState`, not on `Party`)
- Persistence / serialisation to SQLite (handled by `07-persistence`)
- LLM prompt assembly (handled by `05-simulation-engine`)

---

## Key Concepts / Domain Model

### PartyKind

```
PERSON        – an individual human or human-like agent
ORGANIZATION  – a collective entity (company, agency, team, government body)
ENVIRONMENT   – the shared world: physical setting, climate, culture, object positions
```

Every simulation has exactly one `ENVIRONMENT` party. All other parties are
`PERSON` or `ORGANIZATION`. The environment does not take conversational turns;
it is updated by the engine in response to party actions and external events.

### Persona

The persona is *who the party is* — stable across the simulation unless
deliberately changed. It is a frozen dataclass so accidental mutation is a
compile-time error.

| Field | Type | Meaning |
|-------|------|---------|
| `description` | `str` | Narrative fed verbatim into LLM prompts. Should capture background, role, voice. |
| `goals` | `tuple[str, ...]` | What this party is trying to achieve. Ordered by priority. |
| `traits` | `tuple[str, ...]` | Persistent behavioural tendencies (e.g. "risk-averse", "direct communicator"). |
| `knowledge` | `tuple[str, ...]` | Facts this party knows at simulation start. |
| `constraints` | `tuple[str, ...]` | Hard limits — things this party will never do regardless of pressure. |

`description` is the only required field. All others default to empty tuples.
The environment party typically leaves `goals` and `constraints` empty and uses
`knowledge` to encode the setting (location, culture, time period, rules).

### State

State is *how the party is right now* — it changes during the simulation.
It is a flat `dict[str, StateValue]` where `StateValue = str | int | float | bool | None`.

The flat dict is intentional: it is easy to serialise, easy to diff, and easy
for the engine to pass to an LLM as a structured summary. Nesting is disallowed;
complex values should be decomposed (e.g. `"health_physical": 80, "health_mental": 60`
rather than `"health": {"physical": 80, "mental": 60}`).

Every state mutation produces a `StateChange` record appended to an immutable
history list. This log is the audit trail for replay, debugging, and future
research use.

| Field | Type | Meaning |
|-------|------|---------|
| `key` | `str` | State variable name |
| `old_value` | `StateValue` | Value before the change |
| `new_value` | `StateValue` | Value after the change |
| `episode_index` | `int` | Which episode caused the change |
| `reason` | `str \| None` | Optional free-text rationale (from engine or LLM) |

### Party

| Field | Type | Notes |
|-------|------|-------|
| `id` | `str` | Unique within a simulation. Caller-assigned (e.g. `"alice"`, `"acme_corp"`). |
| `name` | `str` | Display name (may differ from id). |
| `kind` | `PartyKind` | Determines special engine handling for ENVIRONMENT. |
| `persona` | `Persona` | Frozen. Update only via `replace_persona()`. |
| `state` | `dict[str, StateValue]` | Mutable. Only updated via `apply_state_update()`. |
| `state_history` | `list[StateChange]` | Append-only. Never mutated directly. |
| `created_at` | `datetime` | UTC. Set at construction. |

---

## API / Interface

```python
from __future__ import annotations
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import NamedTuple

# ── Value type for all state variables ───────────────────────────────────────
StateValue = str | int | float | bool | None


# ── Enums ────────────────────────────────────────────────────────────────────
class PartyKind(Enum):
    PERSON = "person"
    ORGANIZATION = "organization"
    ENVIRONMENT = "environment"


# ── Persona ───────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Persona:
    description: str
    goals: tuple[str, ...] = ()
    traits: tuple[str, ...] = ()
    knowledge: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()


# ── State change record (immutable, NamedTuple for lightweight storage) ───────
class StateChange(NamedTuple):
    key: str
    old_value: StateValue
    new_value: StateValue
    episode_index: int
    reason: str | None


# ── Party ─────────────────────────────────────────────────────────────────────
@dataclass
class Party:
    id: str
    name: str
    kind: PartyKind
    persona: Persona
    state: dict[str, StateValue] = field(default_factory=dict)
    state_history: list[StateChange] = field(default_factory=list)
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    # ── State mutation ────────────────────────────────────────────────────────

    def apply_state_update(
        self,
        updates: dict[str, StateValue],
        episode_index: int,
        reason: str | None = None,
    ) -> list[StateChange]:
        """Apply a batch of state changes atomically.

        Returns the list of StateChange records that were appended.
        Raises ValueError if any value is not a valid StateValue type.
        """
        ...

    def get_state(self, key: str, default: StateValue = None) -> StateValue:
        """Return the current value of a state variable, or default."""
        ...

    def state_snapshot(self) -> dict[str, StateValue]:
        """Return a shallow copy of the current state dict."""
        ...

    # ── Persona update ────────────────────────────────────────────────────────

    def replace_persona(self, **changes: object) -> Party:
        """Return a new Party with an updated Persona (dataclasses.replace).

        Persona changes are rare and deliberate (e.g. a character arc completes,
        a company changes its mission). This method makes the change explicit
        and traceable via the caller's episode log rather than silent mutation.
        """
        ...

    # ── LLM context ──────────────────────────────────────────────────────────

    def to_prompt_context(self, *, include_state: bool = True) -> str:
        """Serialise this party into a text block for LLM prompt injection.

        Format (example):

            ## Alice (person)
            Alice is a retired schoolteacher living in Millhaven...

            Goals:
            - Maintain her vegetable garden

            Traits: warm, stubborn, curious

            Current state:
            - mood: content
            - location: town_square
            - health_physical: 90

        The environment party omits Goals/Traits and renders its knowledge
        entries as a "Setting" section instead.
        """
        ...
```

### Construction helpers

```python
def make_person(id: str, name: str, description: str, **persona_kwargs) -> Party:
    """Convenience constructor for a PERSON party."""
    ...

def make_organization(id: str, name: str, description: str, **persona_kwargs) -> Party:
    """Convenience constructor for an ORGANIZATION party."""
    ...

def make_environment(id: str, name: str, setting: str, facts: tuple[str, ...] = ()) -> Party:
    """Convenience constructor for the ENVIRONMENT party.

    `setting` becomes persona.description.
    `facts` becomes persona.knowledge.
    """
    ...
```

---

## Design Decisions & Rationale

1. **Single `Party` class with a `PartyKind` discriminator, not subclasses.**
   Subclasses (e.g. `Person(Party)`, `Organization(Party)`) would require
   isinstance checks throughout the engine and complicate serialisation. A single
   class with a discriminator keeps the engine code uniform and the persistence
   layer trivial. Special environment behaviour is handled by the engine checking
   `party.kind == PartyKind.ENVIRONMENT`, not by method overriding.

2. **`Persona` is a frozen dataclass.**
   Persona is *identity*, not *state*. Making it frozen prevents accidental
   mutation in the engine loop and signals clearly to readers that it changes
   rarely. The `replace_persona()` method on `Party` provides a deliberate,
   traceable escape hatch for the rare cases where persona genuinely evolves
   (character arc, company pivot).

3. **State is a flat `dict[str, StateValue]` with a primitive value union.**
   Nested dicts are harder to diff, harder to render for LLMs, and harder to
   query in persistence. Restricting values to `str | int | float | bool | None`
   keeps state JSON-serialisable without a custom encoder and makes the
   `to_prompt_context()` output predictable. The engine is responsible for
   decomposing complex concepts into multiple flat keys.

4. **State history is append-only, on the Party.**
   Alternatives considered: (a) store history only in the episode log (external),
   (b) store no history at all. Keeping history on the Party makes it available
   without joining to an episode log, which matters for memory compaction and
   for the `inspect` CLI command. Append-only ensures the audit trail is never
   accidentally overwritten.

5. **Memory lives in the memory engine, not on Party.**
   Embedding a memory list on `Party` would couple the memory retrieval strategy
   to the domain model. The memory engine (`04-memory-engine`) is responsible for
   storing and retrieving memories keyed by party ID. The Party is unaware of
   its own memory.

6. **Party IDs are plain strings, not wrapper types.**
   A `PartyId` newtype would add safety against mixing IDs from different entity
   types. However, in this domain there is only one kind of ID (parties don't
   share an ID space with episodes or sessions), so the safety gain is marginal
   and the ergonomic cost is real. Plain strings keep construction simple and
   JSON serialisation free.

7. **`to_prompt_context()` is on Party, not on the engine.**
   The engine assembles the full prompt from multiple sources (party context,
   memory, episode history, instructions). Each source should know how to render
   itself. Keeping rendering logic on the domain object makes it testable in
   isolation and keeps the engine lean.

---

## Error Handling

| Situation | Behaviour |
|-----------|-----------|
| `apply_state_update` receives a value that is not `StateValue` | `ValueError` with the offending key and value |
| `replace_persona` receives an unknown field name | `TypeError` (from `dataclasses.replace`) |
| Duplicate `Party.id` in the same simulation | Caught at `SimulationState` construction, not here |
| `to_prompt_context` called on a party with no description | Renders with an empty description; no exception |

---

## Testing Strategy

**Unit tests (no I/O, no LLM):**

- `Party` construction with valid and invalid arguments
- `apply_state_update`: happy path, type error, empty update, multi-key batch
- `state_snapshot` returns a copy (mutating snapshot does not affect party)
- `state_history` grows correctly across multiple updates; is never mutated directly
- `replace_persona` produces a new Party; original is unchanged
- `to_prompt_context` output for PERSON, ORGANIZATION, ENVIRONMENT parties
- `to_prompt_context` with `include_state=False` omits the state block
- `make_person`, `make_organization`, `make_environment` convenience constructors

**Edge cases:**
- State update with no changes (empty dict) — should be a no-op, no history entry
- State update setting a key to `None` (explicit null)
- Persona with all optional fields empty
- Environment party with no knowledge entries
- `to_prompt_context` with very long description (no truncation at this layer)

**Coverage target:** >= 90%

---

## Open Questions

None — this module is fully specified. Decisions that might be revisited:

- If simulations with thousands of parties reveal that `state_history` memory
  usage is a problem, history could be moved to the persistence layer and loaded
  lazily. This is a Stage 7+ concern.
- If persona evolution turns out to be frequent (not rare), we may want to add
  a `persona_history` list analogous to `state_history`. Deferred until we have
  real simulation experience.
