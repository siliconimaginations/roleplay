# Persistence

## Purpose

The Persistence layer durably stores simulation sessions so they survive process
restarts, can be resumed after interruption, and can be branched (forked) into
diverging timelines. It owns the SQLite schema, all SQL, and the
serialisation/deserialisation logic that converts between Python domain objects
and stored rows. No other layer touches the database directly.

---

## Scope

**In scope:**
- SQLite schema: all tables and indices
- `PersistenceLayer` protocol and `SqlitePersistenceLayer` implementation
- Session lifecycle: create, save, load, list, delete
- Episode and turn persistence
- Memory entry persistence (the SQL that `SqliteMemoryStore` delegates to)
- Checkpoint and fork (branching) semantics
- JSON export of a full session
- Schema migrations (Alembic-style versioned scripts)

**Out of scope:**
- Memory scoring / compaction logic (see `04-memory-engine`)
- Domain object behaviour (see `core/`)
- Remote or cloud storage (a future extension; the protocol is the seam)

---

## Schema

All tables use `TEXT` primary keys (UUIDs) and store timestamps as ISO-8601
strings in UTC. Booleans are stored as `INTEGER` (0/1).

```sql
-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER NOT NULL,
    applied_at  TEXT NOT NULL
);

-- One row per simulation session (or fork thereof)
CREATE TABLE IF NOT EXISTS sessions (
    session_id          TEXT PRIMARY KEY,
    parent_session_id   TEXT,           -- Non-null for forks
    forked_at_episode   INTEGER,        -- Episode index at which fork was taken
    config_json         TEXT NOT NULL,  -- SimulationConfig serialised as JSON
    started_at          TEXT NOT NULL,
    last_saved_at       TEXT NOT NULL,
    status              TEXT NOT NULL   -- "running" | "paused" | "complete"
);

-- One row per registered party in the session
CREATE TABLE IF NOT EXISTS parties (
    party_id    TEXT NOT NULL,
    session_id  TEXT NOT NULL REFERENCES sessions(session_id),
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL,          -- "PERSON" | "ORGANIZATION" | "ENVIRONMENT"
    persona_json TEXT NOT NULL,         -- Persona fields serialised as JSON
    PRIMARY KEY (party_id, session_id)
);

-- One row per party state change (append-only)
CREATE TABLE IF NOT EXISTS state_changes (
    id              TEXT PRIMARY KEY,
    party_id        TEXT NOT NULL,
    session_id      TEXT NOT NULL,
    key             TEXT NOT NULL,
    old_value_json  TEXT,               -- JSON-encoded StateValue (null for first write)
    new_value_json  TEXT NOT NULL,
    episode_index   INTEGER NOT NULL,
    reason          TEXT
);
CREATE INDEX IF NOT EXISTS idx_state_changes_party
    ON state_changes (party_id, session_id, episode_index);

-- One row per episode
CREATE TABLE IF NOT EXISTS episodes (
    episode_id          TEXT PRIMARY KEY,
    session_id          TEXT NOT NULL REFERENCES sessions(session_id),
    episode_index       INTEGER NOT NULL,
    simulated_time_start TEXT NOT NULL,
    simulated_time_end  TEXT,           -- NULL while open
    started_at          TEXT NOT NULL,
    ended_at            TEXT,           -- NULL while open
    UNIQUE (session_id, episode_index)
);
CREATE INDEX IF NOT EXISTS idx_episodes_session
    ON episodes (session_id, episode_index);

-- One row per turn
CREATE TABLE IF NOT EXISTS turns (
    turn_id             TEXT PRIMARY KEY,
    episode_id          TEXT NOT NULL REFERENCES episodes(episode_id),
    session_id          TEXT NOT NULL,
    party_id            TEXT NOT NULL,
    turn_index          INTEGER NOT NULL,
    output              TEXT NOT NULL,
    state_proposals_json TEXT NOT NULL, -- dict[str, StateValue] as JSON
    tool_calls_json     TEXT NOT NULL,  -- list[ToolCallResult] as JSON
    prompt_tokens       INTEGER NOT NULL DEFAULT 0,
    completion_tokens   INTEGER NOT NULL DEFAULT 0,
    model_used          TEXT NOT NULL DEFAULT '',
    timestamp           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_turns_episode
    ON turns (episode_id, turn_index);

-- One row per memory entry
CREATE TABLE IF NOT EXISTS memory_entries (
    entry_id                TEXT PRIMARY KEY,
    party_id                TEXT NOT NULL,
    session_id              TEXT NOT NULL,
    kind                    TEXT NOT NULL,  -- MemoryKind value
    content                 TEXT NOT NULL,
    episode_index           INTEGER NOT NULL,
    importance              REAL NOT NULL DEFAULT 1.0,
    last_accessed_episode   INTEGER NOT NULL DEFAULT 0,
    access_count            INTEGER NOT NULL DEFAULT 0,
    source_entry_ids_json   TEXT NOT NULL DEFAULT '[]',
    created_at              TEXT NOT NULL,
    forgotten               INTEGER NOT NULL DEFAULT 0  -- soft-delete flag
);
CREATE INDEX IF NOT EXISTS idx_memory_party
    ON memory_entries (party_id, session_id, forgotten, episode_index);
CREATE INDEX IF NOT EXISTS idx_memory_importance
    ON memory_entries (party_id, session_id, importance)
    WHERE forgotten = 0;
```

