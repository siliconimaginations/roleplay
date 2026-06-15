# Memory Engine

## Purpose

The Memory Engine gives each party durable, retrievable recall that survives
context-window limits. Without it, parties in a long simulation would forget
everything beyond the last N episodes. The memory engine writes summaries of
what happened, retrieves the most relevant memories before each party's turn,
compacts old memories when storage grows large, and provides a first-class
forgetting API (intentional memory decay or deletion).

Memory is a first-class subsystem — not a side effect of the simulation loop.
The engine explicitly reads from and writes to it; compaction and forgetting run
as scheduled operations on the loop, not implicitly.

---

## Scope

**In scope:**
- `MemoryEntry` data structure
- `MemoryStore` protocol and SQLite-backed implementation
- Write API: how the engine records what happened in a turn or episode
- Retrieval API: how the engine fetches relevant memories before a turn
- Compaction: summarising many old entries into fewer shorter ones
- Forgetting: decay-based and explicit deletion
- Per-party memory isolation

**Out of scope:**
- Prompt assembly from retrieved memories (see `05-simulation-engine`)
- Persistence schema / SQLite DDL (see `07-persistence` — `MemoryStore`
  delegates storage to the persistence layer)
- LLM provider protocol used by compaction (see `06-provider-abstraction`)
- Episode history in the context window (see `03-episode-model`)

---

## Key Concepts / Domain Model

### MemoryEntry

A `MemoryEntry` is one atomic unit of stored recall. It belongs to exactly one
party and records a fact, observation, or summarised chunk that is relevant to
that party's future behaviour.

```python
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4


class MemoryKind(Enum):
    EPISODIC   = "episodic"    # Specific event: "Alice offered Bob 200 coins"
    SEMANTIC   = "semantic"    # General fact:   "Bob distrusts strangers"
    PROCEDURAL = "procedural"  # Behaviour rule: "When threatened, Bob leaves"
    COMPACTED  = "compacted"   # Summarised chunk of older episodic entries


@dataclass
class MemoryEntry:
    id: str                     # UUID, assigned at creation
    party_id: str               # Owner
    kind: MemoryKind
    content: str                # Natural-language text, injected verbatim into prompts
    episode_index: int          # Episode during which this memory was written
    importance: float = 1.0     # [0.0, 1.0]; used in retrieval scoring and compaction protection
    last_accessed_episode: int = 0
    access_count: int = 0
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    source_entry_ids: tuple[str, ...] = ()  # Non-empty for COMPACTED entries
```

`content` is always plain natural language — no JSON, no YAML — because it is
injected directly into the LLM prompt.

`importance` is set by the writer (engine or compactor) and never modified
after creation. Access statistics (`last_accessed_episode`, `access_count`) are
updated on retrieval.

### MemoryStore (protocol)

```python
from typing import Protocol


class MemoryStore(Protocol):
    # ── Write ────────────────────────────────────────────────────────────────

    async def write(self, entry: MemoryEntry) -> None:
        """Persist a new memory entry."""
        ...

    async def write_many(self, entries: list[MemoryEntry]) -> None:
        """Atomically persist multiple entries (e.g., after compaction)."""
        ...

    async def delete(self, entry_id: str) -> None:
        """Hard-delete one entry (used by explicit forgetting)."""
        ...

    async def delete_many(self, entry_ids: list[str]) -> None:
        """Hard-delete multiple entries atomically."""
        ...

    # ── Read ─────────────────────────────────────────────────────────────────

    async def retrieve(
        self,
        party_id: str,
        query: str,
        *,
        max_entries: int = 20,
        episode_index: int,
    ) -> list[MemoryEntry]:
        """Return up to `max_entries` entries for `party_id` ranked by
        relevance to `query`.

        Also updates `last_accessed_episode` and `access_count` on each
        returned entry.
        """
        ...

    async def list_all(
        self,
        party_id: str,
        *,
        kinds: frozenset[MemoryKind] | None = None,
    ) -> list[MemoryEntry]:
        """Return all entries for `party_id`, newest first.

        Optionally filter to specific kinds. Used by compaction and
        the inspection CLI command.
        """
        ...

    async def entry_count(self, party_id: str) -> int:
        """Return the total entry count for `party_id`."""
        ...

    # ── Stats ────────────────────────────────────────────────────────────────

    async def total_content_length(self, party_id: str) -> int:
        """Return the sum of len(entry.content) for `party_id`.

        Used to decide whether compaction is needed.
        """
        ...
```

