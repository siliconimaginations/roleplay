"""Memory Engine — MemoryEntry, MemoryKind, MemoryStore protocol, InMemoryStore."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Protocol
from uuid import uuid4


class MemoryKind(Enum):
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"
    COMPACTED = "compacted"


@dataclass
class MemoryEntry:
    """One atomic unit of stored recall, belonging to exactly one party."""

    party_id: str
    kind: MemoryKind
    content: str
    episode_index: int
    id: str = field(default_factory=lambda: str(uuid4()))
    importance: float = 1.0
    last_accessed_episode: int = 0
    access_count: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    source_entry_ids: tuple[str, ...] = ()
    forgotten: bool = False


# ---------------------------------------------------------------------------
# Scoring helpers (shared between InMemoryStore and SqliteMemoryStore)
# ---------------------------------------------------------------------------

_BIGRAM_RE = re.compile(r"\b\w+\b")


def _tokens(text: str) -> set[str]:
    """Return lowercase word tokens from *text*."""
    return set(_BIGRAM_RE.findall(text.lower()))


def score_entry(
    entry: MemoryEntry,
    query: str,
    current_episode_index: int,
    weights: dict[str, float] | None = None,
    recency_window: int = 50,
) -> float:
    """Score a memory entry against a retrieval query.

    score = alpha*relevance + beta*recency + gamma*importance + delta*access_frequency
    """
    if weights is None:
        weights = {"alpha": 0.5, "beta": 0.25, "gamma": 0.15, "delta": 0.10}

    alpha = weights.get("alpha", 0.5)
    beta = weights.get("beta", 0.25)
    gamma = weights.get("gamma", 0.15)
    delta = weights.get("delta", 0.10)

    # Relevance: keyword/token overlap
    query_tokens = _tokens(query)
    entry_tokens = _tokens(entry.content)
    if query_tokens and entry_tokens:
        relevance = len(query_tokens & entry_tokens) / len(query_tokens | entry_tokens)
    else:
        relevance = 0.0

    # Recency: linear decay over recency_window episodes
    age = current_episode_index - entry.episode_index
    recency = max(0.0, 1.0 - age / recency_window)

    # Importance: direct
    importance = max(0.0, min(1.0, entry.importance))

    # Access frequency: soft sigmoid, normalised to [0, 1]
    access_freq = min(1.0, entry.access_count / 10.0)

    return alpha * relevance + beta * recency + gamma * importance + delta * access_freq


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class MemoryStore(Protocol):
    """Protocol for all memory store implementations."""

    async def write(self, entry: MemoryEntry) -> None: ...

    async def write_many(self, entries: list[MemoryEntry]) -> None: ...

    async def delete(self, entry_id: str) -> None: ...

    async def delete_many(self, entry_ids: list[str]) -> None: ...

    async def retrieve(
        self,
        party_id: str,
        query: str,
        *,
        max_entries: int = 20,
        episode_index: int,
        weights: dict[str, float] | None = None,
    ) -> list[MemoryEntry]: ...

    async def list_all(
        self,
        party_id: str,
        *,
        kinds: frozenset[MemoryKind] | None = None,
    ) -> list[MemoryEntry]: ...

    async def entry_count(self, party_id: str) -> int: ...

    async def total_content_length(self, party_id: str) -> int: ...


# ---------------------------------------------------------------------------
# InMemoryStore — used in tests and as the reference implementation
# ---------------------------------------------------------------------------


class InMemoryStore:
    """Pure-Python in-memory implementation of MemoryStore."""

    def __init__(self) -> None:
        # party_id → list of entries (insertion order)
        self._store: dict[str, list[MemoryEntry]] = defaultdict(list)

    async def write(self, entry: MemoryEntry) -> None:
        self._store[entry.party_id].append(entry)

    async def write_many(self, entries: list[MemoryEntry]) -> None:
        for entry in entries:
            await self.write(entry)

    async def delete(self, entry_id: str) -> None:
        for entries in self._store.values():
            for i, e in enumerate(entries):
                if e.id == entry_id:
                    entries.pop(i)
                    return
        # Non-existent entry_id is a no-op (idempotent)

    async def delete_many(self, entry_ids: list[str]) -> None:
        id_set = set(entry_ids)
        for party_id in list(self._store.keys()):
            self._store[party_id] = [e for e in self._store[party_id] if e.id not in id_set]

    async def retrieve(
        self,
        party_id: str,
        query: str,
        *,
        max_entries: int = 20,
        episode_index: int,
        weights: dict[str, float] | None = None,
    ) -> list[MemoryEntry]:
        entries = [e for e in self._store.get(party_id, []) if not e.forgotten]
        scored = sorted(
            entries,
            key=lambda e: score_entry(e, query, episode_index, weights),
            reverse=True,
        )
        result = scored[:max_entries]
        # Update access stats
        for e in result:
            e.last_accessed_episode = episode_index
            e.access_count += 1
        return result

    async def list_all(
        self,
        party_id: str,
        *,
        kinds: frozenset[MemoryKind] | None = None,
    ) -> list[MemoryEntry]:
        entries = list(reversed(self._store.get(party_id, [])))  # newest first
        if kinds is not None:
            entries = [e for e in entries if e.kind in kinds]
        return entries

    async def entry_count(self, party_id: str) -> int:
        return len(self._store.get(party_id, []))

    async def total_content_length(self, party_id: str) -> int:
        return sum(len(e.content) for e in self._store.get(party_id, []))
