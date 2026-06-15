"""Tests for MemoryEntry, score_entry, and InMemoryStore."""

from __future__ import annotations

from datetime import datetime

from roleplay.memory.store import (
    InMemoryStore,
    MemoryEntry,
    MemoryKind,
    score_entry,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _entry(
    party_id: str = "alice",
    content: str = "Alice saw Bob near the warehouse",
    episode_index: int = 1,
    importance: float = 0.5,
    kind: MemoryKind = MemoryKind.EPISODIC,
    access_count: int = 0,
) -> MemoryEntry:
    return MemoryEntry(
        party_id=party_id,
        kind=kind,
        content=content,
        episode_index=episode_index,
        importance=importance,
        access_count=access_count,
    )


# ---------------------------------------------------------------------------
# MemoryEntry
# ---------------------------------------------------------------------------


class TestMemoryEntry:
    def test_defaults(self) -> None:
        e = _entry()
        assert e.id != ""
        assert e.last_accessed_episode == 0
        assert e.access_count == 0
        assert e.forgotten is False
        assert e.source_entry_ids == ()
        assert isinstance(e.created_at, datetime)

    def test_id_unique(self) -> None:
        e1 = _entry()
        e2 = _entry()
        assert e1.id != e2.id

    def test_kind_values(self) -> None:
        assert MemoryKind.EPISODIC.value == "episodic"
        assert MemoryKind.SEMANTIC.value == "semantic"
        assert MemoryKind.PROCEDURAL.value == "procedural"
        assert MemoryKind.COMPACTED.value == "compacted"


# ---------------------------------------------------------------------------
# score_entry
# ---------------------------------------------------------------------------


class TestScoreEntry:
    def test_identical_content_scores_high_relevance(self) -> None:
        e = _entry(content="Alice offered Bob two hundred coins")
        score = score_entry(e, "Alice offered Bob two hundred coins", 5)
        assert score > 0.8

    def test_no_overlap_low_relevance(self) -> None:
        e = _entry(content="the weather is sunny today")
        score = score_entry(e, "Alice coins deal", 5)
        assert score < 0.5

    def test_recent_beats_old(self) -> None:
        recent = _entry(content="generic fact", episode_index=9)
        old = _entry(content="generic fact", episode_index=1)
        s_recent = score_entry(recent, "generic fact", 10)
        s_old = score_entry(old, "generic fact", 10)
        assert s_recent > s_old

    def test_high_importance_beats_low(self) -> None:
        high = _entry(content="event", importance=0.9, episode_index=5)
        low = _entry(content="event", importance=0.1, episode_index=5)
        assert score_entry(high, "event", 10) > score_entry(low, "event", 10)

    def test_high_access_count_boosts_score(self) -> None:
        freq = _entry(content="fact", access_count=10, episode_index=5)
        rare = _entry(content="fact", access_count=0, episode_index=5)
        assert score_entry(freq, "fact", 10) > score_entry(rare, "fact", 10)

    def test_recency_window(self) -> None:
        e_now = _entry(content="x", episode_index=10)
        e_old = _entry(content="x", episode_index=0)
        assert score_entry(e_now, "x", 10, recency_window=50) > score_entry(
            e_old, "x", 50, recency_window=50
        )

    def test_empty_query(self) -> None:
        e = _entry(content="something")
        assert score_entry(e, "", 5) >= 0.0

    def test_custom_weights(self) -> None:
        e = _entry(content="matching words in query", importance=0.1)
        score_default = score_entry(e, "matching words", 5)
        score_imp_only = score_entry(
            e,
            "matching words",
            5,
            weights={"alpha": 0.0, "beta": 0.0, "gamma": 1.0, "delta": 0.0},
        )
        assert score_default != score_imp_only


# ---------------------------------------------------------------------------
# InMemoryStore — write / list_all
# ---------------------------------------------------------------------------


class TestInMemoryStoreWrite:
    async def test_write_and_list_all(self) -> None:
        store = InMemoryStore()
        e = _entry()
        await store.write(e)
        result = await store.list_all("alice")
        assert len(result) == 1
        assert result[0].id == e.id

    async def test_write_many(self) -> None:
        store = InMemoryStore()
        entries = [_entry(content=f"fact {i}") for i in range(5)]
        await store.write_many(entries)
        result = await store.list_all("alice")
        assert len(result) == 5

    async def test_write_many_empty(self) -> None:
        store = InMemoryStore()
        await store.write_many([])
        assert await store.entry_count("alice") == 0

    async def test_list_all_newest_first(self) -> None:
        store = InMemoryStore()
        e1 = _entry(content="old", episode_index=1)
        e2 = _entry(content="new", episode_index=5)
        await store.write_many([e1, e2])
        result = await store.list_all("alice")
        assert result[0].id == e2.id

    async def test_list_all_filter_by_kind(self) -> None:
        store = InMemoryStore()
        e_ep = _entry(kind=MemoryKind.EPISODIC)
        e_sem = _entry(kind=MemoryKind.SEMANTIC)
        await store.write_many([e_ep, e_sem])
        result = await store.list_all("alice", kinds=frozenset({MemoryKind.EPISODIC}))
        assert all(e.kind is MemoryKind.EPISODIC for e in result)
        assert len(result) == 1

    async def test_list_all_empty_party(self) -> None:
        store = InMemoryStore()
        assert await store.list_all("nobody") == []

    async def test_party_isolation(self) -> None:
        store = InMemoryStore()
        alice_entry = _entry(party_id="alice", content="alice secret")
        bob_entry = _entry(party_id="bob", content="bob secret")
        await store.write_many([alice_entry, bob_entry])
        alice_memories = await store.list_all("alice")
        assert len(alice_memories) == 1
        assert alice_memories[0].party_id == "alice"


# ---------------------------------------------------------------------------
# InMemoryStore — delete
# ---------------------------------------------------------------------------


class TestInMemoryStoreDelete:
    async def test_delete(self) -> None:
        store = InMemoryStore()
        e = _entry()
        await store.write(e)
        await store.delete(e.id)
        assert await store.list_all("alice") == []

    async def test_delete_nonexistent_noop(self) -> None:
        store = InMemoryStore()
        await store.delete("no-such-id")  # must not raise

    async def test_delete_many(self) -> None:
        store = InMemoryStore()
        entries = [_entry(content=f"fact {i}") for i in range(5)]
        await store.write_many(entries)
        await store.delete_many([entries[0].id, entries[2].id, entries[4].id])
        remaining = await store.list_all("alice")
        remaining_ids = {e.id for e in remaining}
        assert entries[1].id in remaining_ids
        assert entries[3].id in remaining_ids
        assert len(remaining) == 2

    async def test_delete_many_empty_list(self) -> None:
        store = InMemoryStore()
        e = _entry()
        await store.write(e)
        await store.delete_many([])
        assert await store.entry_count("alice") == 1


# ---------------------------------------------------------------------------
# InMemoryStore — retrieve
# ---------------------------------------------------------------------------


class TestInMemoryStoreRetrieve:
    async def test_retrieve_basic(self) -> None:
        store = InMemoryStore()
        e = _entry(content="Alice offered Bob coins", episode_index=5)
        await store.write(e)
        result = await store.retrieve("alice", "Alice coins", episode_index=6)
        assert len(result) == 1

    async def test_retrieve_max_entries(self) -> None:
        store = InMemoryStore()
        entries = [_entry(content=f"fact {i}", episode_index=i) for i in range(10)]
        await store.write_many(entries)
        result = await store.retrieve("alice", "fact", max_entries=3, episode_index=10)
        assert len(result) <= 3

    async def test_retrieve_updates_access_stats(self) -> None:
        store = InMemoryStore()
        e = _entry(content="important event", episode_index=1)
        await store.write(e)
        await store.retrieve("alice", "important", episode_index=5)
        updated = await store.list_all("alice")
        assert updated[0].access_count == 1
        assert updated[0].last_accessed_episode == 5

    async def test_retrieve_empty_party(self) -> None:
        store = InMemoryStore()
        result = await store.retrieve("nobody", "query", episode_index=1)
        assert result == []

    async def test_retrieve_relevant_beats_irrelevant(self) -> None:
        store = InMemoryStore()
        relevant = _entry(content="Alice offered coins deal trade", episode_index=5)
        irrelevant = _entry(content="the sun is shining brightly outside", episode_index=5)
        await store.write_many([relevant, irrelevant])
        result = await store.retrieve("alice", "Alice coins trade", max_entries=2, episode_index=6)
        assert result[0].id == relevant.id

    async def test_retrieve_excludes_forgotten(self) -> None:
        store = InMemoryStore()
        e = _entry(content="forgotten secret")
        e.forgotten = True
        await store.write(e)
        result = await store.retrieve("alice", "secret", episode_index=5)
        assert result == []


# ---------------------------------------------------------------------------
# InMemoryStore — entry_count / total_content_length
# ---------------------------------------------------------------------------


class TestInMemoryStoreStats:
    async def test_entry_count(self) -> None:
        store = InMemoryStore()
        await store.write_many([_entry(content=f"x{i}") for i in range(7)])
        assert await store.entry_count("alice") == 7

    async def test_entry_count_empty(self) -> None:
        store = InMemoryStore()
        assert await store.entry_count("alice") == 0

    async def test_total_content_length(self) -> None:
        store = InMemoryStore()
        await store.write_many([_entry(content="hello"), _entry(content="world!")])
        assert await store.total_content_length("alice") == 11

    async def test_total_content_length_empty(self) -> None:
        store = InMemoryStore()
        assert await store.total_content_length("alice") == 0
