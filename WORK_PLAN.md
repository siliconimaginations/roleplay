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
| 2 | Core Implementation | 🔲 Planned |
| 3 | Memory Engine | 🔲 Planned |
| 4 | Simulation Engine | 🔲 Planned |
| 5 | LLM Provider Layer | 🔲 Planned |
| 6 | Persistence & Session | 🔲 Planned |
| 7 | CLI UI | 🔲 Planned |
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
│   ├── persistence/   # SQLite session storage, serialization
│   ├── api/           # REST API (Stage 8)
│   └── cli.py         # CLI entry point
├── tests/
├── docs/
│   ├── engineering/   # Per-module engineering specs (.md)
│   └── process/       # Tech debt cadence, QA workflow
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

All engineering specs merged. No open questions block implementation.

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

### Advanced features — designed within Stage 1 docs

These features were scoped during Stage 1 and are lower-priority for initial
implementation. They are fully designed; no new design docs are needed.

| Feature | Where designed | Stage to implement |
|---------|---------------|-------------------|
| **Tool usage** (grounding via search, external APIs) | `06-provider-abstraction` | Stage 5 (LLM Provider Layer) |
| **Human intervention** (observer hook, inject context/persona) | `05-simulation-engine`, `08-cli` | Stage 4 (Simulation Engine) + Stage 7 (CLI) |
| **Save / load / branching** (fork sessions, game-like save states) | `07-persistence`, `08-cli` | Stage 6 (Persistence) + Stage 7 (CLI) |

---

## Stage 2 — Core Domain Model Implementation 🔲

Implement `src/roleplay/core/` — pure Python, zero I/O, zero LLM dependencies.

| Submodule | Design Doc | Notes |
|-----------|-----------|-------|
| `Party` dataclass | `01-party-model.md` | Persona, mutable state, history |
| `Environment` party | `02-environment-model.md` | Physical + context tracking |
| `Episode` dataclass | `03-episode-model.md` | Turn list, timestamps, simulated-time |
| `SimulationState` | `03-episode-model.md` | All parties + environment + episode log |

Exit criteria: Domain model fully typed; ≥ 90% coverage; mypy strict passes.

---

## Stage 3 — Memory Engine 🔲

Implement `src/roleplay/memory/`.

| Submodule | Design Doc | Notes |
|-----------|-----------|-------|
| Memory write + retrieval | `04-memory-engine.md` | Relevance scoring, recency weighting |
| Compaction (summarization) | `04-memory-engine.md` | LLM-assisted; triggered by token budget |
| Forgetting | `04-memory-engine.md` | Decay model; explicit forget API |
| Memory query API | `04-memory-engine.md` | Typed query interface for the engine |

Exit criteria: Memory store passes retrieval correctness tests; compaction tested with mocked LLM; ≥ 80% coverage.

---

## Stage 4 — Simulation Engine 🔲

Implement `src/roleplay/engine/`.

| Submodule | Design Doc | Notes |
|-----------|-----------|-------|
| Episode loop | `05-simulation-engine.md` | Drive turns, collect outputs, advance time |
| Turn scheduler | `05-simulation-engine.md` | Who speaks when; initiative rules |
| Environment reactions | `05-simulation-engine.md` | Env party updates state in response to turns |
| Orchestration agent (optional) | `05-simulation-engine.md` | AI-driven loop vs. rule-driven loop |

Exit criteria: A 3-party episode runs end-to-end with mocked LLM; state is consistent after each turn; ≥ 80% coverage.

---

## Stage 5 — LLM Provider Layer 🔲

Implement `src/roleplay/providers/`.

| Submodule | Design Doc | Notes |
|-----------|-----------|-------|
| `Provider` protocol | `06-provider-abstraction.md` | Typed interface all adapters implement |
| Gemini adapter | `06-provider-abstraction.md` | `google-generativeai`; model fallback |
| Claude adapter | `06-provider-abstraction.md` | Anthropic SDK; model fallback |
| Rate-limit handler | `06-provider-abstraction.md` | Exponential backoff; cross-model queue |
| Provider registry | `06-provider-abstraction.md` | Config-driven; default provider selection |

Exit criteria: Both adapters pass against real APIs in integration tests; rate-limit fallback tested with mocked 429s; ≥ 80% coverage.

---

## Stage 6 — Persistence & Session 🔲

Implement `src/roleplay/persistence/`.

| Submodule | Design Doc | Notes |
|-----------|-----------|-------|
| SQLite schema + migrations | `07-persistence.md` | Sessions, episodes, memory entries |
| Session save / resume | `07-persistence.md` | Full state round-trip |
| Memory persistence | `07-persistence.md` | Durable store for long-run episodes |
| Export (JSON) | `07-persistence.md` | For analysis and game integration |

Exit criteria: Session save/resume round-trips correctly; memory survives process restart; ≥ 80% coverage.

---

## Stage 7 — CLI UI 🔲

Implement a usable CLI for running and observing simulations.

| Submodule | Design Doc | Notes |
|-----------|-----------|-------|
| Scenario loader (YAML) | `08-cli.md` | Define parties, environment, episode rules in YAML |
| `roleplay run` command | `08-cli.md` | Stream episode output to terminal |
| `roleplay inspect` command | `08-cli.md` | Dump party state, memory, episode log |
| `roleplay replay` command | `08-cli.md` | Replay a persisted session |

Exit criteria: Both example scenarios (small town, org negotiation) runnable from CLI with real LLMs.

---

## Stage 8 — REST API 🔲

Expose the simulator as a service for downstream developers and the future web UI.

| Submodule | Design Doc | Notes |
|-----------|-----------|-------|
| FastAPI app skeleton | `09-api.md` | Health, session CRUD |
| Simulation control endpoints | `09-api.md` | Start, pause, step, resume |
| WebSocket live updates | `09-api.md` | Stream episode turns to client |
| Auth (API key) | `09-api.md` | Simple key auth for self-hosted use |

Exit criteria: Both example scenarios runnable via API; WebSocket streams turns in real time.

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

## Development Process Per Feature

```
1. Engineering design doc               → PR → review → merge
2. Implementation (domain / engine / provider / persistence)
                                        → PR → CI green → review → merge
3. Integration test coverage            → PR → CI green → merge
```

**Branch naming**: `stage/<n>/<short-description>`
**PR rules**: linked design doc, lint + mypy green, tests added
**Work queue**: always check the Projects board after each merge — it is authoritative over this file
**Lint**: run `bash scripts/lint.sh` before every push
