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
    importance: float = 1.0     # [0.0, 1.0]; used in retrieval scoring
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

### InMemoryStore

Used in unit tests. Stores entries in a plain list; retrieval uses the same
scoring function as `SqliteMemoryStore` (extracted to a shared module so both
implementations are tested against the same logic).

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

**Relevance** is keyword/bigram overlap (no embedding model required by
default). An optional `EmbeddingRelevance` backend swaps in cosine similarity
via a pluggable embedding function.

**Recency** decays linearly over a configurable `recency_window_episodes`
(default: 50). An entry at the current episode scores 1.0; one 50+ episodes old
scores 0.0.

The store returns entries sorted by score descending, up to `max_entries`. The
engine trims further to fit within the prompt's memory budget (measured in
characters, not tokens — see `05-simulation-engine`).

---

## Compaction

Compaction reduces the number of stored entries for a party by summarising a
batch of older entries into a single `COMPACTED` entry.

### Trigger

Compaction runs at the end of an episode if either condition is true for any
party:

- `entry_count(party_id) > compaction_threshold` (default: 200 entries)
- `total_content_length(party_id) > compaction_char_limit` (default: 80 000 chars)

### Algorithm

1. Fetch all `EPISODIC` entries for the party, sorted oldest-first.
2. Take the oldest `compaction_batch_size` entries (default: 50).
3. Call the LLM provider with a prompt:
   ```
   Summarise the following memories for {party_name} into a concise paragraph
   (max 500 characters). Preserve facts, relationships, and events that will
   matter in future interactions. Discard trivial details.

   Memories:
   {entry.content for entry in batch}
   ```
4. Create one `COMPACTED` entry with the LLM's response as `content` and
   `source_entry_ids` pointing to all batch entries.
5. `write_many([compacted_entry])` then `delete_many(batch entry ids)`.

### Idempotency

If compaction fails mid-flight (LLM error), the source entries are not deleted.
The next compaction cycle will retry. The compacted entry is only written after
all source entries are successfully deleted within a transaction.

### COMPACTED entries

`COMPACTED` entries are never re-compacted into a second level. If entry count
stays high after compaction, the engine compacts again — but always from the
remaining raw `EPISODIC` entries. This avoids lossy multi-level compression.

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

## Design Decisions & Rationale

1. **Retrieval is keyword-overlap by default, not embeddings.**
   Embedding models add an external dependency (an embedding API or local model)
   and latency. Keyword overlap is fast, deterministic, and sufficient for most
   scenarios. An `EmbeddingRelevance` backend is provided as an opt-in for
   scenarios where semantic search matters. This keeps the default setup
   dependency-free beyond the LLM provider.

2. **Compaction batch is oldest-first, not lowest-importance-first.**
   Importance is an approximation; the author (engine) may have mis-scored
   entries. Oldest-first is a safe default because recent memories are more
   likely to be referenced in upcoming turns. Importance is used in retrieval
   ranking, not compaction selection.

3. **`COMPACTED` entries are never re-compacted.**
   Multi-level compaction compounds information loss. Once a batch is summarised
   once, the summary is treated as a terminal form. If memory pressure remains
   high, the engine compacts more raw entries, not the summaries.

4. **Memory is per-party, not shared.**
   Parties have asymmetric information. Alice may remember something Bob does
   not. A shared global memory store would collapse this distinction. The engine
   can write the same observation as separate entries to multiple parties with
   different `importance` scores (e.g., an event is highly important to Alice,
   mildly noted by Bob).

5. **`total_content_length` uses character count, not tokens.**
   Token counts are provider-specific. Character count is a reliable proxy and
   cheap to compute in SQL (`SUM(LENGTH(content))`). The engine uses characters
   to decide compaction triggers and prompt budget trimming; actual token limits
   are enforced by the provider at call time.

6. **Soft delete (forgetting) vs. hard delete.**
   Hard deletion loses the audit trail. Soft deletion lets the inspection CLI
   show what a party has forgotten and why — useful for debugging surprising
   agent behaviour. Hard delete is available for GDPR-style erasure and explicit
   scenario control.

7. **`memory_write_mode = "template"` is the default.**
   LLM-based memory writing doubles the number of LLM calls per episode (one
   for each party's memory write). That cost is significant for long simulations.
   The template mode produces slightly lower-quality summaries but keeps costs
   predictable. Scenario designers can opt in to `"llm"` mode when fidelity
   matters more than cost.

---

## Error Handling

| Situation | Behaviour |
|-----------|-----------|
| `write()` storage failure | `RuntimeError` propagates to engine; episode turn still saved |
| `retrieve()` storage failure | `RuntimeError` propagates; engine may proceed with empty memories (configurable: `memory_retrieve_fail_mode = "raise" | "empty"`) |
| Compaction LLM call fails | Source entries are not deleted; compaction is skipped for this cycle; engine logs a warning |
| Compaction LLM returns empty/too-long summary | Engine truncates at `compaction_max_chars` (default: 500) or retries once with a stricter prompt |
| `delete()` of non-existent entry_id | No-op (idempotent) |

---

## Testing Strategy

**Unit tests (no LLM calls):**

- `MemoryEntry` construction; default field values
- `InMemoryStore.write` → `list_all` round-trip
- `InMemoryStore.retrieve` scoring: recent entry beats old entry; high-importance beats low-importance; keyword-matching beats non-matching
- `InMemoryStore.retrieve` with `max_entries` smaller than total count
- `InMemoryStore.entry_count` and `total_content_length`
- Compaction trigger: threshold not reached → no compaction; threshold reached → compaction called
- Compaction algorithm: source entries deleted, compacted entry present, `source_entry_ids` correct
- Compaction idempotency: if LLM call fails, source entries remain, no compacted entry written
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

**Coverage target:** ≥ 90% for `core/memory` (scoring logic, MemoryEntry, compaction algorithm); ≥ 80% for `SqliteMemoryStore` on key paths.

---

## Open Questions

1. **Embedding retrieval backend**: when should this be recommended over keyword
   overlap? The answer depends on the scenario length and topic diversity.
   Defer to implementation; add a config flag and document the trade-off.

2. **Cross-party memory injection**: can the engine write a memory to a party
   that wasn't active in the turn (e.g., "Bob overhead Alice say X even though
   Bob didn't have a turn")? The write API supports this — but the engine policy
   for when to do so is defined in `05-simulation-engine`.
