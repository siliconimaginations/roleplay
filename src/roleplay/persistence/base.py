"""Persistence layer — public protocol, errors, and SessionSummary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from datetime import datetime

    from roleplay.core.episode import Episode, SimulationHistory
    from roleplay.core.simulation_state import SimulationState
    from roleplay.memory.store import MemoryEntry


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class PersistenceError(Exception):
    """Base class for all persistence layer errors."""


class SessionNotFoundError(PersistenceError):
    """Raised when load_session() is called with an unknown session_id."""

    def __init__(self, session_id: str) -> None:
        super().__init__(f"Session not found: {session_id!r}")
        self.session_id = session_id


class CorruptedSessionError(PersistenceError):
    """Raised when a DB row cannot be deserialised into a domain object."""

    def __init__(self, message: str, entry_id: str | None = None) -> None:
        super().__init__(message)
        self.entry_id = entry_id


# ---------------------------------------------------------------------------
# SessionSummary
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionSummary:
    """Lightweight summary returned by list_sessions()."""

    session_id: str
    parent_session_id: str | None
    forked_at_episode: int | None
    episode_count: int
    party_count: int
    status: str
    started_at: datetime
    last_saved_at: datetime


# ---------------------------------------------------------------------------
# PersistenceLayer protocol
# ---------------------------------------------------------------------------


class PersistenceLayer(Protocol):
    """Defines the contract for all persistence backends.

    The only concrete implementation in this package is SqlitePersistenceLayer.
    A future S3/GCS backend would implement this same protocol.
    """

    # ── Session lifecycle ────────────────────────────────────────────────────

    async def create_session(self, state: SimulationState) -> None:
        """Write the session row and all party rows. Idempotent."""
        ...

    async def save_state(self, state: SimulationState) -> None:
        """Persist current party states and update last_saved_at.

        Only writes state_changes rows that are new since the last save.
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

        Called once when the episode opens (ended_at=None) and again when
        it closes (updates ended_at and simulated_time_end).
        """
        ...

    async def load_history(
        self, session_id: str, max_episodes: int | None = None
    ) -> SimulationHistory:
        """Load the episode + turn history for a session.

        Excludes episodes with ended_at=None (partial / crashed episodes).
        If max_episodes is set, loads only the most recent N episodes.
        """
        ...

    # ── Memory ───────────────────────────────────────────────────────────────

    async def write_memory(self, session_id: str, entry: MemoryEntry) -> None: ...
    async def write_memories(self, session_id: str, entries: list[MemoryEntry]) -> None: ...
    async def delete_memory(self, session_id: str, entry_id: str) -> None: ...
    async def delete_memories(self, session_id: str, entry_ids: list[str]) -> None: ...
    async def retrieve_memories(
        self,
        session_id: str,
        party_id: str,
        *,
        kinds: frozenset[str] | None = None,
    ) -> list[MemoryEntry]: ...
    async def update_memory_access(
        self, session_id: str, entry_id: str, episode_index: int
    ) -> None: ...
    async def memory_entry_count(self, session_id: str, party_id: str) -> int: ...
    async def memory_total_content_length(self, session_id: str, party_id: str) -> int: ...

    # ── Checkpoint and fork ──────────────────────────────────────────────────

    async def checkpoint(self, state: SimulationState) -> str:
        """Atomically persist all current state. Returns session_id."""
        ...

    async def fork(self, session_id: str, new_session_id: str) -> SimulationState:
        """Deep-copy the session under new_session_id.

        Sets parent_session_id and forked_at_episode on the new row.
        """
        ...

    # ── Export ───────────────────────────────────────────────────────────────

    async def export_json(self, session_id: str) -> dict[str, object]:
        """Return the full session as a JSON-serialisable dict."""
        ...