Current schema version: **1**.

---

## PersistenceLayer Protocol

```python
from typing import Protocol
from roleplay.core.party import Party
from roleplay.core.episode import Episode, SimulationHistory
from roleplay.core.simulation_state import SimulationState, SimulationConfig
from roleplay.memory.store import MemoryEntry


class PersistenceLayer(Protocol):
    # ── Session lifecycle ────────────────────────────────────────────────────

    async def create_session(self, state: SimulationState) -> None:
        """Write the session row and all party rows. Idempotent."""
        ...

    async def save_state(self, state: SimulationState) -> None:
        """Persist current party states and update last_saved_at.

        Only writes rows that have changed since the last save (diff-based).
        """
        ...

    async def load_session(self, session_id: str) -> SimulationState:
        """Reconstruct a SimulationState from the DB.

        Raises SessionNotFoundError if session_id does not exist.
        Replays state_changes to rebuild Party.state and state_history.
        """
        ...

    async def list_sessions(self) -> list[SessionSummary]:
        """Return all sessions sorted by last_saved_at descending."""
        ...

    async def delete_session(self, session_id: str) -> None:
        """Hard-delete all rows for this session_id across all tables."""
        ...

    # ── Episode and turn persistence ─────────────────────────────────────────

    async def save_episode(self, session_id: str, episode: Episode) -> None:
        """Persist or update the episode row and all its turn rows.

        Called once when the episode opens (with ended_at=None) and again when
        it closes (updating ended_at and simulated_time_end).
        """
        ...

    async def load_history(
        self, session_id: str, max_episodes: int | None = None
    ) -> SimulationHistory:
        """Load the episode + turn history for a session.

        If max_episodes is set, loads only the most recent N episodes.
        """
        ...

    # ── Memory ───────────────────────────────────────────────────────────────

    async def write_memory(self, session_id: str, entry: MemoryEntry) -> None: ...
    async def write_memories(self, session_id: str, entries: list[MemoryEntry]) -> None: ...
    async def delete_memory(self, session_id: str, entry_id: str) -> None: ...
    async def delete_memories(self, session_id: str, entry_ids: list[str]) -> None: ...
    async def retrieve_memories(
        self, session_id: str, party_id: str, *, kinds: frozenset | None = None
    ) -> list[MemoryEntry]: ...
    async def update_memory_access(
        self, session_id: str, entry_id: str, episode_index: int
    ) -> None: ...
    async def memory_entry_count(self, session_id: str, party_id: str) -> int: ...
    async def memory_total_content_length(self, session_id: str, party_id: str) -> int: ...

    # ── Checkpoint and fork ──────────────────────────────────────────────────

    async def checkpoint(self, state: SimulationState) -> str:
        """Atomically persist all current state. Returns checkpoint_id (= session_id)."""
        ...

    async def fork(
        self, session_id: str, new_session_id: str
    ) -> SimulationState:
        """Deep-copy the session under new_session_id.

        Copies all rows (parties, state_changes, episodes, turns, memory_entries)
        with the new session_id. Sets sessions.parent_session_id and
        sessions.forked_at_episode. Returns the new SimulationState.
        """
        ...

    # ── Export ───────────────────────────────────────────────────────────────

    async def export_json(self, session_id: str) -> dict[str, object]:
        """Return the full session as a JSON-serialisable dict."""
        ...
```

