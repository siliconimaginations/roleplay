# Roleplay

[![CI](https://github.com/siliconimaginations/roleplay/actions/workflows/ci.yml/badge.svg)](https://github.com/siliconimaginations/roleplay/actions/workflows/ci.yml)
[![Coverage](https://raw.githubusercontent.com/siliconimaginations/roleplay/badges/coverage.svg)](https://github.com/siliconimaginations/roleplay/tree/badges)

A multi-party interaction simulator. Configure parties — people, organizations, or environments — give them personas, memories, and goals, then watch LLM agents drive their interactions across discrete episodes.

## Use cases

- **Social simulation** — a small town where residents go about their lives, form relationships, and react to events
- **Organizational negotiation** — data center builders, grid operators, and transmission owners working through interconnection bottlenecks
- **Training & game development** — a clean API lets developers build games, training scenarios, or research tools on top

---

## Installation

```bash
# Install uv (if you don't have it)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install the roleplay CLI
git clone https://github.com/siliconimaginations/roleplay.git
cd roleplay
uv sync
```

You also need a Gemini API key (free tier works):

```bash
export GEMINI_API_KEY=your_key_here
# Or put it in a .env file in the project root
```

---

## Quick start

Create a scenario file (`my_scenario.yaml`):

```yaml
session_id: my-first-run
config:
  default_provider: gemini
  max_episodes: 5

parties:
  - id: alice
    kind: person
    name: Alice
    persona:
      description: A curious journalist investigating strange events in Maplewood.
      goals: [uncover the truth, protect her sources]
      traits: [tenacious, skeptical, empathetic]
    state:
      mood: determined

  - id: bob
    kind: person
    name: Bob
    persona:
      description: The town sheriff, loyal to the community but hiding something.
      goals: [keep the peace, protect the secret]
      traits: [calm, evasive, protective]
    state:
      mood: guarded

  - id: maplewood
    kind: environment
    name: Maplewood
    persona:
      description: A quiet Pacific Northwest town with a secret buried in its past.
    state:
      weather: overcast
      time.current: "Day 1, morning"
```

Run it:

```bash
uv run roleplay run my_scenario.yaml
```

---

## CLI reference

### `roleplay run <scenario.yaml>`

Run a scenario from a YAML file. Streams each turn to the terminal in real time.

```bash
uv run roleplay run my_scenario.yaml

# Override the number of episodes
uv run roleplay run my_scenario.yaml --max-episodes 10

# Use a different LLM provider
uv run roleplay run my_scenario.yaml --provider mock   # No API key needed

# Run without interactive pause mode
uv run roleplay run my_scenario.yaml --no-interactive

# Specify database file (default: roleplay.db)
uv run roleplay run my_scenario.yaml --db custom.db
```

### `roleplay resume <session_id>`

Resume a session that was interrupted or paused.

```bash
uv run roleplay resume my-first-run
uv run roleplay resume my-first-run --max-episodes 3   # Run 3 more episodes
```

### `roleplay list`

List all saved sessions.

```bash
uv run roleplay list
uv run roleplay list --fmt json    # JSON output
```

### `roleplay inspect <session_id>`

Inspect a session's current state, memory, and episode log.

```bash
uv run roleplay inspect my-first-run
uv run roleplay inspect my-first-run --party alice        # One party only
uv run roleplay inspect my-first-run --memories           # Include memories
uv run roleplay inspect my-first-run --episodes           # Include episode log
uv run roleplay inspect my-first-run --fmt json
```

### `roleplay fork <session_id>`

Branch a session at its current state to explore an alternative timeline.

```bash
uv run roleplay fork my-first-run --new-id my-fork-1
```

### `roleplay forget <session_id> <party_id> <entry_id>`

Delete a specific memory entry from a party.

```bash
# Get the entry_id from: roleplay inspect <session_id> --memories --fmt json
uv run roleplay forget my-first-run alice mem_abc123
```

### `roleplay delete <session_id>`

Permanently delete a session and all its data.

```bash
uv run roleplay delete my-first-run --confirm
```

---

## Scenario YAML format

### Top-level fields

| Field | Required | Description |
|-------|----------|-------------|
| `parties` | ✅ | List of party objects (must include exactly one `kind: environment`) |
| `session_id` | — | Unique ID for this session; auto-generated UUID if omitted |
| `config` | — | Simulation settings (see below) |
| `scheduler` | — | Turn order (default: `round_robin`) |
| `clock` | — | Simulated time (default: `noop`) |
| `tools` | — | Tool handlers importable via dotted path |

### Config fields

```yaml
config:
  default_provider: gemini        # LLM provider: gemini | claude | mock
  max_episodes: 10                # Stop after this many episodes
  context_window_episodes: 5      # How many past episodes the LLM sees
  memory_max_entries: 100         # Max memory entries per party
  forgetting_enabled: true        # Whether to compact old memories
```

### Party fields

```yaml
- id: alice                       # Unique identifier (required)
  kind: person                    # person | organization | environment (required)
  name: Alice                     # Display name (required)
  persona:
    description: "A journalist"   # Who this party is
    goals: [uncover the truth]    # What they want
    traits: [curious, tenacious]  # How they act
    knowledge: [local history]    # What they know
    constraints: [no violence]    # What they won't do
  state:                          # Initial state key-value pairs
    mood: determined
```

### Scheduler options

```yaml
scheduler:
  kind: round_robin        # Default — parties take turns in order
  # or:
  kind: random_order       # Randomise turn order each episode
  # or:
  kind: fixed
  order: [alice, bob]      # Fixed turn order by party ID
```

### Clock options

```yaml
clock:
  kind: noop               # Default — no simulated time
  # or:
  kind: formatted_increment
  unit: hours              # seconds | minutes | hours | days
  amount: 2                # Advance by this much each episode
  format: "%Y-%m-%d %H:%M"
```

---

## Interactive pause mode

During a run, press **`p`** (then Enter) to pause between episodes. Commands available at the pause prompt:

| Command | Effect |
|---------|--------|
| `c` | Continue |
| `i <text>` | Inject a narrative event into the next episode |
| `s <party> key=value` | Update a party's state |
| `m <party> "<text>"` | Add a memory entry |
| `o <p1> <p2> …` | Reorder upcoming turns |
| `q` | Quit (saves checkpoint) |

---

## Running without an API key

Use the built-in mock provider for testing and development:

```bash
uv run roleplay run my_scenario.yaml --provider mock
```

The mock provider returns scripted responses and requires no API key.

---

## Tech stack

| Layer | Technology |
|-------|-----------|
| Runtime | Python 3.12+ |
| Package manager | uv |
| LLM providers | Gemini (default), Claude, extensible |
| Persistence | SQLite (local dev), pluggable |
| CLI | Typer |
| CI/CD | GitHub Actions |

---

## Development setup

```bash
uv sync --group dev
uv run pytest           # Run tests
bash scripts/lint.sh    # ruff + mypy
```

## Project structure

```
roleplay/
├── src/roleplay/
│   ├── core/           # Party, Episode, SimulationState (pure Python)
│   ├── engine/         # Simulation loop, prompt assembly, ObserverHook
│   ├── memory/         # MemoryStore, compaction, forgetting
│   ├── providers/      # Gemini, Claude, Mock adapters
│   ├── persistence/    # SQLite session storage
│   ├── cli.py          # roleplay CLI (7 commands)
│   └── scenario_yaml.py # YAML scenario loader
├── tests/
├── scenarios/          # Example scenario files
└── docs/
    ├── engineering/    # Per-module design specs
    └── scenario-format.md  # YAML scenario format reference (AI-readable)
```

## Contributing

See [ENGINEERING_PRINCIPLES.md](ENGINEERING_PRINCIPLES.md) — all contributors follow the same design-before-code workflow.

Work plan and stage breakdown: [WORK_PLAN.md](WORK_PLAN.md).

## License

MIT License. See [LICENSE](LICENSE).
