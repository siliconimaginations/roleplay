# Environment Model

## Purpose

The Environment is the shared world every party inhabits. It encodes the
physical setting, cultural context, observable facts, and the current simulated
time. Unlike other parties, the Environment does not take turns or produce
dialogue — it is a read-only context source for all party prompts and a
write-only target for the engine when actions change the world.

---

## Scope

**In scope:**
- Semantics of `PartyKind.ENVIRONMENT` (built on `Party` from `01-party-model`)
- Conventional state key schema for the environment
- How the environment is included in party prompt context
- Environmental update protocol (engine-driven, not LLM-driven)
- Object and location tracking conventions and constraints
- Simulated time as an environment state variable

**Out of scope:**
- Simulated time *advancement* logic (how episodes increment it) — see `03-episode-model`
- Persistence of environment state — see `07-persistence`
- Multi-environment simulations — not supported; one environment per simulation

---

## Key Concepts / Domain Model

### Environment is a Party

The environment is constructed with `make_environment()` from `01-party-model`.
No new class is introduced. Its `kind` is `PartyKind.ENVIRONMENT`.

```
persona.description  →  narrative setting (location, era, culture, tone)
persona.knowledge    →  background facts all parties implicitly know
persona.goals        →  always empty
persona.constraints  →  always empty
state                →  mutable world state (time, weather, objects, locations)
```

The engine enforces that exactly one `PartyKind.ENVIRONMENT` party exists per
simulation and that no LLM turn is ever scheduled for it.

### State key schema

Environment state uses dot-separated key prefixes by convention. The schema is
not enforced by the domain model (state is still a flat `dict[str, StateValue]`
per the Party model), but violated conventions are flagged as warnings at
simulation construction time.

| Prefix | Examples | Meaning |
|--------|---------|---------|
| `time.simulated` | `"Day 3, 14:00"` | Current simulated time (string, human-readable) |
| `time.episode` | `7` | Episode index, incremented by the engine |
| `weather.*` | `weather.condition: "overcast"`, `weather.temp_c: 12` | Observable weather |
| `loc.<id>.*` | `loc.alice.place: "bakery"`, `loc.alice.visible_to: "public"` | Party locations |
| `obj.<id>.*` | `obj.key_ring.place: "alice_pocket"`, `obj.key_ring.visible_to: "alice"` | Object positions |
| `event.*` | `event.current: "town_fair"`, `event.active: true` | Active world events |

All values remain `StateValue` primitives. A party's location is
`loc.<party_id>.place: str`. An object's location is `obj.<obj_id>.place: str`.
`visible_to` is either a party ID (only that party sees it) or `"public"` (all
parties see it).

### Object and location tracking

Object tracking is **advisory, not authoritative**. The environment records
what a reasonable narrator would know — it is not a physics engine. Constraints:

- Objects that have not been explicitly placed are not tracked (no implicit
  "default location").
- The engine updates `obj.*` and `loc.*` keys when a turn's output clearly
  implies a physical change (e.g., "Alice picks up the key ring").
- If an object's location is ambiguous after a turn, the engine leaves the last
  known value unchanged and may add an `event.*` entry noting the ambiguity.
- There is no collision detection, inventory capacity, or spatial distance —
  those are simulation-specific concerns the scenario designer encodes in
  persona descriptions or as constraints.

### Simulated time

`time.simulated` is a human-readable string the scenario designer defines in
terms meaningful for the setting (e.g., `"Monday morning"`, `"Year 3, Spring"`,
`"T+00:45"`). The engine advances it according to the `SimulatedTimeClock`
configured in the episode model (see `03-episode-model`). The environment holds
the *current value*; the episode model defines the *advancement rule*.

`time.episode` is the zero-based count of completed episodes. It is always
present and is set by the engine, not the scenario designer.

### Prompt injection

The environment's context block is **prepended to every party's prompt**, before
that party's own persona and the episode history. This ensures all parties share
the same world view.

Format produced by `to_prompt_context()` for `PartyKind.ENVIRONMENT`:

```
## World: Millhaven (environment)
A small farming town in rural New England, late autumn 1987. The town has a
population of around 800. English is spoken; the culture is insular but not
unfriendly to outsiders.

Background facts:
- The annual harvest festival starts tomorrow
- The old mill has been closed for two years following a fire

Current world state:
- time.simulated: Monday, 7 November 1987, 09:15
- weather.condition: light rain
- weather.temp_c: 8
- loc.alice.place: town_square
- loc.baker_bob.place: bakery
- event.current: pre-festival preparations
```

Keys with `visible_to` set to a specific party ID are filtered out of other
parties' views before injection (handled by the engine, not by
`to_prompt_context()`; see `05-simulation-engine`).

---

## API / Interface

No new types are introduced. The interface is through the `Party` API from
`01-party-model` plus two environment-specific helpers:

