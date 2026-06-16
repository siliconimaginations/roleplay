# Roleplay — Work Plan

## Overview

Multi-party interaction simulator driven by LLM agents.
- **Core**: Python 3.12 + uv + async
- **LLM providers**: Gemini (default), Claude, extensible
- **Repo**: `github.com/siliconimaginations/roleplay` (public, AGPL 3 + commercial license)
- **Dev process**: Engineering spec → Implementation → PR → CI → review → merge
- **Work queue**: GitHub Projects board is authoritative; check it after every merge

---

## Progress Summary

| Stage | Name | Status |
|-------|------|--------|
| 0 | Foundation & Tooling | ✅ Complete |
| 1 | Core Design Docs | ✅ Complete |
| 2 | Core Domain Model | ✅ Complete |
| 3 | Memory Engine | ✅ Complete |
| 4 | Simulation Engine | ✅ Complete |
| 5 | LLM Provider Layer | ✅ Complete |
| POC | Scenario Runner (poc.py) | ✅ Complete |
| 6 | Persistence & Session | 🔲 Planned |
| 7 | CLI (roleplay run / inspect / fork) | 🔲 Planned |
| 8 | REST API | 🔲 Planned |
| 9 | Hardening & CI/CD Maturity | 🔲 Planned |

---

## Repository Structure

```
roleplay/
├── src/roleplay/
│   ├── core/          # Pure domain: Party, Environment, Episode, SimulationState
│   ├── memory/        # Memory store: write, retrieve, compact, forget
│   ├── engine/        # Simulation loop, episode orchestration, turn logic
│   ├── providers/     # LLM provider adapters (Gemini, Claude, …)
│   ├── persistence/   # SQLite session storage, serialization (Stage 6)
│   ├── api/           # REST API (Stage 8)
│   ├── poc.py         # Full-featured POC scenario runner (current primary CLI)
│   ├── cli.py         # Stub — full CLI coming in Stage 7
│   ├── config.py      # TOML scenario loader + .env key loader
│   └── validate.py    # TOML scenario validator CLI
├── tests/
├── scenarios/         # Example TOML scenario files
├── docs/
│   ├── engineering/   # Per-module engineering specs (.md)
│   ├── process/       # Tech debt cadence, QA workflow
│   └── scenario-format.md  # AI-readable TOML reference + generation tips
├── .github/
│   ├── badges/        # Auto-generated coverage badge SVG
│   ├── scripts/
│   │   └── gemini_review.py
│   └── workflows/
│       ├── ci.yml              # lint + type-check + test + coverage
│       └── gemini-review.yml   # AI code review on every PR
├── scripts/
│   └── lint.sh        # ruff + mypy, run before every push
└── pyproject.toml
```

---

## Stage 0 — Foundation & Tooling ✅

Repo live, CI running, local dev works.

- GitHub repo + branch conventions (main requires PR + CI green)
- Python 3.12 + uv package manager + pyproject.toml
- Package skeleton: `src/roleplay/` + `tests/` + smoke tests pass
- GitHub Actions CI: 2 parallel jobs — `lint` (ruff + mypy), `test` (pytest + coverage)
- Coverage threshold: 60% overall / 70% changed-files; badge auto-committed on push to main
- Gemini AI code review on every PR; blocks merge on 🔴 Critical / 🟠 Major issues
- `ENGINEERING_PRINCIPLES.md`: shared process rules for all contributors

---

## Stage 1 — Core Design Docs ✅

All engineering specs merged.

| Doc | Module | Status |
|-----|--------|--------|
| `docs/engineering/01-party-model.md` | Party, Persona, mutable state | ✅ Merged PR #3 |
| `docs/engineering/02-environment-model.md` | Environment party, state schema | ✅ Merged PR #4 |
| `docs/engineering/03-episode-model.md` | Episode, Turn, TurnScheduler, clock | ✅ Merged PR #5 |
| `docs/engineering/04-memory-engine.md` | Memory store, retrieval, compaction, forgetting | ✅ Merged PR #6 |
| `docs/engineering/05-simulation-engine.md` | Simulation loop, ObserverHook, prompt assembly | ✅ Merged PR #7 |
| `docs/engineering/06-provider-abstraction.md` | LLM protocol, Gemini/Claude adapters, Gemma 4 fallback | ✅ Merged PR #8 |
| `docs/engineering/07-persistence.md` | SQLite schema, session CRUD, fork/branch tree | ✅ Merged PR #9 |
| `docs/engineering/08-cli.md` | CLI commands, YAML scenario format, interactive pause | ✅ Merged PR #10 |
| `docs/engineering/09-api.md` | REST API | Deferred to Stage 8 |

