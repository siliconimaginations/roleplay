"""Unit tests for SqlitePersistenceLayer using in-memory SQLite (:memory:)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from roleplay.core.episode import Episode, SimulationHistory, Turn
from roleplay.core.party import PartyKind, make_environment, make_person
from roleplay.core.simulation_state import SimulationConfig, SimulationState
from roleplay.memory.store import MemoryEntry, MemoryKind
from roleplay.persistence import (
    SessionNotFoundError,
    SqlitePersistenceLayer,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_config(session_id: str = "test-session") -> SimulationConfig:
    return SimulationConfig(session_id=session_id)


def _make_state(session_id: str = "test-session") -> SimulationState:
    from roleplay.core.episode import NoopClock, RoundRobinScheduler

    alice = make_person("alice", "Alice", "Alice is a negotiator.")
    bob = make_person("bob", "Bob", "Bob is a merchant.")
    env = make_environment("env-1", "Environment", "A quiet room.")
    return SimulationState(
        config=_make_config(session_id),
        parties={"alice": alice, "bob": bob},
        environment=env,
        history=SimulationHistory(),
        scheduler=RoundRobinScheduler(),
        clock=NoopClock(),
    )


def _make_turn(party_id: str, index: int, output: str = "hello") -> Turn:
    return Turn(party_id=party_id, index=index, output=output)


def _make_episode(index: int, closed: bool = True) -> Episode:
    ep = Episode(index=index, turns=[], simulated_time_start="Day 1")
    ep.add_turn(_make_turn("alice", 0, "Hi!"))
    ep.add_turn(_make_turn("bob", 1, "Hello!"))
    if closed:
        ep.close("Day 2")
    return ep


@pytest.fixture
async def layer(tmp_path: Path) -> SqlitePersistenceLayer:
    from pathlib import Path as _Path

    db = _Path(":memory:")
    layer = SqlitePersistenceLayer(db)
    await layer.open()
    yield layer
    await layer.close()


# ---------------------------------------------------------------------------
# Migration tests
# ---------------------------------------------------------------------------


class TestMigrations:
    async def test_schema_created_on_open(self, layer: SqlitePersistenceLayer) -> None:
        db = layer._db()
        tables = await (
            await db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        ).fetchall()
        names = {r[0] for r in tables}
        expected = {"sessions", "parties", "state_changes", "episodes", "turns", "memory_entries"}
        assert expected.issubset(names)

    async def test_schema_version_set_to_1(self, layer: SqlitePersistenceLayer) -> None:
        db = layer._db()
        row = await (await db.execute("SELECT MAX(version) FROM schema_version")).fetchone()
        assert row[0] == 1

    async def test_migration_not_reapplied_on_second_open(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        l1 = SqlitePersistenceLayer(db_path)
        await l1.open()
        await l1.close()

        l2 = SqlitePersistenceLayer(db_path)
        await l2.open()
        row = await (await l2._db().execute("SELECT COUNT(*) FROM schema_version")).fetchone()
        assert row[0] == 1  # still only one row
        await l2.close()


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------


class TestSessionLifecycle:
    async def test_create_then_list(self, layer: SqlitePersistenceLayer) -> None:
        state = _make_state()
        await layer.create_session(state)
        sessions = await layer.list_sessions()
        assert len(sessions) == 1
        assert sessions[0].session_id == "test-session"
        assert sessions[0].status == "running"

    async def test_create_is_idempotent(self, layer: SqlitePersistenceLayer) -> None:
        state = _make_state()
        await layer.create_session(state)
        await layer.create_session(state)  # should not raise
        sessions = await layer.list_sessions()
        assert len(sessions) == 1

    async def test_load_session_round_trip(self, layer: SqlitePersistenceLayer) -> None:
        state = _make_state()
        await layer.create_session(state)
        loaded = await layer.load_session("test-session")
        assert loaded.config.session_id == "test-session"
        assert set(loaded.parties.keys()) == {"alice", "bob"}
        assert loaded.environment.kind == PartyKind.ENVIRONMENT

    async def test_load_nonexistent_raises(self, layer: SqlitePersistenceLayer) -> None:
        with pytest.raises(SessionNotFoundError):
            await layer.load_session("no-such-session")

    async def test_delete_session_removes_all_rows(self, layer: SqlitePersistenceLayer) -> None:
        state = _make_state()
        await layer.create_session(state)
        ep = _make_episode(0)
        await layer.save_episode("test-session", ep)
        await layer.delete_session("test-session")
        sessions = await layer.list_sessions()
        assert sessions == []
        db = layer._db()
        for table in ("parties", "episodes", "turns"):
            row = await (
                await db.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE session_id = ?", ("test-session",)
                )
            ).fetchone()
            assert row[0] == 0, f"Expected {table} to be empty after delete"

    async def test_list_sessions_sorted_by_last_saved(self, layer: SqlitePersistenceLayer) -> None:
        await layer.create_session(_make_state("session-a"))
        await layer.create_session(_make_state("session-b"))
        # Force a save_state on b to bump last_saved_at
        state_b = _make_state("session-b")
        state_b.parties["alice"].apply_state_update({"mood": "happy"}, episode_index=0)
        await layer.save_state(state_b)
        sessions = await layer.list_sessions()
        # session-b was updated most recently
        assert sessions[0].session_id == "session-b"


# ---------------------------------------------------------------------------
# State changes
# ---------------------------------------------------------------------------


class TestStateChanges:
    async def test_save_state_persists_changes(self, layer: SqlitePersistenceLayer) -> None:
        state = _make_state()
        await layer.create_session(state)
        state.parties["alice"].apply_state_update({"mood": "happy"}, episode_index=0)
        await layer.save_state(state)

        loaded = await layer.load_session("test-session")
        assert loaded.parties["alice"].state.get("mood") == "happy"

    async def test_save_state_is_diff_only(self, layer: SqlitePersistenceLayer) -> None:
        state = _make_state()
        await layer.create_session(state)
        state.parties["alice"].apply_state_update({"mood": "happy"}, episode_index=0)
        await layer.save_state(state)
        await layer.save_state(state)  # second call — no new changes

        db = layer._db()
        row = await (
            await db.execute(
                "SELECT COUNT(*) FROM state_changes WHERE session_id = ?", ("test-session",)
            )
        ).fetchone()
        assert row[0] == 1  # still only one row

    async def test_environment_state_round_trips(self, layer: SqlitePersistenceLayer) -> None:
        state = _make_state()
        await layer.create_session(state)
        state.environment.apply_state_update({"weather": "sunny"}, episode_index=1)
        await layer.save_state(state)

        loaded = await layer.load_session("test-session")
        assert loaded.environment.state.get("weather") == "sunny"


# ---------------------------------------------------------------------------
# Episode and turn persistence
# ---------------------------------------------------------------------------


class TestEpisodePersistence:
    async def test_save_and_load_episode(self, layer: SqlitePersistenceLayer) -> None:
        state = _make_state()
        await layer.create_session(state)
        ep = _make_episode(0)
        await layer.save_episode("test-session", ep)

        history = await layer.load_history("test-session")
        assert len(history.episodes) == 1
        assert history.episodes[0].index == 0
        assert len(history.episodes[0].turns) == 2

    async def test_save_episode_open_then_close(self, layer: SqlitePersistenceLayer) -> None:
        state = _make_state()
        await layer.create_session(state)
        ep = _make_episode(0, closed=False)  # open episode
        await layer.save_episode("test-session", ep)

        # Open episodes excluded from load_history
        history = await layer.load_history("test-session")
        assert len(history.episodes) == 0

        # Close it
        ep.close("Day 2")
        await layer.save_episode("test-session", ep)

        history = await layer.load_history("test-session")
        assert len(history.episodes) == 1
        assert history.episodes[0].simulated_time_end == "Day 2"

    async def test_load_history_max_episodes(self, layer: SqlitePersistenceLayer) -> None:
        state = _make_state()
        await layer.create_session(state)
        for i in range(5):
            await layer.save_episode("test-session", _make_episode(i))

        history = await layer.load_history("test-session", max_episodes=3)
        assert len(history.episodes) == 3
        # Should be the most recent 3, in ascending order
        assert [ep.index for ep in history.episodes] == [2, 3, 4]

    async def test_turn_fields_round_trip(self, layer: SqlitePersistenceLayer) -> None:
        state = _make_state()
        await layer.create_session(state)
        ep = Episode(index=0, turns=[], simulated_time_start="T0")
        turn = Turn(
            party_id="alice",
            index=0,
            output="My offer is 100 gold.",
            state_update_proposals={"deal_amount": 100},
            prompt_tokens=50,
            completion_tokens=20,
        )
        ep.add_turn(turn)
        ep.close("T1")
        await layer.save_episode("test-session", ep)

        history = await layer.load_history("test-session")
        loaded_turn = history.episodes[0].turns[0]
        assert loaded_turn.output == "My offer is 100 gold."
        assert loaded_turn.state_update_proposals == {"deal_amount": 100}
        assert loaded_turn.prompt_tokens == 50
        assert loaded_turn.completion_tokens == 20

    async def test_partial_episode_excluded_from_history(
        self, layer: SqlitePersistenceLayer
    ) -> None:
        """Open episode (ended_at=None) is excluded — resume semantics."""
        state = _make_state()
        await layer.create_session(state)
        closed_ep = _make_episode(0)
        open_ep = _make_episode(1, closed=False)
        await layer.save_episode("test-session", closed_ep)
        await layer.save_episode("test-session", open_ep)

        history = await layer.load_history("test-session")
        assert len(history.episodes) == 1
        assert history.episodes[0].index == 0


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------


class TestMemory:
    def _entry(
        self, party_id: str = "alice", episode_index: int = 0, content: str = "Test memory"
    ) -> MemoryEntry:
        return MemoryEntry(
            party_id=party_id,
            kind=MemoryKind.EPISODIC,
            content=content,
            episode_index=episode_index,
        )

    async def test_write_then_retrieve(self, layer: SqlitePersistenceLayer) -> None:
        state = _make_state()
        await layer.create_session(state)
        entry = self._entry()
        await layer.write_memory("test-session", entry)

        results = await layer.retrieve_memories("test-session", "alice")
        assert len(results) == 1
        assert results[0].content == "Test memory"

    async def test_write_many(self, layer: SqlitePersistenceLayer) -> None:
        state = _make_state()
        await layer.create_session(state)
        entries = [self._entry(content=f"Memory {i}") for i in range(5)]
        await layer.write_memories("test-session", entries)

        results = await layer.retrieve_memories("test-session", "alice")
        assert len(results) == 5

    async def test_delete_memory(self, layer: SqlitePersistenceLayer) -> None:
        state = _make_state()
        await layer.create_session(state)
        entry = self._entry()
        await layer.write_memory("test-session", entry)
        await layer.delete_memory("test-session", entry.id)

        results = await layer.retrieve_memories("test-session", "alice")
        assert results == []

    async def test_delete_nonexistent_is_noop(self, layer: SqlitePersistenceLayer) -> None:
        state = _make_state()
        await layer.create_session(state)
        await layer.delete_memory("test-session", "no-such-id")  # must not raise

    async def test_delete_many(self, layer: SqlitePersistenceLayer) -> None:
        state = _make_state()
        await layer.create_session(state)
        entries = [self._entry(content=f"M{i}") for i in range(3)]
        await layer.write_memories("test-session", entries)
        ids = [e.id for e in entries[:2]]
        await layer.delete_memories("test-session", ids)

        results = await layer.retrieve_memories("test-session", "alice")
        assert len(results) == 1

    async def test_retrieve_by_kind(self, layer: SqlitePersistenceLayer) -> None:
        state = _make_state()
        await layer.create_session(state)
        episodic = MemoryEntry(
            party_id="alice", kind=MemoryKind.EPISODIC, content="ep", episode_index=0
        )
        semantic = MemoryEntry(
            party_id="alice", kind=MemoryKind.SEMANTIC, content="sem", episode_index=0
        )
        await layer.write_memories("test-session", [episodic, semantic])

        results = await layer.retrieve_memories(
            "test-session", "alice", kinds=frozenset({"episodic"})
        )
        assert len(results) == 1
        assert results[0].kind == MemoryKind.EPISODIC

    async def test_update_memory_access(self, layer: SqlitePersistenceLayer) -> None:
        state = _make_state()
        await layer.create_session(state)
        entry = self._entry()
        await layer.write_memory("test-session", entry)
        await layer.update_memory_access("test-session", entry.id, episode_index=5)

        results = await layer.retrieve_memories("test-session", "alice")
        assert results[0].last_accessed_episode == 5
        assert results[0].access_count == 1

    async def test_memory_entry_count(self, layer: SqlitePersistenceLayer) -> None:
        state = _make_state()
        await layer.create_session(state)
        entries = [self._entry(content=f"M{i}") for i in range(4)]
        await layer.write_memories("test-session", entries)
        count = await layer.memory_entry_count("test-session", "alice")
        assert count == 4

    async def test_memory_total_content_length(self, layer: SqlitePersistenceLayer) -> None:
        state = _make_state()
        await layer.create_session(state)
        entry = self._entry(content="hello")
        await layer.write_memory("test-session", entry)
        length = await layer.memory_total_content_length("test-session", "alice")
        assert length == 5

    async def test_memory_entry_count_zero_for_unknown_party(
        self, layer: SqlitePersistenceLayer
    ) -> None:
        state = _make_state()
        await layer.create_session(state)
        count = await layer.memory_entry_count("test-session", "nobody")
        assert count == 0


# ---------------------------------------------------------------------------
# Checkpoint
# ---------------------------------------------------------------------------


class TestCheckpoint:
    async def test_checkpoint_returns_session_id(self, layer: SqlitePersistenceLayer) -> None:
        state = _make_state()
        await layer.create_session(state)
        result = await layer.checkpoint(state)
        assert result == "test-session"

    async def test_checkpoint_persists_state_changes(self, layer: SqlitePersistenceLayer) -> None:
        state = _make_state()
        await layer.create_session(state)
        state.parties["alice"].apply_state_update({"x": 42}, episode_index=0)
        await layer.checkpoint(state)

        loaded = await layer.load_session("test-session")
        assert loaded.parties["alice"].state["x"] == 42


# ---------------------------------------------------------------------------
# Fork
# ---------------------------------------------------------------------------


class TestFork:
    async def test_fork_creates_new_session(self, layer: SqlitePersistenceLayer) -> None:
        state = _make_state("root")
        await layer.create_session(state)
        await layer.save_episode("root", _make_episode(0))

        await layer.fork("root", "fork-1")
        sessions = await layer.list_sessions()
        ids = {s.session_id for s in sessions}
        assert "root" in ids and "fork-1" in ids

    async def test_fork_has_correct_parent(self, layer: SqlitePersistenceLayer) -> None:
        state = _make_state("root")
        await layer.create_session(state)
        await layer.save_episode("root", _make_episode(0))

        await layer.fork("root", "fork-1")
        sessions = await layer.list_sessions()
        fork_summary = next(s for s in sessions if s.session_id == "fork-1")
        assert fork_summary.parent_session_id == "root"

    async def test_fork_history_independent(self, layer: SqlitePersistenceLayer) -> None:
        state = _make_state("root")
        await layer.create_session(state)
        await layer.save_episode("root", _make_episode(0))

        forked = await layer.fork("root", "fork-1")
        assert forked.config.session_id == "fork-1"

        # Add episode to fork only
        await layer.save_episode("fork-1", _make_episode(1))

        root_history = await layer.load_history("root")
        fork_history = await layer.load_history("fork-1")
        assert len(root_history.episodes) == 1
        assert len(fork_history.episodes) == 2

    async def test_fork_of_fork_has_correct_parent(self, layer: SqlitePersistenceLayer) -> None:
        state = _make_state("root")
        await layer.create_session(state)
        await layer.save_episode("root", _make_episode(0))
        await layer.fork("root", "fork-1")
        await layer.fork("fork-1", "fork-2")

        sessions = {s.session_id: s for s in await layer.list_sessions()}
        assert sessions["fork-2"].parent_session_id == "fork-1"

    async def test_fork_nonexistent_raises(self, layer: SqlitePersistenceLayer) -> None:
        with pytest.raises(SessionNotFoundError):
            await layer.fork("no-such", "new-id")

    async def test_fork_memories_copied(self, layer: SqlitePersistenceLayer) -> None:
        state = _make_state("root")
        await layer.create_session(state)
        entry = MemoryEntry(
            party_id="alice", kind=MemoryKind.EPISODIC, content="shared", episode_index=0
        )
        await layer.write_memory("root", entry)
        await layer.fork("root", "fork-1")

        root_mems = await layer.retrieve_memories("root", "alice")
        fork_mems = await layer.retrieve_memories("fork-1", "alice")
        assert len(root_mems) == 1
        assert len(fork_mems) == 1

    async def test_fork_source_entry_ids_remapped(self, layer: SqlitePersistenceLayer) -> None:
        """source_entry_ids in forked entries must point to the fork's own entry IDs."""
        state = _make_state("root")
        await layer.create_session(state)

        # Write two source entries then a compacted entry referencing them
        src1 = MemoryEntry(
            party_id="alice", kind=MemoryKind.EPISODIC, content="src1", episode_index=0
        )
        src2 = MemoryEntry(
            party_id="alice", kind=MemoryKind.EPISODIC, content="src2", episode_index=0
        )
        compacted = MemoryEntry(
            party_id="alice",
            kind=MemoryKind.COMPACTED,
            content="compacted",
            episode_index=1,
            source_entry_ids=(src1.id, src2.id),
        )
        await layer.write_memories("root", [src1, src2, compacted])
        await layer.fork("root", "fork-1")

        fork_mems = await layer.retrieve_memories("fork-1", "alice")
        fork_ids = {m.id for m in fork_mems}
        fork_compacted = next(m for m in fork_mems if m.kind == MemoryKind.COMPACTED)

        # source_entry_ids must reference entries that exist in the fork
        for src_id in fork_compacted.source_entry_ids:
            assert src_id in fork_ids, (
                f"source_entry_id {src_id!r} not found in fork entries {fork_ids}"
            )
        # And they must NOT reference the original session's IDs
        original_ids = {src1.id, src2.id, compacted.id}
        for src_id in fork_compacted.source_entry_ids:
            assert src_id not in original_ids, (
                f"source_entry_id {src_id!r} still points to original session entry"
            )


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


