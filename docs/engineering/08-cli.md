# CLI

## Purpose

The CLI is the primary user interface for Stage 7. It lets users define
scenarios in YAML, run simulations, observe episode output in real time, pause
and intervene, inspect session state, and manage sessions (list, resume, fork,
delete). It is also the first place where the human intervention (`ObserverHook`)
and save/load/branching features become user-accessible.

---

## Scope

**In scope:**
- Scenario file format (YAML)
- All CLI commands and their flags
- Real-time episode output streaming to the terminal
- Interactive pause / intervention mode (stdin-driven `ObserverHook`)
- `roleplay fork` command (branching)
- `roleplay inspect` command (state dump)
- Tool registration from scenario file
- Exit codes

**Out of scope:**
- REST API (see `09-api`)
- Web UI (future)
- Provider configuration beyond what is in the scenario file or environment variables

---

## Scenario File Format

A scenario is a single YAML file that fully describes the initial state of a
simulation. It is the only required input to `roleplay run`.

```yaml
# scenario.yaml — annotated example

session_id: town-2026-v1          # Optional; auto-generated UUID if omitted
description: "A small coastal town in the 1920s"

config:
  context_window_episodes: 10
  memory_max_entries: 20
  memory_char_budget: 4000
  memory_write_mode: template      # "template" | "llm"
  compaction_threshold: 200
  forgetting_enabled: false
  default_provider: gemini         # "gemini" | "claude"
  environment_reactive: true
  auto_checkpoint: true
  passive_observation_parties: []  # party_ids that receive passive memory writes

parties:
  - id: alice
    kind: person
    name: "Alice Harrow"
    persona:
      description: "The town's postmistress, sharp-eyed and cautious."
      goals:
        - "Keep the post office solvent"
        - "Learn who has been stealing from deliveries"
      traits:
        - "observant"
        - "reserved"
      knowledge:
        - "Three packages went missing last month"
      constraints:
        - "Never accuses without evidence"
    state:
      mood: neutral
      location: post_office

  - id: bob
    kind: person
    name: "Bob Crane"
    persona:
      description: "The harbour master, jovial but hiding a debt."
      goals:
        - "Pay off his creditors before the bank finds out"
      traits:
        - "gregarious"
        - "anxious under pressure"
    state:
      mood: anxious
      location: harbour

  - id: town
    kind: environment
    name: "Coastal Town"
    persona:
      description: "A quiet fishing town in decline. The economy depends on the sardine fleet."
    state:
      time.simulated: "1922-03-15 08:00"
      time.episode: 0
      weather.condition: overcast
      weather.temperature_c: 12
      loc.post_office.place: "Main Street"
      loc.post_office.visible_to: all
      loc.harbour.place: "South Wharf"
      loc.harbour.visible_to: all
      event.recent: none

clock:
  kind: formatted_increment
  unit: hours
  amount: 2
  format: "%Y-%m-%d %H:%M"

scheduler:
  kind: round_robin              # "round_robin" | "random_order" | "fixed"
  # order: [alice, bob]         # Only for kind: fixed

tools:
  # Built-in tools are always registered; list additional scenario-specific ones here
  - name: search_town_records
    description: "Search the town's public records (birth, death, property)."
    parameters:
      type: object
      properties:
        query:
          type: string
          description: "Search terms"
      required: [query]
    handler: roleplay.tools.builtin.mock_search  # Python dotted path; must be async callable
```

### Validation rules

- Exactly one `kind: environment` party is required.
- `session_id` must be unique across existing sessions (checked at startup;
  auto-generated if omitted).
- `clock.kind` must be one of `noop`, `formatted_increment`, `lambda`.
  `lambda` is not supported from YAML (only from Python API); the CLI rejects
  it with a clear error.
- `scheduler.kind` must be one of `round_robin`, `random_order`, `fixed`.
  `fixed` requires a non-empty `order` list containing valid party ids.
- `tools[].handler` must be a resolvable Python dotted path to an async
  callable. The CLI imports and validates it at startup, before the simulation
  starts.
- Unknown top-level keys raise a validation warning (not an error) to stay
  forward-compatible.

---

## Commands

### `roleplay run <scenario.yaml>`

Start a new simulation from a scenario file.

