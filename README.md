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
description: "A small town drama"
session_id: my-first-run

config:
  default_provider: gemini
  max_episodes: 5
  goal: "Alice uncovers the truth or the sheriff reveals his secret"

# Named locations — parties use state.location to move between them
environments:
  - id: town_square
    name: Town Square
    description: The public centre of Maplewood. Conversations here are overheard.

  - id: sheriffs_office
    name: Sheriff's Office
    description: Bob's domain. Files and old case records fill the shelves.

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
      location: town_square

  - id: bob
    kind: person
    name: Bob
    persona:
      description: The town sheriff, loyal to the community but hiding something.
      goals: [keep the peace, protect the secret]
      traits: [calm, evasive, protective]
    state:
      mood: guarded
      location: sheriffs_office

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
uv run roleplay list --format json    # JSON output
```

### `roleplay inspect <session_id>`

Inspect a session's current state, memory, and episode log.

```bash
uv run roleplay inspect my-first-run
uv run roleplay inspect my-first-run --party alice        # One party only
uv run roleplay inspect my-first-run --memories           # Include memories
uv run roleplay inspect my-first-run --episodes           # Include episode log
uv run roleplay inspect my-first-run --format json
```

### `roleplay fork <session_id>`

Branch a session at its current state to explore an alternative timeline.

```bash
uv run roleplay fork my-first-run --new-id my-fork-1
```

### `roleplay forget <session_id> <party_id> <entry_id>`

Delete a specific memory entry from a party.

```bash
# Get the entry_id from: roleplay inspect <session_id> --memories --format json
uv run roleplay forget my-first-run alice mem_abc123
```

### `roleplay validate <scenario.yaml>`

Validate a scenario file without creating a session. Exits 0 on success, 1 with error list on failure.

```bash
uv run roleplay validate my_scenario.yaml
# ✓ my_scenario.yaml is valid.

# Returns a non-zero exit code and prints errors on invalid input:
# ✗ my_scenario.yaml has 2 error(s):
#   • parties: must include exactly one kind=environment party
#   • parties[0].persona.description: required field missing
```

### `roleplay export <session_id>`

Export a session's current state as JSON, including all parties with their personas, episode history, and injection markers. Useful for archiving, post-processing, or importing into other tools.

```bash
# Print JSON to stdout
uv run roleplay export my-session

# Save to a file
uv run roleplay export my-session -o session-export.json
```

The JSON includes `session_id`, `parties` (with `persona` and `initial_state`), `environment`, `environments` (named environments), and `episodes` (with turn history and summaries).

### `roleplay generate <prompt>`

Generate a complete YAML scenario from a natural-language prompt using an AI provider. The output is valid YAML ready to pass to `roleplay run` or `roleplay validate`.

```bash
# Print generated YAML to stdout
uv run roleplay generate "a tense salary negotiation between an employee and their manager"

# Save directly to a file
uv run roleplay generate "two rival chefs compete in a blind taste test" -o scenarios/chefs.yaml

# Use a specific provider
uv run roleplay generate "cold war spy exchange at a border crossing" --provider claude -o scenarios/spy.yaml
```

The command uses the `gemini` provider by default. Set `GEMINI_API_KEY` (or `CLAUDE_API_KEY` for `--provider claude`) in your environment before running.

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
| `description` | — | Human-readable description of the scenario |
| `config` | — | Simulation settings (see below) |
| `environments` | — | Named locations parties can move between (multi-environment support) |
| `scheduler` | — | Turn order (default: `round_robin`) |
| `clock` | — | Simulated time (default: `noop`) |
| `tools` | — | Tool handlers importable via dotted path |

### Config fields

```yaml
config:
  default_provider: gemini        # LLM provider: gemini | claude | mock
  default_model: gemini-2.0-flash # Model override (provider default if omitted)
  max_episodes: 10                # Stop after this many episodes
  context_window_episodes: 5      # How many past episodes the LLM sees
  memory_max_entries: 100         # Max memory entries per party
  forgetting_enabled: false       # Whether to compact old memories
  environment_reactive: true      # Environment party takes a turn each episode
  auto_checkpoint: true           # Save to DB after every episode
  goal: ""                        # End condition checked by LLM after each episode
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

# Frontend
cd frontend && npm install && npm run build   # production build
cd frontend && npm run dev                   # dev server with HMR
```


## Web UI

Roleplay ships a React + Vite single-page application served by the FastAPI backend.

### Development (hot-reload)

```bash
# Terminal 1 — backend
# GEMINI_API_KEY authenticates with the Gemini LLM API (get one at ai.google.dev)
# ROLEPLAY_API_KEY is your own secret that protects the Roleplay server endpoints
export GEMINI_API_KEY=<your-gemini-api-key>
export ROLEPLAY_API_KEY=dev              # any string you choose
uv run uvicorn roleplay.api.app:app --reload