### SqliteMemoryStore

The production implementation backed by the persistence layer (see
`07-persistence` for the full DDL). Delegates all SQL to the `PersistenceLayer`
passed at construction — no direct sqlite3 calls inside `SqliteMemoryStore`.
`SqliteMemoryStore` implements `MemoryStore` via duck typing (no explicit
`Protocol` subclassing required by Python's structural typing); `PersistenceLayer`
owns the DDL for the `memory_entries` table.

### InMemoryStore

Used in unit tests. Stores entries in a plain list; retrieval uses the same
scoring function as `SqliteMemoryStore` (extracted to a shared
`_score_entry()` module so both implementations are tested against identical
logic). No SQL dependency.

---

## Write API

The engine calls `write()` after each episode closes with entries for each
party that participated. Two write patterns:

### 1. Episodic write (after each episode)

The engine synthesises a short episodic entry from the episode's turns for each
party. The text is generated by the LLM or by a lightweight template rule,
depending on the `memory_write_mode` setting:

| Mode | Behaviour |
|------|-----------|
| `"llm"` | Engine calls the provider with a "summarise this turn for {party_name}'s memory" prompt. Richer, slower. |
| `"template"` | Engine formats a fixed template from turn output. Fast, deterministic. Default. |

### 2. Semantic/procedural write (explicit)

The engine (or a scenario script) can inject semantic or procedural entries at
any time via `write()`. These express persistent facts or behavioural rules that
outlast any episode window.

---

## Retrieval API

Before each party's turn, the engine calls `retrieve(party_id, query, ...)`.

The `query` is the current episode's accumulated context (other parties' turns
so far, environment state summary). The store scores entries by:

```
score(entry) = α × relevance(entry.content, query)
             + β × recency(entry.episode_index, current_episode_index)
             + γ × importance(entry.importance)
             + δ × access_frequency(entry.access_count)
```

Default weights: `α=0.5, β=0.25, γ=0.15, δ=0.10`.

**Basis for weights:** There is no single authoritative source; these are
empirical heuristics calibrated against the goal of surfacing *relevant and
timely* memories. The dominant term (α=0.5) reflects the standard IR finding
that query-term relevance is the primary signal for recall tasks (c.f. BM25's
design philosophy). Recency (β=0.25) models the recency effect in episodic
memory: recent events are more likely to influence current behaviour than distant
ones. Importance (γ=0.15) is a supplementary author-set signal. Access frequency
(δ=0.10) captures a reuse effect — memories retrieved before are more likely to
be relevant again. The weights are normalised to sum to 1.0 and are all exposed
as configuration so scenario designers can tune them. They should be validated
empirically during integration testing against representative scenarios.

**Relevance** is keyword/bigram overlap (no embedding model required by
default). An optional `EmbeddingRelevance` backend swaps in cosine similarity
via a pluggable embedding function (see Capacity Planning section for when this
is needed).

**Recency** decays linearly over a configurable `recency_window_episodes`
(default: 50). An entry at the current episode scores 1.0; one 50+ episodes old
scores 0.0.

The store returns entries sorted by score descending, up to `max_entries`. The
engine trims further to fit within the prompt's memory budget (measured in
characters, not tokens — see `05-simulation-engine`).

---

## Capacity Planning

Before fixing the default thresholds, we analyse a demanding scenario to verify
the design is tractable: **50 people, 50 simulated years, 1 episode per
simulated day** (18,250 episodes total).

### Raw entry growth (no compaction)

