"""Memory compaction — summarise old low-importance entries into COMPACTED entries."""

from __future__ import annotations

import logging
import warnings
from collections.abc import Callable
from uuid import uuid4

from roleplay.memory.store import InMemoryStore, MemoryEntry, MemoryKind

logger = logging.getLogger(__name__)

# Summariser callable: (party_name, batch) -> summary_text
_Summariser = Callable[[str, list[MemoryEntry]], str]


async def maybe_compact(
    store: InMemoryStore,
    party_id: str,
    party_name: str,
    *,
    compaction_threshold: int = 200,
    compaction_batch_size: int = 50,
    compaction_importance_floor: float = 0.7,
    compaction_char_limit: int = 80_000,
    current_episode_index: int = 0,
    summariser: _Summariser | None = None,
) -> bool:
    """Run compaction for *party_id* if the trigger threshold is exceeded.

    Returns ``True`` if compaction ran, ``False`` otherwise.

    *summariser* is a callable ``(party_name, batch) -> str``.  If ``None``,
    a fallback template summary is used (useful in tests without an LLM).
    """
    count = await store.entry_count(party_id)
    char_len = await store.total_content_length(party_id)

    if count <= compaction_threshold and char_len <= compaction_char_limit:
        return False

    # Warn when protected entries are piling up
    episodic = await store.list_all(party_id, kinds=frozenset({MemoryKind.EPISODIC}))
    protected = [e for e in episodic if e.importance >= compaction_importance_floor]
    if len(protected) > compaction_threshold // 2:
        warnings.warn(
            f"Party '{party_id}' has {len(protected)} protected entries "
            f"(importance >= {compaction_importance_floor}). "
            "Consider lowering the importance floor.",
            stacklevel=2,
        )

    compactable = sorted(
        [e for e in episodic if e.importance < compaction_importance_floor],
        key=lambda e: e.episode_index,  # oldest first
    )

    if len(compactable) < 10:
        logger.debug("Party '%s': fewer than 10 compactable entries, skipping.", party_id)
        return False

    batch = compactable[:compaction_batch_size]

    if summariser is not None:
        summary_text = summariser(party_name, batch)
    else:
        # Template fallback (no LLM)
        lines = [e.content[:100] for e in batch]
        summary_text = f"{party_name}: " + " | ".join(lines)
        summary_text = summary_text[:500]

    max_importance = max(e.importance for e in batch)
    compacted = MemoryEntry(
        id=str(uuid4()),
        party_id=party_id,
        kind=MemoryKind.COMPACTED,
        content=summary_text,
        episode_index=current_episode_index,
        importance=max_importance,
        source_entry_ids=tuple(e.id for e in batch),
    )

    await store.write_many([compacted])
    await store.delete_many([e.id for e in batch])
    return True
