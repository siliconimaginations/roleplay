# Environment Model

## Purpose

The Environment is the shared world context injected into every party's prompt.
The simulator supports **multiple named environments** (locations, rooms, spaces)
within a single simulation. Parties carry a `location` state key that identifies
which environment they are currently in. This allows parties to occupy different
spaces simultaneously, and the engine to enrich each party's prompt with the
description of the space they are actually in.

---

## Scope

**In scope:**
- Semantics of `PartyKind.ENVIRONMENT` (the single "world" party — unchanged)
- Named `Environment` objects in `EnvironmentRegistry` (new)
- `location` party state key and how the engine uses it
- Per-party environment description injection in prompt assembly
- Co-location filtering: only parties sharing a location speak to each other
- YAML schema for the `environments:` list
- Backward compatibility: simulations without `environments:` behave identically to before

**Out of scope:**
- Persistence of environment-level state (stored as party state on the ENVIRONMENT party, unchanged)
- Multi-environment simulated time (single `time.simulated` key on ENVIRONMENT party still governs all)
- Spatial distance / pathfinding — not supported; location is a discrete label

---

## Key Concepts

### The ENVIRONMENT party (unchanged)

One party with `kind: environment` is still required per simulation. It holds:
- `persona.description` → the global narrative setting (era, culture, tone)
- `persona.knowledge` → background facts all parties implicitly know
- `state` → mutable world state (`time.simulated`, `weather.*`, `event.*`, shared facts)

This party does not take turns and its context is prepended to every party's prompt
before their own persona block.

### Named environments (new)

An optional top-level `environments:` list in the YAML defines discrete named
locations that parties can occupy:

```yaml
environments:
  - id: town_square
    name: "Town Square"
    description: "A busy public space. Conversations here are overheard by all present."
    state:
      time_of_day: "morning"
  - id: town_hall
    name: "Town Hall"
    description: "Formal setting. The mayor presides. Proceedings are on the record."
  - id: general_store
    name: "General Store"
    description: "Run by Martha. Gossip spreads here faster than anywhere in town."
```

Each environment has:
- `id` (required) — unique key used in party `location` state
- `name` (required) — display name used in prompts
- `description` (required) — injected into each co-located party's prompt
- `state` (optional) — environment-specific key/value pairs included in the prompt

### Party location state

A party declares its starting location via its `state` block:

```yaml
parties:
  - id: sheriff
    name: "Sheriff Cole"
    kind: person
    state:
      location: town_square
```

`location` is a plain `StateValue` string. Parties update it via
`state_update_proposals` exactly like any other state key:

```
STATE: location=town_hall
```

The engine validates proposed location values against the known environment ids
and logs a warning (but does not reject) if the value is not recognised.

### Prompt assembly — per-party environment injection

When building a party's prompt, the engine checks the party's `location` state:

1. If `location` is set and matches an environment id in the registry, the
   environment's `name`, `description`, and `state` are included in the prompt's
   environment block, **in addition to** the global ENVIRONMENT party context.

2. If `location` is not set or the registry is empty, the prompt is assembled
   exactly as before (backward-compatible).

Prompt structure (updated layer 2):

```
[1] Party persona + state
[2] Global world context (ENVIRONMENT party description + state)
    [2b] Current location: <name> — <description>
         Location state: key=value ...
[3] Memory
[4] Episode history
[5] Current episode turns
[6] Instruction suffix
```

### Co-location filter

Before each turn in an episode, the engine filters the active speaker list to
parties that share the same `location` as the current speaker. The rules:

- If **both** the speaker and a potential responder have `location` set, they
  must match for the responder to be included.
- If **either** party has no `location` set, no filtering is applied (backward-
  compatible; all parties participate as before).
- If the registry is empty (no `environments:` defined), no filtering applies.

This means a conversation between the sheriff in the town square and the mayor
in the town hall cannot happen in a single turn — the parties must first move to
a shared location (or the scenario must be designed with that in mind).

---

## Data Model

### `Environment` dataclass (`core/environment.py`)

```python
@dataclass
class Environment:
    id: str
    name: str
    description: str
    state: dict[str, StateValue] = field(default_factory=dict)
```

### `EnvironmentRegistry`

```python
class EnvironmentRegistry:
    def __init__(self, environments: list[Environment]) -> None: ...
    def get(self, env_id: str) -> Environment | None: ...
    def ids(self) -> list[str]: ...
    def __bool__(self) -> bool: ...  # False when empty
```

### `SimulationState` extension

`SimulationState` gains an optional `environments: EnvironmentRegistry` field
(defaults to an empty registry). All existing code that does not use environments
is unaffected.

---

## YAML Schema

```yaml
# Optional — omit entirely for single-environment scenarios (backward-compatible)
environments:
  - id: <string>           # required — unique identifier
    name: <string>         # required — display name
    description: <string>  # required — injected into co-located party prompts
    state:                 # optional — environment-specific key/value pairs
      <key>: <value>
```

Validation:
- Duplicate `id` values → `ValidationError`
- Party `location` referencing an unknown environment id → warning (not error),
  so new environments can be added incrementally
- `environments:` list present but empty → treated same as absent (no-op)

---

## Design Decisions

1. **`location` is a plain party state key, not a first-class field.**
   Keeps the Party model unchanged. AI can update location via `STATE: location=…`
   exactly like any other state proposal. No new Party API required.

2. **Named environments are separate from the ENVIRONMENT party.**
   The ENVIRONMENT party remains the global world context. Named environments
   are additive overlays that describe sub-spaces within that world. This avoids
   breaking all existing scenarios.

3. **Co-location filter is opt-in via registry presence.**
   If no `environments:` block is defined, the filter is a no-op. This makes
   the feature entirely backward-compatible.

4. **Environment state is static per episode (not mutated by the engine).**
   Environment-level mutable world state continues to live on the ENVIRONMENT
   party's state dict. Named environment state is descriptive metadata — it does
   not change during an episode. Full environment state mutation is a potential
   future extension.

5. **No routing of state proposals to named environments.**
   `STATE: location=town_hall` updates the *party's* state. Named environments
   themselves do not receive state proposals in this version.

---

## Error Handling

| Situation | Behaviour |
|-----------|-----------|
| Duplicate environment id in YAML | `ValidationError` at load time |
| Party `location` references unknown environment id | Warning logged; party treated as unlocated for filtering |
| `environments:` key present but empty list | Treated as absent; no filtering |
| Registry empty and `location` set on party | No filtering; party participates in all turns |

---

## Testing Strategy

- `Environment` dataclass construction and field access
- `EnvironmentRegistry.get()` returns correct environment or `None`
- `EnvironmentRegistry.__bool__()` — True when non-empty
- YAML loading: `environments:` list parsed correctly into registry
- YAML loading: duplicate id raises `ValidationError`
- Prompt assembly: party with `location` gets environment description in env block
- Prompt assembly: party with no `location` gets no extra env block
- Prompt assembly: no registry → prompt unchanged (backward-compat)
- Co-location filter: parties in same location → all included
- Co-location filter: parties in different locations → responders filtered out
- Co-location filter: party with no location → no filtering applied
- Co-location filter: empty registry → no filtering applied