| Metric | Calculation | Value |
|--------|-------------|-------|
| Episodes | 50 yr × 365 days | 18,250 |
| Raw episodic entries per person | 1 per episode | 18,250 |
| Avg content length | ~200 chars/entry | — |
| Raw text per person | 18,250 × 200 | 3.65 MB |
| Total raw text (50 people) | 3.65 MB × 50 | 182 MB |
| SQLite row overhead (~4×) | 182 MB × 4 | ~730 MB |

730 MB for 50 people over 50 years is feasible but gets uncomfortable without
compaction, and retrieval over 18,250 rows per person per query would be slow.

### With compaction (threshold=200, batch=50)

Compaction runs when a party's entry count exceeds 200. Each compaction takes
the 50 lowest-importance EPISODIC entries oldest-first (see Algorithm below)
and replaces them with 1 COMPACTED entry — a net reduction of 49 entries per
run.

After N episodes, the steady-state entry count per party approaches:
```
peak count ≈ 200 + 50 = 250 entries (compaction fires, then 49 entries removed)
```
In the steady state the party oscillates between ~200 and ~250 entries.

After 18,250 episodes:
```
compaction runs ≈ (18,250 - 200) / 50 ≈ 361 per person
raw entries remaining ≈ 18,250 - (361 × 49) ≈ 560 episodic entries
compacted entries ≈ 361
total entries per person ≈ 560 + 361 ≈ 920
```

| Metric | Value |
|--------|-------|
| Entries per person (steady state) | ~920 |
| Text per person (560×200 + 361×500 chars) | ~293 KB |
| Total text, 50 people | ~14.6 MB |
| SQLite total (with overhead) | ~50–60 MB |

**Retrieval speed:** with an index on `(party_id, episode_index)` and
`importance`, scoring 920 rows in Python takes < 1 ms. Even at 50 parties
retrieving concurrently, total retrieval overhead per episode is well under
100 ms.

**Conclusion:** The default thresholds (compaction at 200 entries or 80 000
chars, batch of 50) are appropriate for this scale. For scenarios with faster
episode rates or more parties, the thresholds can be lowered; for lighter
scenarios, raised.

### Embedding retrieval

Keyword overlap retrieval on ~920 entries per party requires no external
service and scales easily to 50 parties. Embedding retrieval (cosine
similarity via an external API) is warranted only when:
- Entry count per party reliably exceeds ~2 000 (compaction keeps it well below
  this at default settings), **or**
- Topics are highly diverse and keyword overlap produces poor recall (testable
  empirically with a scenario-specific benchmark).

At default settings for this reference scenario, keyword retrieval is
sufficient. The embedding backend is an opt-in for power users.

---

## Compaction

Compaction reduces the number of stored entries for a party by summarising a
batch of lower-priority entries into a single `COMPACTED` entry.

### Trigger

Compaction runs at the end of an episode if either condition is true for any
party:

- `entry_count(party_id) > compaction_threshold` (default: 200 entries)
- `total_content_length(party_id) > compaction_char_limit` (default: 80 000 chars)

### Algorithm

High-importance entries carry durable facts or character-defining knowledge
and must not be silently lost to compaction. The algorithm therefore separates
*protected* entries from *compactable* ones before selecting the batch:

1. Fetch all `EPISODIC` entries for the party.
2. Split into:
   - **Protected**: `importance ≥ compaction_importance_floor` (default: 0.7).
     These are never included in a compaction batch.
   - **Compactable**: `importance < compaction_importance_floor`.
3. Sort compactable entries oldest-first.
4. Take the oldest `compaction_batch_size` (default: 50). If fewer than 10
   compactable entries exist, skip this compaction cycle (not worth the LLM
   call).
5. Call the LLM provider with a prompt:
   ```
   Summarise the following memories for {party_name} into a concise paragraph
   (max 500 characters). Preserve facts, relationships, and events that will
   matter in future interactions. Discard trivial details.

   Memories:
   {entry.content for entry in batch}
   ```
6. Create one `COMPACTED` entry with the LLM's response as `content`,
   `importance` set to the **maximum** importance of the source batch (so the
   compacted summary is ranked at least as highly as the most important source),
   and `source_entry_ids` pointing to all batch entries.
