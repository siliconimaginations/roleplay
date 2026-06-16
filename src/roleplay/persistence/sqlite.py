"""SqlitePersistenceLayer — the concrete SQLite-backed persistence implementation."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

import aiosqlite

from roleplay.persistence._serialization import (
    _dt_to_str,
    _str_to_dt,
    decode_config,
    decode_memory_entry,
    decode_persona,
    decode_state_value,
    decode_turn,
    encode_config,
    encode_episode_row,
    encode_memory_row,
    encode_persona,
    encode_state_change_row,
    encode_turn_row,
)
from roleplay.persistence._serialization import (
    episode_id as make_episode_id,
)
from roleplay.persistence._serialization import (
    turn_id as make_turn_id,
)
from roleplay.persistence.base import (
    CorruptedSessionError,
    PersistenceError,
    SessionNotFoundError,
    SessionSummary,
)

if TYPE_CHECKING:
    from roleplay.core.episode import Episode, SimulationHistory
    from roleplay.core.party import Party
    from roleplay.core.simulation_state import SimulationState
    from roleplay.memory.store import MemoryEntry

logger = logging.getLogger(__name__)

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_MAX_DB_SIZE_MB_DEFAULT = 500


class SqlitePersistenceLayer:
    """SQLite-backed implementation of PersistenceLayer.

    Usage::

        layer = SqlitePersistenceLayer(db_path)
        await layer.open()          # connects + runs migrations
        ...
        await layer.close()

    Or use as an async context manager::

        async with SqlitePersistenceLayer(db_path) as layer:
            ...
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        max_db_size_mb: int = _MAX_DB_SIZE_MB_DEFAULT,
    ) -> None:
        self._db_path = Path(db_path)
        self._max_db_size_mb = max_db_size_mb
        self._conn: aiosqlite.Connection | None = None
        # Track which state_change IDs we've already written per session
        # to implement diff-based save_state().
        self._written_change_ids: dict[str, set[str]] = {}

    async def open(self) -> None:
        """Open the database connection and apply pending migrations."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(str(self._db_path))
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode = WAL")
        await self._conn.execute("PRAGMA foreign_keys = ON")
        await self._run_migrations()

    async def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def __aenter__(self) -> SqlitePersistenceLayer:
        await self.open()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _db(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise PersistenceError("Database connection is not open. Call open() first.")
        return self._conn

    async def _run_migrations(self) -> None:
        """Apply all pending SQL migration files in filename order."""
        db = self._db()
        # Ensure schema_version table exists before querying it.
        await db.execute(
            "CREATE TABLE IF NOT EXISTS schema_version "
            "(version INTEGER NOT NULL, applied_at TEXT NOT NULL)"
        )
        await db.commit()

        row = await (await db.execute("SELECT MAX(version) FROM schema_version")).fetchone()
        current_version: int = row[0] if row and row[0] is not None else 0

        sql_files = sorted(_MIGRATIONS_DIR.glob("*.sql"))
        for sql_file in sql_files:
            version = int(sql_file.stem.split("_")[0])
            if version <= current_version:
                continue
            logger.info("Applying migration %s", sql_file.name)
            sql = sql_file.read_text(encoding="utf-8")
            await db.executescript(sql)
            await db.commit()

    def _check_size(self) -> None:
        """Log a warning if the DB file exceeds max_db_size_mb."""
        if not self._db_path.exists():
            return
        size_mb = self._db_path.stat().st_size / (1024 * 1024)
        if size_mb > self._max_db_size_mb:
            logger.warning(
                "Database size %.1f MB exceeds limit %d MB (%s)",
                size_mb,
                self._max_db_size_mb,
                self._db_path,
            )

    # ── Session lifecycle ────────────────────────────────────────────────────

    async def create_session(self, state: SimulationState) -> None:
        """Write the session row and all party rows. Idempotent."""
        db = self._db()
        now = _dt_to_str(__import__("datetime").datetime.now(__import__("datetime").UTC))
        await db.execute(
            """
            INSERT OR IGNORE INTO sessions
              (session_id, parent_session_id, forked_at_episode,
               config_json, started_at, last_saved_at, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                state.config.session_id,
                None,
                None,
                encode_config(state.config),
                now,
                now,
                "running",
            ),
        )
        # Upsert all parties
        all_parties = [*state.parties.values(), state.environment]
        for party in all_parties:
            await db.execute(
                """
                INSERT OR IGNORE INTO parties
                  (party_id, session_id, name, kind, persona_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    party.id,
                    state.config.session_id,
                    party.name,
                    party.kind.value,
                    encode_persona(party.persona),
                ),
            )
        await db.commit()
        self._written_change_ids.setdefault(state.config.session_id, set())

    async def save_state(self, state: SimulationState) -> None:
        """Persist new state_changes rows and update last_saved_at."""
        db = self._db()
        session_id = state.config.session_id
        written = self._written_change_ids.setdefault(session_id, set())

        all_parties = [*state.parties.values(), state.environment]
        rows: list[dict[str, object]] = []
        for party in all_parties:
            for change in party.state_history:
                # Use (party_id, key, episode_index) as a logical identity —
                # StateChange is a NamedTuple with no UUID, so we derive one.
                change_key = f"{party.id}:{change.key}:{change.episode_index}"
                if change_key not in written:
                    rows.append(encode_state_change_row(session_id, party.id, change))
                    written.add(change_key)

        if rows:
            await db.executemany(
                """
                INSERT OR IGNORE INTO state_changes
                  (id, party_id, session_id, key,
                   old_value_json, new_value_json, episode_index, reason)
                VALUES (:id, :party_id, :session_id, :key,
                        :old_value_json, :new_value_json, :episode_index, :reason)
                """,
                rows,
            )

        now = _dt_to_str(__import__("datetime").datetime.now(__import__("datetime").UTC))
        await db.execute(
            "UPDATE sessions SET last_saved_at = ? WHERE session_id = ?",
            (now, session_id),
        )
        await db.commit()

    async def load_session(self, session_id: str) -> SimulationState:
        """Reconstruct a SimulationState from the DB."""
        db = self._db()

        # Load session row
        row = await (
            await db.execute("SELECT config_json FROM sessions WHERE session_id = ?", (session_id,))
        ).fetchone()
        if row is None:
            raise SessionNotFoundError(session_id)

        try:
            config = decode_config(row["config_json"])
        except Exception as exc:
            raise CorruptedSessionError(
                f"Could not decode config for session {session_id!r}: {exc}"
            ) from exc

        # Load parties
        party_rows = await (
            await db.execute(
                "SELECT party_id, name, kind, persona_json FROM parties WHERE session_id = ?",
                (session_id,),
            )
        ).fetchall()

        from roleplay.core.party import Party, PartyKind, make_environment

        parties: dict[str, Party] = {}
        environment: Party | None = None
        for pr in party_rows:
            persona = decode_persona(pr["persona_json"])
            kind = PartyKind(pr["kind"])
            party = Party(
                id=pr["party_id"],
                name=pr["name"],
                kind=kind,
                persona=persona,
            )
            if kind == PartyKind.ENVIRONMENT:
                environment = party
            else:
                parties[pr["party_id"]] = party

        if environment is None:
            # Create a minimal environment if none was stored
            environment = make_environment(f"{session_id}:env", "Environment", "")

        # Replay state_changes to rebuild party state
        change_rows = await (
            await db.execute(
                """
                SELECT party_id, key, new_value_json, episode_index
                FROM state_changes
                WHERE session_id = ?
                ORDER BY episode_index, rowid
                """,
                (session_id,),
            )
        ).fetchall()

        # Rebuild state for each party
        from roleplay.core.party import StateValue as _StateValue

        party_states: dict[str, dict[str, _StateValue]] = {}
        for cr in change_rows:
            pid = cr["party_id"]
            if pid not in party_states:
                party_states[pid] = {}
            party_states[pid][cr["key"]] = decode_state_value(cr["new_value_json"])

        # Apply states back
        from dataclasses import replace as dc_replace

        for pid, pstate in party_states.items():
            if pid in parties:
                parties[pid] = dc_replace(parties[pid], state=pstate)
            elif environment and pid == environment.id:
                environment = dc_replace(environment, state=pstate)

        # Load history (completed episodes only)
        history = await self.load_history(session_id)

        from roleplay.core.episode import NoopClock, RoundRobinScheduler
        from roleplay.core.simulation_state import SimulationState

        return SimulationState(
            config=config,
            parties=parties,
            environment=environment,
            history=history,
            scheduler=RoundRobinScheduler(),
            clock=NoopClock(),
        )

    async def list_sessions(self) -> list[SessionSummary]:
        """Return all sessions sorted by last_saved_at descending."""
        db = self._db()
        rows = await (
            await db.execute(
                """
                SELECT s.session_id, s.parent_session_id, s.forked_at_episode,
                       s.status, s.started_at, s.last_saved_at,
                       COUNT(DISTINCT e.episode_id) AS episode_count,
                       COUNT(DISTINCT p.party_id) AS party_count
                FROM sessions s
                LEFT JOIN episodes e
                       ON e.session_id = s.session_id AND e.ended_at IS NOT NULL
                LEFT JOIN parties p ON p.session_id = s.session_id
                GROUP BY s.session_id
                ORDER BY s.last_saved_at DESC
                """
            )
        ).fetchall()
        return [
            SessionSummary(
                session_id=r["session_id"],
                parent_session_id=r["parent_session_id"],
                forked_at_episode=r["forked_at_episode"],
                episode_count=r["episode_count"],
                party_count=r["party_count"],
                status=r["status"],
                started_at=_str_to_dt(r["started_at"]),
                last_saved_at=_str_to_dt(r["last_saved_at"]),
            )
            for r in rows
        ]

    async def delete_session(self, session_id: str) -> None:
        """Hard-delete all rows for this session_id across all tables."""
        db = self._db()
        async with db.execute("BEGIN"):
            pass
        for table in (
            "turns",
            "memory_entries",
            "state_changes",
            "episodes",
            "parties",
            "sessions",
        ):
            await db.execute(
                f"DELETE FROM {table} WHERE session_id = ?",
                (session_id,),
            )
        await db.commit()
        self._written_change_ids.pop(session_id, None)

    # ── Episode and turn persistence ─────────────────────────────────────────

    async def save_episode(self, session_id: str, episode: Episode) -> None:
        """Upsert episode row + all turn rows."""
        db = self._db()
        ep_row = encode_episode_row(session_id, episode)
        ep_id = str(ep_row["episode_id"])

        await db.execute(
            """
            INSERT INTO episodes
              (episode_id, session_id, episode_index, simulated_time_start,
               simulated_time_end, started_at, ended_at)
            VALUES (:episode_id, :session_id, :episode_index, :simulated_time_start,
                    :simulated_time_end, :started_at, :ended_at)
            ON CONFLICT(episode_id) DO UPDATE SET
              simulated_time_end = excluded.simulated_time_end,
              ended_at = excluded.ended_at
            """,
            ep_row,
        )

        for turn in episode.turns:
            tr = encode_turn_row(session_id, ep_id, turn)
            await db.execute(
                """
                INSERT OR IGNORE INTO turns
                  (turn_id, episode_id, session_id, party_id, turn_index,
                   output, state_proposals_json, tool_calls_json,
                   prompt_tokens, completion_tokens, model_used, timestamp)
                VALUES (:turn_id, :episode_id, :session_id, :party_id, :turn_index,
                        :output, :state_proposals_json, :tool_calls_json,
                        :prompt_tokens, :completion_tokens, :model_used, :timestamp)
                """,
                tr,
            )

        await db.commit()

    async def load_history(
        self, session_id: str, max_episodes: int | None = None
    ) -> SimulationHistory:
        """Load completed episodes + their turns; exclude partial episodes."""
        db = self._db()

        ep_query = """
            SELECT episode_id, episode_index, simulated_time_start,
                   simulated_time_end, started_at, ended_at
            FROM episodes
            WHERE session_id = ? AND ended_at IS NOT NULL
            ORDER BY episode_index
        """
        params: tuple[object, ...] = (session_id,)
        if max_episodes is not None:
            ep_query = f"""
                SELECT * FROM ({ep_query}) sub
                ORDER BY episode_index DESC
                LIMIT ?
            """
            params = (session_id, max_episodes)

        ep_rows = await (await db.execute(ep_query, params)).fetchall()

        from roleplay.core.episode import Episode, SimulationHistory

        episodes = []
        for ep_row in ep_rows:
            turn_rows = await (
                await db.execute(
                    """
                    SELECT party_id, turn_index, output, state_proposals_json,
                           tool_calls_json, prompt_tokens, completion_tokens, timestamp
                    FROM turns
                    WHERE episode_id = ?
                    ORDER BY turn_index
                    """,
                    (ep_row["episode_id"],),
                )
            ).fetchall()

            turns = [decode_turn(dict(tr)) for tr in turn_rows]
            ep = Episode(
                index=ep_row["episode_index"],
                turns=turns,
                simulated_time_start=ep_row["simulated_time_start"],
                simulated_time_end=ep_row["simulated_time_end"],
                started_at=_str_to_dt(ep_row["started_at"]),
                ended_at=_str_to_dt(ep_row["ended_at"]),
            )
            episodes.append(ep)

        # If we loaded in DESC order (max_episodes path), re-sort ascending
        if max_episodes is not None:
            episodes.sort(key=lambda e: e.index)

        return SimulationHistory(episodes=episodes)

    # ── Memory ───────────────────────────────────────────────────────────────

    async def write_memory(self, session_id: str, entry: MemoryEntry) -> None:
        db = self._db()
        row = encode_memory_row(session_id, entry)
        await db.execute(
            """
            INSERT OR REPLACE INTO memory_entries
              (entry_id, party_id, session_id, kind, content, episode_index,
               importance, last_accessed_episode, access_count,
               source_entry_ids_json, created_at, forgotten)
            VALUES (:entry_id, :party_id, :session_id, :kind, :content,
                    :episode_index, :importance, :last_accessed_episode,
                    :access_count, :source_entry_ids_json, :created_at, :forgotten)
            """,
            row,
        )
        await db.commit()

    async def write_memories(self, session_id: str, entries: list[MemoryEntry]) -> None:
        if not entries:
            return
        db = self._db()
        rows = [encode_memory_row(session_id, e) for e in entries]
        await db.executemany(
            """
            INSERT OR REPLACE INTO memory_entries
              (entry_id, party_id, session_id, kind, content, episode_index,
               importance, last_accessed_episode, access_count,
               source_entry_ids_json, created_at, forgotten)
            VALUES (:entry_id, :party_id, :session_id, :kind, :content,
                    :episode_index, :importance, :last_accessed_episode,
                    :access_count, :source_entry_ids_json, :created_at, :forgotten)
            """,
            rows,
        )
        await db.commit()

    async def delete_memory(self, session_id: str, entry_id: str) -> None:
        db = self._db()
        await db.execute(
            "DELETE FROM memory_entries WHERE session_id = ? AND entry_id = ?",
            (session_id, entry_id),
        )
        await db.commit()

    async def delete_memories(self, session_id: str, entry_ids: list[str]) -> None:
        if not entry_ids:
            return
        db = self._db()
        placeholders = ",".join("?" * len(entry_ids))
        await db.execute(
            f"DELETE FROM memory_entries WHERE session_id = ? AND entry_id IN ({placeholders})",
            (session_id, *entry_ids),
        )
        await db.commit()

    async def retrieve_memories(
        self,
        session_id: str,
        party_id: str,
        *,
        kinds: frozenset[str] | None = None,
    ) -> list[MemoryEntry]:
        db = self._db()
        if kinds is not None:
            placeholders = ",".join("?" * len(kinds))
            rows = await (
                await db.execute(
                    f"""
                    SELECT * FROM memory_entries
                    WHERE session_id = ? AND party_id = ? AND forgotten = 0
                      AND kind IN ({placeholders})
                    ORDER BY episode_index
                    """,
                    (session_id, party_id, *kinds),
                )
            ).fetchall()
        else:
            rows = await (
                await db.execute(
                    """
                    SELECT * FROM memory_entries
                    WHERE session_id = ? AND party_id = ? AND forgotten = 0
                    ORDER BY episode_index
                    """,
                    (session_id, party_id),
                )
            ).fetchall()
        return [decode_memory_entry(dict(r)) for r in rows]

    async def update_memory_access(
        self, session_id: str, entry_id: str, episode_index: int
    ) -> None:
        db = self._db()
        await db.execute(
            """
            UPDATE memory_entries
            SET last_accessed_episode = ?,
                access_count = access_count + 1
            WHERE session_id = ? AND entry_id = ?
            """,
            (episode_index, session_id, entry_id),
        )
        await db.commit()

    async def memory_entry_count(self, session_id: str, party_id: str) -> int:
        db = self._db()
        row = await (
            await db.execute(
                "SELECT COUNT(*) FROM memory_entries "
                "WHERE session_id = ? AND party_id = ? AND forgotten = 0",
                (session_id, party_id),
            )
        ).fetchone()
        return int(row[0]) if row else 0

    async def memory_total_content_length(self, session_id: str, party_id: str) -> int:
        db = self._db()
        row = await (
            await db.execute(
                "SELECT SUM(LENGTH(content)) FROM memory_entries "
                "WHERE session_id = ? AND party_id = ? AND forgotten = 0",
                (session_id, party_id),
            )
        ).fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    # ── Checkpoint and fork ──────────────────────────────────────────────────

    async def checkpoint(self, state: SimulationState) -> str:
        """Atomically persist all current state."""
        self._check_size()
        await self.save_state(state)
        return state.config.session_id

    async def fork(self, session_id: str, new_session_id: str) -> SimulationState:
        """Deep-copy the session under new_session_id."""
        db = self._db()

        # Load the current episode count for forked_at_episode
        row = await (
            await db.execute(
                "SELECT COUNT(*) FROM episodes WHERE session_id = ? AND ended_at IS NOT NULL",
                (session_id,),
            )
        ).fetchone()
        forked_at = int(row[0]) if row else 0

        # Atomically copy all rows
        await db.execute("BEGIN")
        try:
            # Copy session row
            src_row = await (
                await db.execute(
                    "SELECT config_json, started_at, status FROM sessions WHERE session_id = ?",
                    (session_id,),
                )
            ).fetchone()
            if src_row is None:
                raise SessionNotFoundError(session_id)

            import datetime as dt

            now = _dt_to_str(dt.datetime.now(dt.UTC))
            await db.execute(
                """
                INSERT INTO sessions
                  (session_id, parent_session_id, forked_at_episode,
                   config_json, started_at, last_saved_at, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_session_id,
                    session_id,
                    forked_at,
                    # Patch config_json to use new session_id
                    _patch_session_id_in_config(src_row["config_json"], new_session_id),
                    src_row["started_at"],
                    now,
                    src_row["status"],
                ),
            )

            # Copy parties
            party_rows = await (
                await db.execute(
                    "SELECT party_id, name, kind, persona_json FROM parties WHERE session_id = ?",
                    (session_id,),
                )
            ).fetchall()
            for pr in party_rows:
                await db.execute(
                    "INSERT INTO parties (party_id, session_id, name, kind, persona_json) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (pr["party_id"], new_session_id, pr["name"], pr["kind"], pr["persona_json"]),
                )

            # Copy state_changes
            sc_rows = await (
                await db.execute(
                    "SELECT party_id, key, old_value_json, new_value_json, episode_index, reason "
                    "FROM state_changes WHERE session_id = ?",
                    (session_id,),
                )
            ).fetchall()
            from uuid import uuid4 as _uuid4

            for sc in sc_rows:
                await db.execute(
                    "INSERT INTO state_changes "
                    "(id, party_id, session_id, key, old_value_json, new_value_json, "
                    "episode_index, reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(_uuid4()),
                        sc["party_id"],
                        new_session_id,
                        sc["key"],
                        sc["old_value_json"],
                        sc["new_value_json"],
                        sc["episode_index"],
                        sc["reason"],
                    ),
                )

            # Copy episodes + turns
            ep_rows = await (
                await db.execute(
                    "SELECT episode_id, episode_index, simulated_time_start, "
                    "simulated_time_end, started_at, ended_at FROM episodes WHERE session_id = ?",
                    (session_id,),
                )
            ).fetchall()
            for ep in ep_rows:
                new_ep_id = make_episode_id(new_session_id, ep["episode_index"])
                await db.execute(
                    "INSERT INTO episodes "
                    "(episode_id, session_id, episode_index, simulated_time_start, "
                    "simulated_time_end, started_at, ended_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        new_ep_id,
                        new_session_id,
                        ep["episode_index"],
                        ep["simulated_time_start"],
                        ep["simulated_time_end"],
                        ep["started_at"],
                        ep["ended_at"],
                    ),
                )
                # Copy turns for this episode
                t_rows = await (
                    await db.execute(
                        "SELECT party_id, turn_index, output, state_proposals_json, "
                        "tool_calls_json, prompt_tokens, completion_tokens, "
                        "model_used, timestamp FROM turns WHERE episode_id = ?",
                        (ep["episode_id"],),
                    )
                ).fetchall()
                for tr in t_rows:
                    new_turn_id = make_turn_id(
                        new_session_id, ep["episode_index"], tr["turn_index"]
                    )
                    await db.execute(
                        "INSERT INTO turns (turn_id, episode_id, session_id, party_id, "
                        "turn_index, output, state_proposals_json, tool_calls_json, "
                        "prompt_tokens, completion_tokens, model_used, timestamp) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            new_turn_id,
                            new_ep_id,
                            new_session_id,
                            tr["party_id"],
                            tr["turn_index"],
                            tr["output"],
                            tr["state_proposals_json"],
                            tr["tool_calls_json"],
                            tr["prompt_tokens"],
                            tr["completion_tokens"],
                            tr["model_used"],
                            tr["timestamp"],
                        ),
                    )

            # Copy memory entries (new entry_id per copy to satisfy PK uniqueness)
            mem_rows = await (
                await db.execute(
                    "SELECT * FROM memory_entries WHERE session_id = ?",
                    (session_id,),
                )
            ).fetchall()
            for mr in mem_rows:
                await db.execute(
                    "INSERT INTO memory_entries "
                    "(entry_id, party_id, session_id, kind, content, episode_index, "
                    "importance, last_accessed_episode, access_count, "
                    "source_entry_ids_json, created_at, forgotten) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(_uuid4()),  # new UUID — entry_id PK is globally unique
                        mr["party_id"],
                        new_session_id,
                        mr["kind"],
                        mr["content"],
                        mr["episode_index"],
                        mr["importance"],
                        mr["last_accessed_episode"],
                        mr["access_count"],
                        mr["source_entry_ids_json"],
                        mr["created_at"],
                        mr["forgotten"],
                    ),
                )

            await db.commit()
        except Exception:
            await db.rollback()
            raise

        return await self.load_session(new_session_id)

    # ── Export ───────────────────────────────────────────────────────────────

    async def export_json(self, session_id: str) -> dict[str, object]:
        """Return the full session as a JSON-serialisable dict."""
        db = self._db()

        sess = await (
            await db.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,))
        ).fetchone()
        if sess is None:
            raise SessionNotFoundError(session_id)

        parties = await (
            await db.execute("SELECT * FROM parties WHERE session_id = ?", (session_id,))
        ).fetchall()
        episodes_raw = await (
            await db.execute(
                "SELECT * FROM episodes WHERE session_id = ? ORDER BY episode_index",
                (session_id,),
            )
        ).fetchall()
        memories = await (
            await db.execute("SELECT * FROM memory_entries WHERE session_id = ?", (session_id,))
        ).fetchall()

        episodes_out = []
        for ep in episodes_raw:
            turns = await (
                await db.execute(
                    "SELECT * FROM turns WHERE episode_id = ? ORDER BY turn_index",
                    (ep["episode_id"],),
                )
            ).fetchall()
            episodes_out.append(
                {
                    **dict(ep),
                    "turns": [dict(t) for t in turns],
                }
            )

        return {
            "session": dict(sess),
            "parties": [dict(p) for p in parties],
            "episodes": episodes_out,
            "memories": [dict(m) for m in memories],
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _patch_session_id_in_config(config_json: str, new_session_id: str) -> str:
    """Replace session_id in a serialised SimulationConfig JSON blob."""
    d: dict[str, object] = json.loads(config_json)
    d["session_id"] = new_session_id
    return json.dumps(d)
