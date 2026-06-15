"""Tests for memory compaction."""

from __future__ import annotations

import warnings

from roleplay.memory.compaction import maybe_compact
from roleplay.memory.store import InMemoryStore, MemoryEntry, MemoryKind


def _ep(
    party_id: str = "alice",
    importance: float = 0.5,
    episode_index: int = 1,
    content: str = "some fact about the world",
) -> MemoryEntry:
    return MemoryEntry(
        party_id=party_id,
        kind=MemoryKind.EPISODIC,
        content=content,
        episode_index=episode_index,
        importance=importance,
    )


async def _fill(store: InMemoryStore, n: int, importance: float = 0.5) -> None:
    entries = [_ep(importance=importance, episode_index=i, content=f"fact {i}") for i in range(n)]
    await store.write_many(entries)


class TestMaybeCompact:
    async def test_no_compaction_below_threshold(self) -> None:
        store = InMemoryStore()
        await _fill(store, 10)
        ran = await maybe_compact(
            store,
            "alice",
            "Alice",
            compaction_threshold=200,
            current_episode_index=10,
        )
        assert ran is False
        assert await store.entry_count("alice") == 10

    async def test_compaction_above_threshold(self) -> None:
        store = InMemoryStore()
        await _fill(store, 50)
        ran = await maybe_compact(
            store,
            "alice",
            "Alice",
            compaction_threshold=20,
            compaction_batch_size=10,
            current_episode_index=50,
        )
        assert ran is True

    async def test_compacted_entry_written(self) -> None:
        store = InMemoryStore()
        await _fill(store, 30)
        await maybe_compact(
            store,
            "alice",
            "Alice",
            compaction_threshold=20,
            compaction_batch_size=10,
            current_episode_index=30,
        )
        compacted = await store.list_all("alice", kinds=frozenset({MemoryKind.COMPACTED}))
        assert len(compacted) == 1

    async def test_source_entries_deleted(self) -> None:
        store = InMemoryStore()
        await _fill(store, 30)
        before = await store.entry_count("alice")
        await maybe_compact(
            store,
            "alice",
            "Alice",
            compaction_threshold=20,
            compaction_batch_size=10,
            current_episode_index=30,
        )
        after = await store.entry_count("alice")
        assert after == before - 10 + 1  # -10 source + 1 compacted

    async def test_source_entry_ids_recorded(self) -> None:
        store = InMemoryStore()
        await _fill(store, 30)
        await maybe_compact(
            store,
            "alice",
            "Alice",
            compaction_threshold=20,
            compaction_batch_size=10,
            current_episode_index=30,
        )
        compacted = await store.list_all("alice", kinds=frozenset({MemoryKind.COMPACTED}))
        assert len(compacted[0].source_entry_ids) == 10

    async def test_compacted_importance_is_max_of_batch(self) -> None:
        store = InMemoryStore()
        entries = [_ep(importance=0.3, episode_index=i, content=f"fact {i}") for i in range(10)]
        entries.append(_ep(importance=0.6, episode_index=10, content="special fact"))
        await store.write_many(entries)
        await maybe_compact(
            store,
            "alice",
            "Alice",
            compaction_threshold=5,
            compaction_batch_size=11,
            current_episode_index=11,
        )
        compacted = await store.list_all("alice", kinds=frozenset({MemoryKind.COMPACTED}))
        assert abs(compacted[0].importance - 0.6) < 1e-9

    async def test_protected_entries_excluded_from_batch(self) -> None:
        store = InMemoryStore()
        protected = [
            _ep(importance=0.9, episode_index=i, content=f"protected {i}") for i in range(5)
        ]
        compactable = [
            _ep(importance=0.3, episode_index=i + 10, content=f"compactable {i}")
            for i in range(20)
        ]
        await store.write_many(protected + compactable)
        await maybe_compact(
            store,
            "alice",
            "Alice",
            compaction_threshold=10,
            compaction_batch_size=10,
            compaction_importance_floor=0.7,
            current_episode_index=30,
        )
        remaining = await store.list_all("alice")
        remaining_contents = [e.content for e in remaining]
        for i in range(5):
            assert f"protected {i}" in remaining_contents

    async def test_skips_when_fewer_than_10_compactable(self) -> None:
        store = InMemoryStore()
        entries = [
            _ep(importance=0.9, episode_index=i, content=f"protected {i}") for i in range(20)
        ] + [_ep(importance=0.3, episode_index=i + 20, content=f"c{i}") for i in range(5)]
        await store.write_many(entries)
        ran = await maybe_compact(
            store,
            "alice",
            "Alice",
            compaction_threshold=10,
            compaction_importance_floor=0.7,
            current_episode_index=30,
        )
        assert ran is False

    async def test_custom_summariser_called(self) -> None:
        store = InMemoryStore()
        await _fill(store, 30)
        called_with: list[str] = []

        def summariser(party_name: str, batch: list) -> str:
            called_with.append(party_name)
            return "custom summary"

        await maybe_compact(
            store,
            "alice",
            "Alice",
            compaction_threshold=20,
            compaction_batch_size=10,
            current_episode_index=30,
            summariser=summariser,
        )
        assert called_with == ["Alice"]
        compacted = await store.list_all("alice", kinds=frozenset({MemoryKind.COMPACTED}))
        assert compacted[0].content == "custom summary"

    async def test_compaction_by_char_limit(self) -> None:
        store = InMemoryStore()
        entries = [
            _ep(importance=0.3, episode_index=i, content="x" * 100) for i in range(15)
        ]
        await store.write_many(entries)
        ran = await maybe_compact(
            store,
            "alice",
            "Alice",
            compaction_threshold=1000,  # count threshold not reached
            compaction_char_limit=200,  # char limit triggers
            compaction_batch_size=10,
            current_episode_index=15,
        )
        assert ran is True

    async def test_protected_count_warning(self) -> None:
        store = InMemoryStore()
        entries = [
            _ep(importance=0.9, episode_index=i, content=f"protected {i}") for i in range(60)
        ] + [
            _ep(importance=0.3, episode_index=i + 60, content=f"c{i}") for i in range(10)
        ]
        await store.write_many(entries)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            await maybe_compact(
                store,
                "alice",
                "Alice",
                compaction_threshold=50,
                compaction_importance_floor=0.7,
                compaction_batch_size=10,
                current_episode_index=70,
            )
        assert any("protected" in str(warning.message) for warning in w)