7. In a single DB transaction: `write_many([compacted_entry])` then
   `delete_many(batch entry ids)`.

### Importance and compaction interaction

Protected entries (importance ≥ 0.7) accumulate separately and are never
compacted. If a scenario produces many high-importance entries without
compaction relief, the engine logs a warning when protected entry count exceeds
`compaction_threshold / 2` (default: 100). The scenario designer should either
lower the importance floor or be more selective about what gets high importance
scores.

### Idempotency

If compaction fails mid-flight (LLM error), the DB transaction is rolled back:
source entries are not deleted and no compacted entry is written. The next
compaction cycle will retry with the same batch. "Transaction" here refers to a
SQLite database transaction — both the insert and the deletes are inside a
single `BEGIN … COMMIT` block.

### COMPACTED entries

`COMPACTED` entries are never re-compacted into a second level. If entry count
stays high after compaction, the engine compacts again from the remaining raw
`EPISODIC` entries. This avoids multi-level lossy compression.

---

## Forgetting

### Decay-based forgetting

When `forgetting_enabled = True` (default: `False`), the engine runs a decay
pass after compaction. Entries that have not been accessed in
`forgetting_idle_episodes` episodes (default: 100) and have `importance < 0.3`
are eligible for soft deletion.

Soft deletion: entries are marked `forgotten = True` in storage and excluded
from retrieval. They are not hard-deleted and can be recovered via the
inspection API.

### Explicit forgetting

```python
await store.delete(entry_id)        # Hard-delete one entry
await store.delete_many(entry_ids)  # Hard-delete multiple entries
```

The CLI `roleplay forget <session> <party> <entry_id>` delegates to this. See
`08-cli.md`.

Human-intervention mode (see `05-simulation-engine`) can call explicit
forgetting to inject a "selective amnesia" event — e.g., a character forgets
they know a secret.

---

## Cross-Party Memory

### Preferred mechanism: give Bob a turn

The cleanest way to record that Bob overheard Alice is to give Bob a turn in
the same episode — even a brief one — where the engine can provide Alice's
output as context. Bob's own LLM response then generates the episodic memory
naturally.

This is always feasible when:
- Both parties are in the same location (visible_to rules permit it), and
- The scenario calls for Bob to be an active participant in the episode.

### When cross-party injection is appropriate

Cross-party memory write (engine writes an entry to a party that had no turn)
is reserved for **passive observation**: Bob was physically present but the
episode is structured such that only Alice had a turn (e.g., Alice addressed
the room and the engine loops through all present parties writing a
"you-overheard" memory without giving each a turn). This is an optimisation for
large group scenes where giving every bystander a full LLM turn is prohibitively
expensive.

The engine policy for deciding when to use passive injection vs. active turns
is defined in `05-simulation-engine`. The memory engine's write API is
agnostic — it accepts writes for any party at any time.

---

## Design Decisions & Rationale

1. **Retrieval is keyword-overlap by default, not embeddings.**
   At the reference scale (50 parties, 50 years), keyword overlap is fast,
   deterministic, and sufficient (see Capacity Planning). An `EmbeddingRelevance`
   backend is provided as an opt-in for scenarios where semantic search matters.

2. **Compaction selects by lowest importance then oldest-first.**
   Importance is the primary guard: high-importance entries are protected
   entirely. Within the compactable pool, oldest-first is the tiebreaker —
   recent memories are more likely to be referenced in upcoming turns.

3. **`COMPACTED` entries inherit the maximum importance of their source batch.**
   A compacted summary is at least as important as the most significant thing
   it records. Using `max(source importance)` prevents a summary that contains
   an important fact from being ranked below newer trivial entries.

4. **`COMPACTED` entries are never re-compacted.**
   Multi-level compaction compounds information loss. Once a batch is summarised
   once, the summary is treated as a terminal form.

5. **Memory is per-party, not shared.**
   Parties have asymmetric information. Alice may remember something Bob does
   not. A shared global memory store would collapse this distinction.