---

## Stage 2 — Core Domain Model ✅

`src/roleplay/core/` — pure Python, zero I/O, zero LLM dependencies.

| Submodule | PRs | Notes |
|-----------|-----|-------|
| `Party` dataclass (person, organization, environment) | #11, #14 | Persona, mutable state, history |
| `Environment` party + state schema | #11, #14 | Physical + context tracking |
| `Episode` + `Turn` + schedulers + clocks | #13 | RoundRobin, Noop; simulated time |
| `SimulationState` | #14 | All parties + environment + episode log |

---

## Stage 3 — Memory Engine ✅

`src/roleplay/memory/`

| Submodule | PRs | Notes |
|-----------|-----|-------|
| `MemoryEntry`, `MemoryKind` | #15 | Typed entries with importance scoring |
| `InMemoryStore` | #15 | Relevance + recency retrieval |
| `MemoryStore` protocol | #15 | Typed interface for engine |

---

## Stage 4 — Simulation Engine ✅

`src/roleplay/engine/`

| Submodule | PRs | Notes |
|-----------|-----|-------|
| `SimulationEngine` — async episode loop | #16 | Drives turns, collects outputs, advances time |
| `ObserverHook` + `ObserverDirective` | #16 | Continue / halt / inject |
| `_assemble_prompt` (6-layer structure) | #16 | Budget trimming, history, memory, persona |
| Environment reactive turn | #16 | Env party updates state per episode |
| `ProviderExhaustedError` catch + graceful halt | #51 | Session summary still prints on exhaust |

---

## Stage 5 — LLM Provider Layer ✅

`src/roleplay/providers/`

| Submodule | PRs | Notes |
|-----------|-----|-------|
| `Provider` protocol + `CompletionRequest/Response` | #17 | Typed interface |
| `GeminiProvider` — model fallback chain | #17, #24, #26–28, #51, #56 | 6-model chain; Gemma 4 fallback |
| `ClaudeProvider` | #17 | Anthropic SDK adapter |
| `MockProvider` | #17 | Scripted responses; no API key |
| Session-level rate-limit skip list | #24 | Exhausted models skipped for session lifetime |
| RPM vs RPD skip-list distinction | #27, #51 | RPM (retry-after) not permanently banned |
| httpx timeout → `ProviderError` | #35 | ReadTimeout + ConnectTimeout caught |
| `ProviderRegistry` | #17 | Config-driven provider selection |

---

## POC Scenario Runner ✅ — `src/roleplay/poc.py`

The primary user-facing entry point until Stage 7 CLI is built.

```
uv run python -m roleplay.poc [OPTIONS]
```

