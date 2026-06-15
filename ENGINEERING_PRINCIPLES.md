# Engineering Principles & Teamwork Protocol

This document is the authoritative reference for how we work on Roleplay. All contributors (human and AI) must follow these principles. It is a living document — propose changes via PR.

---

## 1. Design Before Code

**No implementation PR is merged without a prior approved design.**

The sequence is strict:

```
Engineering design doc  (docs/engineering/<nn>-<module-name>.md)
        ↓ PR → review → merge
Implementation + integration tests (in the same PR)
        ↓ PR → CI green → review → merge
```

- A design doc PR may be small — a few hundred words is enough if the design is clear.
- Implementation PRs that arrive without a linked design doc are blocked, not reviewed.
- If a design changes materially during implementation, update the design doc in the same PR.
- Integration tests (tagged `@pytest.mark.integration`) are included in the same implementation PR, not a separate one. They are skipped in CI by default and run manually.

> **Note on UX docs:** A UX design doc phase will be introduced when a web UI is added (Stage 8+). Until then, CLI interaction is covered directly in the engineering doc for the relevant module.

---

## 2. Engineering Design Doc Standard

Every non-trivial submodule gets a design doc at `docs/engineering/<nn>-<module-name>.md`.

Minimum required sections:

```markdown
# <Module Name>

## Purpose
One paragraph: what problem this module solves and why it exists here.

## Scope
What is in scope. What is explicitly out of scope.

## Key Concepts / Domain Model
Entities, their fields, and relationships. Use tables or diagrams.

## API / Interface
Public interfaces, function signatures, REST endpoints, or CLI commands.
Use concrete Python types (dataclasses, TypedDicts, Protocols, JSON examples).

## Design Decisions & Rationale
Numbered list of non-obvious choices and why they were made.
Include alternatives considered and why they were rejected.

## Error Handling
How failures surface. What the caller should do.

## Testing Strategy
Unit tests: what is mocked, what is tested in isolation.
Integration tests: what real dependencies are exercised (LLM APIs, DB).
Edge cases to cover.

## Open Questions
Unresolved issues that will be decided during or after implementation.
```

---

## 3. Git Workflow

### Branch naming
```
stage/<n>/<short-description>
```
Examples: `stage/0/repo-scaffold`, `stage/1/party-model`, `stage/2/memory-engine`

### Commit messages
Follow Conventional Commits:
```
<type>(<scope>): <short summary>

[optional body — wrap at 72 chars]
[optional footer: Closes #<issue>]
```
Types: `feat`, `fix`, `test`, `docs`, `refactor`, `chore`, `ci`

### Pull requests
- One logical change per PR. Don't bundle unrelated fixes.
- PR title follows the same Conventional Commits format.
- Fill in the PR template (see `.github/PULL_REQUEST_TEMPLATE.md`).
- PRs against `main` require: CI green + at least one review approval.
- Aim for < 400 lines changed. Python is concise — a complete module (domain + logic + tests) often fits in one PR. Don't force artificial splits; split only when a module has genuinely independent pieces.

### Branch protection on `main`

> **Note**: GitHub branch protection rules require GitHub Pro for private repos. This repo operates on a free plan, so rules are not GitHub-enforced. The following constraints are **mandatory by convention**:

- **Never push directly to `main`**. All changes go through a PR on a named branch.
- **Never merge your own PR**. A second person (or Claude, for human-authored PRs) must review and approve.
- **Never merge with a red CI**. If CI fails, fix it before merging — no exceptions.
- **Never merge with unresolved review comments**. Resolve or explicitly acknowledge each comment before merging.
- **Critical PRs** (core simulation loop, memory model, LLM provider abstraction, API contracts, session model) require a review from `siliconimaginations` before merge.

---

## 4. Code Standards

### Python

- **Style**: `ruff` enforced in CI (lint + format). No suppressions without a comment explaining why.
- **Types**: `mypy --strict` enforced in CI. No `Any` without a `# type: ignore` comment and reason.
- **Naming**: follow PEP 8 — `snake_case` for functions/variables, `PascalCase` for classes, `UPPER_SNAKE_CASE` for constants.
- **Immutability first**: prefer frozen dataclasses and `tuple`/`frozenset` for data that should not change after construction.
- **Async**: use `async/await` throughout the simulation loop. No blocking I/O on the event loop.
- **Side effects**: keep domain logic (party model, memory, simulation loop) free of I/O. I/O belongs in the LLM provider layer and persistence layer.
- **Error handling**: use typed exceptions or `Result`-style returns for expected failures. Never swallow exceptions silently. LLM API errors must surface to the caller with enough context to retry or escalate.
- **Secrets**: API keys come from environment variables only. Never hardcode or log them.