6. **`total_content_length` uses character count, not tokens.**
   Token counts are provider-specific. Character count is a reliable proxy and
   cheap to compute in SQL (`SUM(LENGTH(content))`).

7. **Soft delete (forgetting) vs. hard delete.**
   Hard deletion loses the audit trail. Soft deletion lets the inspection CLI
   show what a party has forgotten and why. Hard delete is available for
   GDPR-style erasure and explicit scenario control.

8. **`memory_write_mode = "template"` is the default.**
   LLM-based memory writing doubles the number of LLM calls per episode.
   Template mode keeps costs predictable. Scenario designers can opt in to
   `"llm"` mode when fidelity matters more than cost.

---

## Error Handling

| Situation | Behaviour |
|-----------|-----------|
| `write()` storage failure | `RuntimeError` propagates to engine; episode turn still saved |
| `retrieve()` storage failure | `RuntimeError` propagates; engine may proceed with empty memories (configurable: `memory_retrieve_fail_mode = "raise" | "empty"`) |
| Compaction LLM call fails | DB transaction rolled back; source entries untouched; engine logs a warning; retry next cycle |
| Compaction LLM returns empty/too-long summary | Engine truncates at `compaction_max_chars` (default: 500) or retries once with a stricter prompt |
| `delete()` of non-existent entry_id | No-op (idempotent) |
| Protected entry count exceeds `compaction_threshold / 2` | Warning logged; no exception; designer should review importance assignments |

---

## Testing Strategy

**Unit tests (no LLM calls):**

- `MemoryEntry` construction; default field values
- `InMemoryStore.write` → `list_all` round-trip
- `InMemoryStore.retrieve` scoring: recent entry beats old entry; high-importance beats low-importance; keyword-matching beats non-matching
- `InMemoryStore.retrieve` with `max_entries` smaller than total count
- `InMemoryStore.entry_count` and `total_content_length`
- Compaction trigger: threshold not reached → no compaction; threshold reached → compaction called
- Compaction importance split: entries with importance ≥ 0.7 excluded from batch; entries below floor selected oldest-first
- Compaction batch: compacted entry importance = max of source batch
- Compaction algorithm: source entries deleted, compacted entry present, `source_entry_ids` correct
- Compaction idempotency: if LLM call fails, source entries remain, no compacted entry written
- Compaction skip: fewer than 10 compactable entries → no LLM call
- Forgetting decay: entry below importance threshold + idle episodes → soft-deleted; entry above threshold → retained
- Explicit delete: entry absent from subsequent `list_all`

**Integration tests (real LLM, tagged `@pytest.mark.integration`):**

- LLM-based compaction produces non-empty summary
- LLM-based memory write produces a coherent episodic entry
- Round-trip through `SqliteMemoryStore` (requires persistence layer from Stage 6)

**Edge cases:**

- `list_all` on party with zero entries
- `retrieve` on party with zero entries
- `write_many` with an empty list
- Compaction batch smaller than `compaction_batch_size`
- Two parties with the same episode index — retrieval isolation (Alice can't see Bob's memories)
- All EPISODIC entries are protected (importance ≥ floor) — compaction skipped

**Coverage target:** ≥ 90% for `core/memory` (scoring logic, MemoryEntry, compaction algorithm); ≥ 80% for `SqliteMemoryStore` on key paths.

---

## Open Questions

1. **Embedding retrieval backend**: keyword overlap is sufficient at default
   scale (< 1 000 entries per party; < 50 parties). Embedding retrieval becomes
   useful when either (a) entry count per party reliably exceeds ~2 000, or (b)
   scenario topics are highly diverse (testable via a per-scenario recall
   benchmark). The `EmbeddingRelevance` backend is a config opt-in; the
   threshold for recommending it will be documented after integration testing
   reveals real recall failure modes.

2. **Cross-party passive injection vs. active turns**: the preferred mechanism
   is an active turn. Passive injection is reserved for large group scenes where
   giving every bystander a turn is too expensive. The engine policy (when to
   inject vs. when to schedule a turn) is defined in `05-simulation-engine`.
   This is not a design gap in the memory engine — the write API is
   intentionally agnostic.