```
Usage: roleplay run [OPTIONS] SCENARIO

Options:
  --max-episodes INTEGER   Stop after N episodes (default: run indefinitely)
  --provider TEXT          Override default_provider from scenario file
  --output TEXT            Output mode: "stream" | "quiet" (default: stream)
  --interactive / --no-interactive
                           Enable interactive pause mode (default: true)
  --db TEXT                Path to SQLite DB file (default: ./roleplay.db)
```

**Stream output format** (one line per turn):

```
[Ep 1 | 1922-03-15 08:00] Alice Harrow
  I noticed the Crane boy loitering near the sorting room again this morning...
  STATE: mood=suspicious

[Ep 1 | 1922-03-15 08:00] Bob Crane
  Another fine morning at the harbour. Nothing unusual to report.

[Ep 1 | 1922-03-15 08:00] 🌍 Environment
  The morning fog lifts slightly. A delivery cart arrives at Main Street.
  STATE: weather.condition=partly_cloudy event.recent=delivery_arrived

[Ep 1 → 1922-03-15 10:00] Episode complete. Tokens: 1 240. Memories written: 2.
```

A divider is printed between episodes. Simulated time advances visibly.

### `roleplay resume <session_id>`

Resume a paused or interrupted session.

```
Usage: roleplay resume [OPTIONS] SESSION_ID

Options:
  --max-episodes INTEGER
  --interactive / --no-interactive
  --db TEXT
```

Loads the session via `PersistenceLayer.load_session()`, then resumes
`SimulationEngine.run()`. The terminal shows which episode is being resumed.

### `roleplay inspect <session_id>`

Dump current session state to stdout.

```
Usage: roleplay inspect [OPTIONS] SESSION_ID

Options:
  --party TEXT     Limit output to one party id
  --memories       Include memory entries in output
  --episodes INTEGER  Show last N episodes (default: 5)
  --format TEXT    "text" | "json" (default: text)
  --db TEXT
```

**Text output example:**

```
Session: town-2026-v1  Status: paused  Episodes: 12  Last saved: 2026-06-14 09:42

Parties:
  alice (Alice Harrow) [PERSON]
    State: mood=suspicious, location=post_office
    Memories: 47 entries (shown with --memories)

  bob (Bob Crane) [PERSON]
    State: mood=anxious, location=harbour
    Memories: 51 entries

  town (Coastal Town) [ENVIRONMENT]
    State: time.simulated=1922-03-16 14:00, weather.condition=overcast, ...

Recent episodes (last 5):
  Ep 8  1922-03-15 16:00 → 18:00  Turns: 3  Tokens: 1 102
  Ep 9  1922-03-15 18:00 → 20:00  Turns: 3  Tokens:  987
  ...
```

### `roleplay list`

List all sessions in the DB.

```
Usage: roleplay list [OPTIONS]

Options:
  --db TEXT
  --format TEXT   "text" | "json"
```

Output includes session_id, status, episode count, last saved, and
parent_session_id (for forks).

### `roleplay fork <session_id>`

Create a branched copy of a session at its current state.

```
Usage: roleplay fork [OPTIONS] SESSION_ID

Options:
  --new-id TEXT   New session_id for the fork (default: auto-generated)
  --db TEXT
```

Calls `PersistenceLayer.fork()`. Prints the new session_id. The user can then
run both branches independently with `roleplay run` / `roleplay resume`.

```
$ roleplay fork town-2026-v1
Forked: town-2026-v1 → town-2026-v1-fork-a3b7
Run the fork with: roleplay resume town-2026-v1-fork-a3b7
```

### `roleplay forget <session_id> <party_id> <entry_id>`

Hard-delete a specific memory entry.

```
Usage: roleplay forget SESSION_ID PARTY_ID ENTRY_ID [--db TEXT]
```

Useful for scenario editing and selective amnesia injection without entering
interactive mode.

### `roleplay validate <scenario.yaml>`

Validate a scenario file without creating a session. Exits 0 on success, 1 on any error.

```
Usage: roleplay validate [OPTIONS] FILES...

Options:
  --quiet / -q   Suppress warnings; only print errors.
```

```bash
uv run roleplay validate my_scenario.yaml
# ✓ my_scenario.yaml is valid.

uv run roleplay validate bad.yaml
# ✗ bad.yaml has 2 error(s):
#   parties: must include exactly one kind=environment party
#   parties[0].persona.description: required field missing
```