### General

- **No magic numbers**: name every constant with domain meaning (e.g., `MAX_EPISODE_TOKENS = 8192`).
- **Delete dead code**: don't comment out code — commit history preserves it.
- **Dependencies**: add a dependency only when it clearly earns its place. Document why in the PR description. Prefer stdlib + a small set of well-maintained packages over a large dependency tree.

---

## 5. Testing Standards

| Layer | Tool | Coverage target |
|-------|------|----------------|
| Domain model (Party, Memory, Episode) | pytest | >= 90% |
| Simulation engine (loop, orchestration) | pytest + mocks | >= 80% |
| LLM provider adapters | pytest + mocks | >= 80% |
| Persistence layer | pytest (real SQLite) | key paths |
| CLI | pytest + capsys | smoke tests |
| Integration (real LLM APIs) | pytest -m integration | critical flows |

Rules:
- Tests live in `tests/` mirroring `src/roleplay/` structure.
- Integration tests that call real LLM APIs are tagged `@pytest.mark.integration` and skipped in CI by default (`-m "not integration"`). They are included in the same PR as the implementation they test.
- A PR that reduces coverage without a documented reason is rejected.
- Test names describe behaviour: `"memory_store returns recent episodes first"`, not `"test_memory_1"`.

---

## 6. Architecture Principles

### Separation of concerns

```
src/roleplay/
├── core/          # Pure domain: Party, Environment, Episode, SimulationState
├── memory/        # Memory store: write, retrieve, compact, forget
├── engine/        # Simulation loop, episode orchestration, turn logic
├── providers/     # LLM provider adapters (Gemini, Claude, ...)
├── persistence/   # SQLite session storage, serialization
├── api/           # REST API (added in Stage 8)
└── cli.py         # CLI entry point
```

- `core/` has zero I/O and zero LLM dependencies. It is the stable foundation.
- `providers/` is the only layer that calls external APIs. It can be mocked cleanly in tests.
- `engine/` orchestrates but does not call LLMs directly — it delegates to `providers/`.
- `persistence/` is the only layer that writes to disk.

### Provider abstraction
All LLM calls go through a `Provider` protocol. The engine only knows about the protocol, not specific models. This enables: model switching on rate limits, multi-provider fallback, and clean mocking in tests.

### Episode-driven time
The simulation advances in discrete episodes. Each episode has a wall-clock timestamp and an optional simulated-time mapping (e.g., one episode = one in-world hour). Time never advances automatically — it only advances when the engine processes an episode.

### Memory as a first-class subsystem
Memory is not a side effect of the simulation loop — it is a subsystem with its own read/write/compact/query API. The engine reads memory before each agent turn and writes new memories after. Compaction and forgetting are scheduled operations, not implicit truncation.

### Stateless API layer (future)
When REST/WebSocket APIs are added (Stage 8), controllers will be stateless. All simulation state lives in the session (persisted to DB).

---

## 7. CI / CD Requirements

Every PR must pass:
1. **Lint**: `ruff check .` and `ruff format --check .`
2. **Types**: `mypy src/` (strict mode)
3. **Unit tests**: all non-integration tests green (`-m "not integration"`)
4. **Coverage**: >= 60% overall, >= 70% on changed files

CI failures block merge — no exceptions.

Integration tests (`-m integration`) run manually or via `workflow_dispatch`, never as a required PR gate.

---

## 8. Documentation Standards

- Every public Python symbol (class, function, method, module) gets a docstring. One-liners are fine for obvious symbols.
- Every public function gets type hints on all parameters and the return type.
- REST endpoints (when added) are documented with examples in the engineering design doc — not only in code.
- The `docs/` folder is the canonical home for design and architecture docs.

---

## 9. AI Collaboration Protocol

When Claude (AI assistant) works on this codebase:

- Claude follows the same design-before-code sequence as human contributors.
- Claude does not push directly to `main`. All changes go through a PR.
- Claude writes a design doc PR first for any new submodule, awaiting review before implementation.
- Claude flags uncertainty explicitly — if a design choice is non-obvious, it is listed under "Design Decisions & Rationale" with the tradeoff explained.
- Claude does not silently change the scope of a task. If implementation reveals the design needs to change, Claude raises it in the PR description or chat before making the change.
- Claude treats this document as a hard constraint, not a suggestion.

### GitHub identity

