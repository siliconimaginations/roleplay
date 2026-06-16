"""Persistence layer — SQLite-backed durable session storage.

Public surface::

    from roleplay.persistence import SqlitePersistenceLayer
    from roleplay.persistence.base import (
        PersistenceError,
        SessionNotFoundError,
        CorruptedSessionError,
        SessionSummary,
    )
"""

from roleplay.persistence.base import (
    CorruptedSessionError,
    PersistenceError,
    SessionNotFoundError,
    SessionSummary,
)
from roleplay.persistence.sqlite import SqlitePersistenceLayer

__all__ = [
    "CorruptedSessionError",
    "PersistenceError",
    "SessionNotFoundError",
    "SessionSummary",
    "SqlitePersistenceLayer",
]