Accepts multiple files; prints a result block for each. The exit code is 1 if any file is invalid.

### `roleplay export <session_id>`

Export a session's current state as JSON. Includes party personas, episode history with AI summaries, injection markers, and named environments.

```
Usage: roleplay export [OPTIONS] SESSION_ID

Options:
  --output / -o TEXT   Write JSON to this file instead of stdout.
  --db TEXT            Path to SQLite DB file (default: ./roleplay.db).
```

```bash
# Print to stdout
uv run roleplay export my-session

# Save to file
uv run roleplay export my-session -o archive.json
```

The export format is:

```json
{
  "session_id": "my-session",
  "parties": [
    {
      "id": "alice",
      "name": "Alice",
      "kind": "person",
      "persona": { "description": "...", "goals": ["..."] }
    }
  ],
  "environment": { "id": "world", "name": "World", "persona": {} },
  "environments": [
    { "id": "hall", "name": "Hallway", "description": "A corridor." }
  ],
  "episodes": [
    {
      "episode": 0,
      "summary": "Alice and Bob discussed the contract.",
      "turns": [{ "party_id": "alice", "output": "..." }]
    }
  ]
}
```

### `roleplay delete <session_id>`

Delete a session and all its data.

```
Usage: roleplay delete [OPTIONS] SESSION_ID

Options:
  --confirm   Required flag to prevent accidental deletion
  --db TEXT
```

---

## Interactive Pause Mode

When `--interactive` is enabled (the default), the CLI registers a
`CliObserverHook` with the engine. The hook polls for keyboard input at each
`before_episode` and `after_episode` call point.

### Trigger

The user presses **`p`** (or **Enter**) at any time during episode streaming to
signal a pause request. The CLI sets a threading flag; the observer hook checks
it at the next `before_episode` call and returns `HALT`. The current episode
finishes first (HALT does not abort in-progress turns).

### Intervention prompt

Once paused, the CLI shows an intervention prompt:

```
⏸  Paused after episode 12. Commands:
  [c]ontinue          Resume the simulation
  [i]nject <text>     Inject an out-of-band event into the next episode
  [s]tate <party> <key>=<value>   Update a party's state
  [p]ersona <party> <field>=<value>  Update a party's persona field
  [m]emory <party> "<text>" [--importance 0.8]  Write a memory entry
  [f]orget <party> <entry_id>     Hard-delete a memory entry
  [o]rder <party_id> [<party_id>...]  Force speaker order for next episode
  [q]uit              Save and exit
  [d]iscard           Exit without saving current episode
  [?]                 Show this help
>
```

Each command maps directly to an `InjectionPayload` field or an engine
directive:

| Command | InjectionPayload field |
|---------|----------------------|
| `inject <text>` | `context_override` |
| `state alice mood=suspicious` | `state_updates["alice"]["mood"] = "suspicious"` |
| `persona alice goals="Find the thief"` | `persona_overrides["alice"]["goals"] = ["Find the thief"]` |
| `memory alice "Alice saw Bob near the warehouse" --importance 0.8` | `memory_writes` |
| `forget alice <entry_id>` | Calls `persistence.delete_memory()` directly |
| `order alice bob` | `force_scheduler = ["alice", "bob"]` |
| `continue` | `ObserverDirective.inject(payload)` (or `continue_()` if nothing changed) |
| `quit` | Checkpoints and exits |
| `discard` | Exits without persisting the open episode |

Multiple commands can be issued before `continue` — they accumulate into one
`InjectionPayload`.

---

## Tool Registration

Scenario-defined tools are loaded at CLI startup:

1. Parse `tools:` section of the scenario YAML.
2. For each tool, import the `handler` dotted path:
   ```python
   module, attr = handler.rsplit(".", 1)
   fn = getattr(importlib.import_module(module), attr)
   ```
3. Validate that `fn` is an async callable.
4. Register `ToolDefinition` + `fn` in the `ToolRegistry`.
5. Pass the registry to the `Provider` adapter.

If any tool fails to import, the CLI exits before starting the simulation with
a descriptive error.

---

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Clean exit (max_episodes reached, or user quit) |
| 1 | Configuration error (invalid scenario file, missing env var) |
| 2 | Runtime error (provider exhausted, DB failure) |
| 3 | User interrupted (`Ctrl+C`) — session is checkpointed before exit |