```python
def make_environment(
    id: str,
    name: str,
    setting: str,
    facts: tuple[str, ...] = (),
    initial_state: dict[str, StateValue] | None = None,
) -> Party:
    """Construct the environment party.

    Args:
        id:            Unique party id (e.g. "world", "millhaven").
        name:          Display name shown in prompts (e.g. "Millhaven").
        setting:       Narrative description of the world — injected verbatim
                       as the environment's description.
        facts:         Immutable background facts (persona.knowledge).
        initial_state: Optional starting values for time/weather/loc/obj/event
                       keys. Validated against the key schema conventions.
    """
    ...


def validate_environment_state(state: dict[str, StateValue]) -> list[str]:
    """Return a list of warning strings for any keys that violate the
    dot-prefix schema convention. Empty list means no warnings.

    Does not raise — warnings are logged at simulation construction time.
    """
    ...
```

The engine also uses `Party.apply_state_update()` directly to push world
changes; no additional mutation API is needed.

---

## Design Decisions & Rationale

1. **No new class for Environment — reuse `Party` with `PartyKind.ENVIRONMENT`.**
   A dedicated `Environment` class would duplicate most of `Party` (id, name,
   persona, state, history) for marginal added clarity. The `PartyKind`
   discriminator is enough for the engine to apply special handling. This also
   means persistence, serialisation, and the memory engine all work uniformly
   across every participant type.

2. **Flat state keys with dot-prefix conventions, not nested dicts.**
   Consistent with decision #3 from the Party model. The dot prefixes give
   enough structure for human readability and programmatic filtering (e.g.,
   `{k: v for k, v in state.items() if k.startswith("loc.")}`) without
   introducing nesting or a schema registry.

3. **Object tracking is advisory.**
   A physics-accurate object tracker would require specifying reachability,
   weight, container logic, etc. — far beyond the simulator's scope. Advisory
   tracking (the engine updates state when it can confidently infer a change) is
   sufficient for social and negotiation simulations and keeps the complexity
   budget where it belongs: in the LLM's world model, not in code.

4. **`visible_to` filtering is the engine's responsibility, not `to_prompt_context()`.**
   `to_prompt_context()` renders all state keys unconditionally. The engine
   filters before passing context to a party's turn. This keeps the domain
   object simple and testable without a recipient parameter.

5. **Simulated time is a state variable, not a dedicated field.**
   `time.simulated` lives in state because it changes (the engine updates it
   each episode) and because its history is useful for replay. A dedicated
   `simulated_time` field would be redundant with state and would require special
   casing in serialisation.

6. **One environment per simulation.**
   Multiple environments would require routing rules (which party talks to which
   environment) and complicate the prompt assembly considerably. The strong
   majority of target use cases (town, negotiation room, data-centre planning
   process) have a single coherent world. Multi-environment support is deferred
   indefinitely.

---

## Error Handling

| Situation | Behaviour |
|-----------|-----------|
| Simulation constructed with zero `ENVIRONMENT` parties | `ValueError` at `SimulationState` construction |
| Simulation constructed with more than one `ENVIRONMENT` party | `ValueError` at `SimulationState` construction |
| Engine attempts to schedule an LLM turn for the environment | `RuntimeError` in engine |
| `validate_environment_state` finds unknown key prefix | Warning string returned; no exception raised |
| `apply_state_update` on environment called with non-`StateValue` | `ValueError` (from Party; no environment-specific handling) |

---

## Testing Strategy

**Unit tests:**
- `make_environment` constructs a `Party` with `kind == PartyKind.ENVIRONMENT`,
  empty goals, empty constraints, correct description and knowledge
- `make_environment` with `initial_state` populates state correctly
- `validate_environment_state` returns no warnings for valid key schema
- `validate_environment_state` returns warnings for arbitrary keys, nested-dict
  values (caught by Party-level type check), and other violations
- `to_prompt_context()` for environment party: renders Setting block and omits
  Goals/Traits/Constraints sections
- `to_prompt_context()` renders all state keys including `loc.*`, `obj.*`,
  `time.*`
- `apply_state_update` on environment behaves identically to other parties
  (covered by Party unit tests; environment needs only a smoke test)

**Edge cases:**
- Environment with no initial state keys
- Environment with only `time.*` keys and no location or object tracking
- `to_prompt_context()` with a very large number of state keys (no truncation
  at this layer — truncation is the engine's concern)

**Coverage target:** >= 90% (shared with Party module)

---

## Open Questions

None blocking.

Potential future extension: if a simulation needs **multiple locations with
independent weather and time** (e.g., characters in different cities), the
cleanest path is not multiple environments but compound `loc.*` / `weather.*`
keys with a location prefix (`weather.london.condition`, `weather.paris.condition`).
This is a scenario-design choice, not a model change.