### SessionSummary

```python
@dataclass(frozen=True)
class SessionSummary:
    session_id: str
    parent_session_id: str | None
    forked_at_episode: int | None
    episode_count: int
    party_count: int
    status: str
    started_at: datetime
    last_saved_at: datetime
```

---

## Branching Tree

Each fork creates a new session row with `parent_session_id` pointing to the
source. This forms a tree rooted at the original session. Branches are
independent after the fork point — writes to one session never affect another.

```
session-A (root)
    ├── session-B (forked at episode 10)
    │       └── session-D (forked at episode 15)
    └── session-C (forked at episode 20)
```

The CLI `roleplay fork <session_id>` calls `persistence.fork()` and returns the
new `session_id`. The user can then run any branch independently. There is no
merge operation — branches are permanent divergences.

**Storage cost of a fork:** a full copy of all rows. For the reference scenario
(50 people, 50 years), this is ~50–60 MB per fork. Acceptable for a few
branches; the user is warned if the DB exceeds a configurable size limit
(`max_db_size_mb`, default: 500).

---

## Serialisation

All domain objects are serialised to JSON for storage. Deserialisation is the
reverse — no `pickle`, no `eval`.

| Object | Format |
|--------|--------|
| `Persona` | `{"description": str, "goals": [...], "traits": [...], ...}` |
| `StateValue` | JSON primitive; `None` → SQL NULL |
| `SimulationConfig` | `dataclasses.asdict()` output |
| `ToolCallResult` | `{"tool_name": str, "arguments": {...}, "result": str, "error": str|null}` |
| `MemoryKind` | String value of the enum (`"episodic"`, etc.) |
| `datetime` | ISO-8601 UTC string (`2026-01-01T12:00:00+00:00`) |

`state_changes` rows are written once and never updated — the current state is
always reconstructable by replaying changes in `episode_index` order.

---

## Resume Semantics

When the engine restarts after a crash:

1. `load_session(session_id)` is called.
2. The persistence layer replays `state_changes` to rebuild `Party.state`.
3. `load_history()` loads all completed episodes.
4. If the last episode has `ended_at = NULL` (it was open when the crash
   occurred), it is treated as a partial episode. The engine discards the
   partial episode and restarts from the last complete episode. The partial
   episode rows remain in the DB for forensic inspection but are excluded from
   `SimulationHistory`.

---

## Schema Migrations

Schema changes are applied via versioned migration scripts in
`src/roleplay/persistence/migrations/`:

```
migrations/
├── 001_initial.sql
├── 002_add_model_used_to_turns.sql
└── ...
```

The `SqlitePersistenceLayer` runs all pending migrations on first connection,
comparing the `schema_version` table against the scripts on disk. Migrations
are strictly additive (no column drops, no renames) during the design phase to
keep rollback trivial.

---

## Design Decisions & Rationale