class TestExportJson:
    async def test_export_has_expected_keys(self, layer: SqlitePersistenceLayer) -> None:
        state = _make_state()
        await layer.create_session(state)
        result = await layer.export_json("test-session")
        assert set(result.keys()) == {"session", "parties", "episodes", "memories"}

    async def test_export_session_id_correct(self, layer: SqlitePersistenceLayer) -> None:
        state = _make_state()
        await layer.create_session(state)
        result = await layer.export_json("test-session")
        assert result["session"]["session_id"] == "test-session"

    async def test_export_parties_count(self, layer: SqlitePersistenceLayer) -> None:
        state = _make_state()
        await layer.create_session(state)
        result = await layer.export_json("test-session")
        # alice, bob, env-1
        assert len(result["parties"]) == 3

    async def test_export_includes_turns(self, layer: SqlitePersistenceLayer) -> None:
        state = _make_state()
        await layer.create_session(state)
        await layer.save_episode("test-session", _make_episode(0))
        result = await layer.export_json("test-session")
        assert len(result["episodes"]) == 1
        assert len(result["episodes"][0]["turns"]) == 2

    async def test_export_no_memories(self, layer: SqlitePersistenceLayer) -> None:
        state = _make_state()
        await layer.create_session(state)
        result = await layer.export_json("test-session")
        assert result["memories"] == []

    async def test_export_nonexistent_raises(self, layer: SqlitePersistenceLayer) -> None:
        with pytest.raises(SessionNotFoundError):
            await layer.export_json("ghost")


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    async def test_session_with_zero_episodes(self, layer: SqlitePersistenceLayer) -> None:
        state = _make_state()
        await layer.create_session(state)
        history = await layer.load_history("test-session")
        assert history.episodes == []

    async def test_write_memories_empty_list_noop(self, layer: SqlitePersistenceLayer) -> None:
        state = _make_state()
        await layer.create_session(state)
        await layer.write_memories("test-session", [])  # must not raise

    async def test_delete_memories_empty_list_noop(self, layer: SqlitePersistenceLayer) -> None:
        state = _make_state()
        await layer.create_session(state)
        await layer.delete_memories("test-session", [])  # must not raise