On `Ctrl+C`, the CLI catches `KeyboardInterrupt`, calls `engine.checkpoint()`,
and exits with code 3.

---

## Design Decisions & Rationale

1. **YAML for scenario files, not TOML or Python.**
   YAML is the most familiar format for config-heavy files in the Python
   ecosystem (Ansible, Docker Compose, GitHub Actions). TOML is cleaner for
   flat config but awkward for nested lists of parties. A Python DSL would be
   more powerful but introduces a security risk (arbitrary code execution on
   load). YAML with a strict schema is the right balance.

2. **Interactive mode polls at episode boundaries, not mid-turn.**
   Pausing mid-turn would leave the episode in an inconsistent state (some
   parties have spoken, others haven't). Polling at `before_episode` and
   `after_episode` is clean and predictable.

3. **Multiple intervention commands before `continue` accumulate into one payload.**
   Users often want to make several changes together (inject an event AND update
   a party's state). Accumulating them into one `InjectionPayload` before
   `continue` is simpler than applying each command immediately and avoids
   partial application.

4. **`roleplay fork` is a first-class command, not a flag on `run`.**
   Branching is a deliberate act that the user should invoke explicitly. Hiding
   it as a flag on `run` (e.g., `--fork-from`) would bury a significant
   capability.

5. **`--confirm` required for `delete`.**
   Accidental deletion of a 50-episode session would be painful. A required
   flag (not just a `y/n` prompt) makes deletion intentional and scriptable
   without interactive prompts.

6. **Stream output shows episode boundaries and token counts.**
   Knowing token consumption per episode helps users tune `max_output_tokens`,
   provider selection, and memory budget. This information is already available
   on `Episode.total_tokens()` — surfacing it in the stream adds no cost.

---

## Error Handling

| Situation | Behaviour |
|-----------|-----------|
| Scenario file not found | Exit code 1, descriptive message |
| Invalid YAML syntax | Exit code 1, line/column of error |
| Validation error (missing env party, bad scheduler) | Exit code 1, list of all errors |
| Tool handler import fails | Exit code 1, dotted path that failed |
| `ProviderExhaustedError` during run | Exit code 2; session checkpointed; message with attempted models |
| DB write failure during run | Exit code 2; session state may be partially persisted; message with advice to resume |
| `Ctrl+C` | Exit code 3; checkpoint called before exit |
| `roleplay resume` with unknown session_id | Exit code 1, `SessionNotFoundError` message |
| `roleplay fork` DB size exceeds limit | Warning printed; fork proceeds |

---

## Testing Strategy

**Unit tests:**

- Scenario YAML parsing: valid file → correct `SimulationState` fields
- Validation errors: missing env party, bad scheduler kind, unresolvable tool handler
- Tool registration: valid dotted path imported and registered; bad path → error
- Interactive pause: `InjectionPayload` correctly assembled from each command type
- Multiple commands before `continue`: accumulated into single payload
- Stream output format: episode header, turn block, episode footer
- Exit code mapping: each error scenario produces the correct code

**Integration tests (`@pytest.mark.integration`):**

- `roleplay run` on a 3-episode scenario with real provider: output to stdout, session in DB
- `roleplay resume` after simulated crash (open episode in DB)
- `roleplay fork` + `roleplay resume` on the fork: independent histories
- `roleplay inspect` JSON output matches DB state

**Edge cases:**

- Scenario with a single party (only environment + one person)
- `roleplay run --max-episodes 0` (should run zero episodes and exit cleanly)
- `Ctrl+C` during environment update turn (checkpoint should still succeed)
- `roleplay delete --confirm` on a session with child forks (deletes only the
  specified session; orphaned fork `parent_session_id` becomes stale — noted
  in the inspect output)

**Coverage target:** ≥ 80% for `cli.py`; scenario parser ≥ 90%.

---

## Open Questions

1. **Colour / rich output**: should the stream output use colour codes or rich
   formatting (e.g., `rich` library)? Deferred to implementation — add `rich`
   as an optional dependency if it improves readability without complicating
   tests.

2. **`roleplay delete` with child forks**: currently deletes only the specified
   session; orphaned forks keep their `parent_session_id` pointing to a deleted
   row. A future `--cascade` flag could delete the full subtree. Deferred.