| Feature | PR | Notes |
|---------|----|-------|
| TOML scenario loading via `config.py` | #19 | `--config`, `--env-file` flags |
| `.env` API key loading | #19 | Silently ignored if missing |
| Mock provider (`--mock`) | #17 | No API key; scripted responses |
| Verbosity 0: AI episode summaries | #29, #36 | One line per episode + env diff |
| Verbosity 1: full dialog stream | #25 | Default; each turn printed in real time |
| Verbosity 2: turn excerpts + AI summary | — | 80-char excerpt per turn + summary (planned #44) |
| Episode counter `N / M` in header | #47 | Shows progress through total episode count |
| Per-episode wall-clock timing `⏱` | #47 | Displayed after each episode |
| Model-switch notice `⚡` | #47 | Shown when fallback model is used |
| Goal tally `(goal achieved N / M ep)` | #47 | Running tally on ⊙ goal line |
| Session summary (models, tokens, duration) | #47 | Printed after `engine.run()` returns |
| Final env state snapshot | #47 | Printed in verbosity=0 mode |
| Checkpoint resume | — | `.checkpoint.json` survives crashes (planned #46) |
| `--watch` spinner | — | Dots to stderr during slow LLM calls (planned #45) |
| TOML validator CLI | #23 | `python -m roleplay.validate scenarios/x.toml` |
| Scenario format docs | #23, #53 | `docs/scenario-format.md`; AI-generation tips |

---

## Stage 6 — Persistence & Session 🔲

Implement `src/roleplay/persistence/`.

Prerequisite for `roleplay resume`, `roleplay fork`, and `roleplay inspect`.

| Submodule | Design Doc | Notes |
|-----------|-----------|-------|
| SQLite schema + migrations | `07-persistence.md` | Sessions, episodes, turns, memory entries |
| `SqlitePersistenceLayer` | `07-persistence.md` | Create, save, load, list, delete |
| Session save / resume | `07-persistence.md` | Full state round-trip across process restarts |
| Memory persistence | `07-persistence.md` | Durable store (replaces `InMemoryStore`) |
| Session fork / branching | `07-persistence.md` | `parent_session_id` + `forked_at_episode` |
| JSON export | `07-persistence.md` | For analysis and downstream tools |

Exit criteria: Session save/resume round-trips correctly; memory survives process restart; ≥ 80% coverage.

---

## Stage 7 — CLI UI 🔲

Implement the full `roleplay` CLI described in `docs/engineering/08-cli.md`.

Note: `poc.py` already covers the core `roleplay run` use case; Stage 7 adds persistence-backed commands, the YAML scenario format, and interactive pause mode.

| Submodule | Design Doc | Notes |
|-----------|-----------|-------|
| `roleplay run <scenario.yaml>` | `08-cli.md` | YAML loader; stream output to terminal |
| `roleplay resume <session_id>` | `08-cli.md` | Load from SQLite and continue |
| `roleplay inspect <session_id>` | `08-cli.md` | Dump party state, memory, episode log |
| `roleplay list` | `08-cli.md` | All sessions in DB |
| `roleplay fork <session_id>` | `08-cli.md` | Branch a session at current state |
| `roleplay forget` / `roleplay delete` | `08-cli.md` | Memory + session management |
| Interactive pause mode | `08-cli.md` | `p` to pause; inject / state / persona / memory commands |

Exit criteria: Both example scenarios runnable from CLI with real LLMs; resume tested after simulated crash.

---

## Stage 8 — REST API 🔲

Expose the simulator as a service.

| Submodule | Design Doc | Notes |
|-----------|-----------|-------|
| FastAPI app skeleton | `09-api.md` | Health, session CRUD |
| Simulation control endpoints | `09-api.md` | Start, pause, step, resume |
| WebSocket live updates | `09-api.md` | Stream episode turns to client |
| Auth (API key) | `09-api.md` | Simple key auth for self-hosted use |

---

## Stage 9 — Hardening & CI/CD Maturity 🔲

| Submodule | Notes |
|-----------|-------|
| Integration test suite | Full episode run with real LLMs; tagged `integration` |
| Performance profiling | Episode loop latency; memory retrieval under load |
| Coverage promotion | All targets raised 5 pp |
| Docker image | Single-container deployment |
| Cloud deploy guide | Docker Compose → cloud-ready config |

---

## Open UX Issues

| Issue | Title | Status |
|-------|-------|--------|
| #43 | Goal trend tally on ⊙ line | ✅ Shipped in PR #47 — close |
| #44 | Verbosity=2 (excerpts + summary) | 🔲 Planned |
| #45 | --watch spinner for slow LLM calls | 🔲 Planned |
| #46 | Checkpoint resume | 🔲 Planned |

---

## Development Process Per Feature

```
1. Engineering design doc               → PR → review → merge
2. Implementation (domain / engine / provider / persistence)
                                        → PR → CI green → review → merge
3. Integration test coverage            → PR → CI green → merge
```

**Branch naming**: `stage/<n>/<short-description>` or `feat/<short-description>`
**PR rules**: linked design doc (for new modules), lint + mypy green, tests added
**Lint**: run `bash scripts/lint.sh` before every push