1. **Append-only `state_changes` instead of a current-state snapshot.**
   Storing only the latest state would lose history (who changed what and when).
   An append-only log makes replay, debugging, and branching straightforward.
   The current state is always reconstructed by replaying changes — this is
   cheap at simulation scale (< 10 000 changes per party over 50 years).

2. **Fork is a full table copy, not a copy-on-write pointer.**
   Copy-on-write would save storage but enormously complicate queries (every
   read would need to walk an ancestry chain). At the reference scale (~60 MB
   per fork), full copy is safe. A size warning at 500 MB total keeps storage
   honest.

3. **No `pickle` or `eval` for serialisation.**
   Pickle is a security risk (arbitrary code execution on load) and brittle
   across Python versions. All serialisation uses JSON, which is portable,
   auditable, and human-readable.

4. **Schema migrations are strictly additive.**
   Non-additive changes (column drops, renames) require careful coordination
   with running processes. During the design and early implementation phase,
   all schema changes are additions only. If a column becomes obsolete, it is
   deprecated with a comment rather than dropped.

5. **Partial (crashed) episodes are kept but excluded from history.**
   Deleting partial episodes on resume would destroy forensic information.
   Keeping them but excluding them from `SimulationHistory` is the safe
   middle ground: the simulation resumes cleanly, and the partial data is
   available for inspection.

6. **`save_state()` is diff-based (only changed rows).**
   Writing all state_changes on every `save_state()` call would rapidly
   inflate the table. The persistence layer tracks which `state_history`
   entries have already been written (by `id`) and only inserts new ones.

---

## Error Handling

| Situation | Behaviour |
|-----------|-----------|
| `load_session()` for non-existent `session_id` | `SessionNotFoundError` |
| DB file missing on load | `PersistenceError` with path |
| Migration fails mid-flight | DB left in partial state; `PersistenceError` raised; manual recovery required |
| `fork()` fails mid-copy | DB transaction rolled back; source session untouched |
| `save_episode()` called with duplicate `(session_id, episode_index)` | Upsert — update existing row |
| JSON deserialisation error on `load_session()` | `CorruptedSessionError` with entry_id |
| DB size exceeds `max_db_size_mb` | Warning logged on each `checkpoint()`; no exception |

---

## Testing Strategy

**Unit tests (in-memory SQLite via `:memory:`):**

- `create_session` → `load_session` round-trip: all fields intact
- `save_episode` (open) → `save_episode` (closed): `ended_at` updated
- `load_history`: episodes in correct order; turns in correct order within episode
- `write_memory` → `retrieve_memories` round-trip
- `update_memory_access`: `last_accessed_episode` and `access_count` updated
- `checkpoint()` atomically persists all in-progress changes
- `fork()`: new session has all rows; modifications to fork do not affect source
- Branching tree: fork of a fork has correct `parent_session_id`
- Resume: partial open episode excluded from `load_history` result
- `export_json()` produces valid JSON with all expected keys
- `list_sessions()`: sorted by `last_saved_at` descending
- `delete_session()`: all rows across all tables removed for that `session_id`
- Schema migration: version 1 applied on fresh DB; already-applied migration skipped

**Integration tests (`@pytest.mark.integration`):**

- Full 10-episode simulation save and resume from on-disk SQLite file
- Fork → run each branch → verify independent histories

**Edge cases:**

- Session with zero episodes
- Fork at episode 0 (before any episodes)
- `export_json()` on a session with no memory entries
- `delete_memory()` for non-existent `entry_id` (no-op)
- Two concurrent `save_state()` calls (SQLite WAL mode handles this)

**Coverage target:** ≥ 80% for `persistence/` on key paths (session CRUD,
episode save/load, fork, migration runner).

---

## Open Questions

None blocking.

Future: remote storage backend (S3, GCS) implementing `PersistenceLayer` for
cloud deployments. The protocol is the seam — the rest of the system is
unaffected.
