"""Engine-level Turn dataclass (extends core concept with token counts)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from roleplay.core.party import StateValue


@dataclass
class Turn:
    """One party's output within an episode, as produced by the engine."""

    party_id: str
    output: str
    state_update_proposals: dict[str, StateValue] = field(default_factory=dict)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model_used: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