# Terminal 2 — frontend dev server (proxies /sessions and /health to :8000)
cd frontend
npm install
npm run dev          # Opens http://localhost:5173
# Enter the same ROLEPLAY_API_KEY value ("dev") when the UI prompts for an API key
```

### Production build

```bash
cd frontend && npm run build   # emits frontend/dist/
uv run uvicorn roleplay.api.app:app --host 0.0.0.0 --port 8000
# The API now serves the SPA at / and all non-API paths.
```

### Docker (includes frontend)

```bash
docker build -t roleplay .
docker run -p 8000:8000 \
  -e ROLEPLAY_API_KEY=secret \
  -e GEMINI_API_KEY=<your-gemini-api-key> \
  roleplay
# Visit http://localhost:8000
# Enter "secret" when the UI prompts for an API key
```

Or with docker-compose:

```bash
# Edit docker-compose.yml to set GEMINI_API_KEY, then:
docker compose up
```

### Features

| Screen | What you can do |
|--------|-----------------|
| **Sessions list** | See all sessions, their status and episode count; refresh auto-polls every 5 s |
| **Create session** | Paste or edit a YAML scenario and submit — session is created immediately |
| **Live stream** | Watch turns stream in real time via WebSocket; colour-coded per party |
| **Generate from prompt** | Describe a scenario in plain English — the AI generates a complete valid YAML and populates the editor |
| **Validate** | Check a YAML scenario for errors before creating a session — shows a list of actionable errors in amber or a green "valid" confirmation |
| **Summary / Detail toggle** | Switch between a compact one-line-per-episode summary view and the full turn-by-turn dialog (with AI-generated summary shown below each episode) |
| **Run / Pause** | Start N episodes or pause mid-run from the browser |
| **Inject event** | Push a narrative event into the running simulation |
| **Inspector** | Browse party state, environment state, and session config |
| **Fork / Delete** | Fork a session at its current state, or delete it with one click |

## REST API

Roleplay exposes a FastAPI HTTP/WebSocket server for programmatic control.

### Start the server

```bash
uv run uvicorn roleplay.api.app:app --reload
# or with a custom DB and API key:
ROLEPLAY_DB_PATH=~/my.db ROLEPLAY_API_KEY=secret uv run uvicorn roleplay.api.app:app
```

### Authentication

When `ROLEPLAY_API_KEY` is set, all requests must include:

```
X-API-Key: <your-key>
```

Omit the env var entirely for unauthenticated dev mode.

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check |
| `POST` | `/sessions` | Create session from YAML body |
| `GET` | `/sessions` | List all sessions |
| `GET` | `/sessions/{id}` | Get session detail |
| `DELETE` | `/sessions/{id}` | Delete a session |
| `POST` | `/sessions/{id}/fork` | Fork session at current state |
| `GET` | `/sessions/{id}/yaml` | Return current session state as a YAML scenario document |
| `GET` | `/sessions/{id}/export` | Export full session state as JSON (parties, history, environments) |
| `POST` | `/sessions/validate` | Validate YAML without creating a session |
| `POST` | `/sessions/generate` | Generate a YAML scenario from a natural-language prompt |
| `POST` | `/sessions/{id}/run?episodes=N` | Run N episodes (background) |
| `GET` | `/sessions/{id}/status` | Get run status |
| `POST` | `/sessions/{id}/pause` | Request pause after current turn |
| `POST` | `/sessions/{id}/inject` | Inject narrative text into next episode |
| `GET` | `/sessions/{id}/history` | Episode + turn history (includes AI summaries) |
| `WS` | `/sessions/{id}/stream` | WebSocket live event stream |

### Create a session

```bash
curl -X POST http://localhost:8000/sessions \
  -H "Content-Type: text/plain" \
  --data-binary @scenarios/example.yaml
```

### Run and stream events

```bash
# Start 3 episodes
curl -X POST "http://localhost:8000/sessions/example-001/run?episodes=3"

# Stream events via WebSocket (wscat or similar)
wscat -c "ws://localhost:8000/sessions/example-001/stream"
```

WebSocket events: `connected`, `episode_start`, `turn`, `episode_end`, `simulation_complete`, `error`, `ping`.

Full API reference: [`docs/engineering/09-api.md`](docs/engineering/09-api.md).

## Project structure

```
roleplay/
├── src/roleplay/
│   ├── core/           # Party, Episode, SimulationState (pure Python)
│   ├── engine/         # Simulation loop, prompt assembly, ObserverHook
│   ├── memory/         # MemoryStore, compaction, forgetting
│   ├── providers/      # Gemini, Claude, Mock adapters
│   ├── persistence/    # SQLite session storage
│   ├── api/            # FastAPI REST server (app, routes, runner)
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