Claude operates as **[`nagasawa94`](https://github.com/nagasawa94)** on GitHub — a dedicated bot account with Write access to this repo. All branches pushed and PRs opened by Claude will show `nagasawa94` as the author, keeping Claude's contributions clearly distinct from Rick's (`siliconimaginations`).

### PR classification

Every PR is either **non-critical** or **critical**:

| Type | Examples |
|------|---------|
| **Non-critical** | CI changes, tooling, coverage, linting, test fixes, doc updates, refactors within an approved design |
| **Critical** | Architecture decisions, module API contracts, simulation loop design, memory model, LLM provider abstraction, new submodule design docs |

### Review workflow

#### Non-critical PRs
1. `nagasawa94` opens the PR. Rick is **not** assigned as reviewer.
2. Claude polls every ~30 s for: all CI checks green · Gemini AI review present · no unresolved critical/major Gemini issues.
3. Minor Gemini suggestions -> add a `# TODO:` in code and open a GitHub issue. Do not block merge.
4. Once all criteria are met, Claude merges autonomously and notifies Rick in chat.
5. After merging, Claude checks the GitHub Projects board to determine the next task.

#### Critical PRs
1. `nagasawa94` opens the PR with `**PR Classification:** CRITICAL` in the description and assigns `siliconimaginations` as reviewer.
2. Poll CI and Gemini; address ALL Gemini issues. Do not wait for Rick before fixing Gemini findings.
3. Do **not** merge without Rick's explicit approval.
4. **Do not sit idle while waiting.** Continue working on other WORK_PLAN tasks that are not blocked.

#### Critical -> Non-critical transition
Rick signals by adding `_NCP` to the PR description or a review comment. Claude updates the classification and switches to autonomous merge flow.

#### Non-critical -> Critical escalation
- **Can be deferred:** open a GitHub issue, add `# TODO: #<issue>`, merge as non-critical.
- **Must be fixed in this PR:** update to `CRITICAL`, assign Rick, notify in chat.

#### When Rick says "keep working based on the plan"
Claude applies the non-critical workflow and works autonomously through `WORK_PLAN.md` until a critical decision point is reached, then pauses and notifies Rick.

| PR type | Author | Assigned reviewer | Merge |
|---------|--------|-------------------|-------|
| Non-critical (Claude) | `nagasawa94` | none | Claude merges when CI + Gemini pass |
| Critical (Claude) | `nagasawa94` | `siliconimaginations` | Rick must approve |
| Any (Rick) | `siliconimaginations` | `nagasawa94` | Claude reviews in chat; Rick merges |

**Merging without CI green and a passed Gemini review is a process violation.**

#### Determining the next task

After every merged PR:
1. Check the GitHub Projects board (link added once board is created).
2. Take the highest-priority item in **This Sprint** that is not blocked. `priority/P0` always takes precedence.
3. If the board is empty or all items are blocked, surface the situation to Rick.
4. Announce the next planned task in chat before starting — Rick can override.

---

## 10. Definition of Done

A feature is **done** when:
- [ ] Design doc merged to `main`
- [ ] Implementation PR approved and merged (includes integration tests)
- [ ] CI passes (lint + types + unit tests)
- [ ] Coverage targets met
- [ ] No unresolved review comments
- [ ] `WORK_PLAN.md` stage updated if the feature completed a stage milestone

---

## 11. PR Size Policy

**Aim for**: < 400 lines changed (excluding generated files and lock files).
**Hard limit**: 1 000 lines changed. A PR exceeding this must be split before review starts.

Python is concise — a complete module (domain model + logic + tests) often fits comfortably in one PR. Split only when a module has genuinely independent pieces (e.g., persistence schema separate from query logic), not to hit an arbitrary line count.

When splitting is necessary, each PR in the sequence must pass CI and be merged before the next opens.

**Claude-specific rule**: before starting implementation of any module, estimate the total line count. If the estimate exceeds 400 lines, propose a split plan in chat before writing code.

---

## 12. Tech Debt & Coverage Process

**Labels** (applied on every new issue):

| Label | Meaning |
|-------|---------|
| `tech-debt` | Refactor, cleanup, design debt |
| `performance` | Measurable speed or resource improvement |
| `coverage` | Test coverage gap |
| `docs` | Stale or missing documentation |
| `ci` | Pipeline or tooling change |
| `priority/P0` | **Urgent** -- active breakage, data loss, security issue, or blocker; must be resolved immediately |
| `priority/P1` | This sprint |
| `priority/P2` | Next 1-2 sprints |
| `priority/P3` | Backlog |

**Coverage thresholds** (enforced in CI):

| Scope | Minimum (overall) | Minimum (changed files on PR) |
|-------|-------------------|-------------------------------|
| All Python source | 60% | 70% |

Thresholds rise 5 pp per major stage milestone.
