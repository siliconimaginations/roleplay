"""JSON serialisation / deserialisation for all domain objects.

Only this module knows about the wire format.  Everything else works with
typed Python objects.  No pickle, no eval.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

if TYPE_CHECKING:
    from roleplay.core.episode import Episode, ToolCall, Turn
    from roleplay.core.party import Persona, StateChange, StateValue
    from roleplay.core.simulation_state import SimulationConfig
    from roleplay.memory.store import MemoryEntry


# ---------------------------------------------------------------------------
# Datetime helpers
# ---------------------------------------------------------------------------


def _dt_to_str(dt: datetime) -> str:
    """Encode a datetime to ISO-8601 UTC string."""
    return dt.astimezone(UTC).isoformat()


def _str_to_dt(s: str) -> datetime:
    """Decode an ISO-8601 string to a timezone-aware UTC datetime."""
    return datetime.fromisoformat(s).astimezone(UTC)


# ---------------------------------------------------------------------------
# StateValue
# ---------------------------------------------------------------------------


def encode_state_value(value: StateValue) -> str:
    """Encode a StateValue to a JSON string (handles None correctly)."""
    return json.dumps(value)


def decode_state_value(s: str | None) -> StateValue:
    """Decode a JSON string to StateValue; None column → None."""
    if s is None:
        return None
    val: StateValue = json.loads(s)
    return val


# ---------------------------------------------------------------------------
# Persona
# ---------------------------------------------------------------------------


def encode_persona(persona: Persona) -> str:
    return json.dumps(
        {
            "description": persona.description,
            "goals": list(persona.goals),
            "traits": list(persona.traits),
            "knowledge": list(persona.knowledge),
            "constraints": list(persona.constraints),
        }
    )


def decode_persona(s: str) -> Persona:
    from roleplay.core.party import Persona as _Persona

    d: dict[str, Any] = json.loads(s)
    return _Persona(
        description=d["description"],
        goals=tuple(d.get("goals", [])),
        traits=tuple(d.get("traits", [])),
        knowledge=tuple(d.get("knowledge", [])),
        constraints=tuple(d.get("constraints", [])),
    )


# ---------------------------------------------------------------------------
# SimulationConfig
# ---------------------------------------------------------------------------


def encode_config(config: SimulationConfig) -> str:
    return json.dumps(dataclasses.asdict(config))


def decode_config(s: str) -> SimulationConfig:
    from roleplay.core.simulation_state import SimulationConfig as _SimulationConfig

    d: dict[str, Any] = json.loads(s)
    return _SimulationConfig(**d)


# ---------------------------------------------------------------------------
# ToolCall
# ---------------------------------------------------------------------------


def encode_tool_calls(tool_calls: tuple[ToolCall, ...]) -> str:
    return json.dumps(
        [
            {
                "tool_name": tc.tool_name,
                "arguments": tc.arguments,
                "result": tc.result,
                "error": tc.error,
            }
            for tc in tool_calls
        ]
    )


def decode_tool_calls(s: str) -> tuple[ToolCall, ...]:
    from roleplay.core.episode import ToolCall as _ToolCall

    items: list[dict[str, Any]] = json.loads(s)
    return tuple(
        _ToolCall(
            tool_name=item["tool_name"],
            arguments=item["arguments"],
            result=item["result"],
            error=item.get("error"),
        )
        for item in items
    )


# ---------------------------------------------------------------------------
# Turn
# ---------------------------------------------------------------------------


def episode_id(session_id: str, episode_index: int) -> str:
    """Deterministic episode_id for storage (no UUID on Episode)."""
    return f"{session_id}/ep/{episode_index}"


def turn_id(session_id: str, episode_index: int, turn_index: int) -> str:
    """Deterministic turn_id for storage (no UUID on Turn)."""
    return f"{session_id}/ep/{episode_index}/t/{turn_index}"


def encode_turn_row(session_id: str, ep_id: str, turn: Turn) -> dict[str, object]:
    return {
        "turn_id": turn_id(session_id, int(ep_id.split("/ep/")[1].split("/")[0]), turn.index),
        "episode_id": ep_id,
        "session_id": session_id,
        "party_id": turn.party_id,
        "turn_index": turn.index,
        "output": turn.output,
        "state_proposals_json": json.dumps(turn.state_update_proposals),
        "tool_calls_json": encode_tool_calls(turn.tool_calls),
        "prompt_tokens": turn.prompt_tokens,
        "completion_tokens": turn.completion_tokens,
        "model_used": turn.model_used,
        "timestamp": _dt_to_str(turn.timestamp),
    }


def decode_turn(row: dict[str, Any]) -> Turn:
    from roleplay.core.episode import Turn as _Turn

    state_proposals: dict[str, StateValue] = json.loads(row["state_proposals_json"])
    return _Turn(
        party_id=row["party_id"],
        index=row["turn_index"],
        output=row["output"],
        state_update_proposals=state_proposals,
        tool_calls=decode_tool_calls(row["tool_calls_json"]),
        prompt_tokens=row["prompt_tokens"],
        completion_tokens=row["completion_tokens"],
        model_used=row.get("model_used", ""),
        timestamp=_str_to_dt(row["timestamp"]),
    )


# ---------------------------------------------------------------------------
# Episode
# ---------------------------------------------------------------------------


def encode_episode_row(session_id: str, ep: Episode) -> dict[str, object]:
    ep_id = episode_id(session_id, ep.index)
    return {
        "episode_id": ep_id,
        "session_id": session_id,
        "episode_index": ep.index,
        "simulated_time_start": ep.simulated_time_start,
        "simulated_time_end": ep.simulated_time_end,
        "started_at": _dt_to_str(ep.started_at),
        "ended_at": _dt_to_str(ep.ended_at) if ep.ended_at else None,
        "summary": ep.summary,
    }


# ---------------------------------------------------------------------------
# MemoryEntry
# ---------------------------------------------------------------------------


def encode_memory_row(session_id: str, entry: MemoryEntry) -> dict[str, object]:
    return {
        "entry_id": entry.id,
        "party_id": entry.party_id,
        "session_id": session_id,
        "kind": entry.kind.value,
        "content": entry.content,
        "episode_index": entry.episode_index,
        "importance": entry.importance,
        "last_accessed_episode": entry.last_accessed_episode,
        "access_count": entry.access_count,
        "source_entry_ids_json": json.dumps(list(entry.source_entry_ids)),
        "created_at": _dt_to_str(entry.created_at),
        "forgotten": int(entry.forgotten),
    }


def decode_memory_entry(row: dict[str, Any]) -> MemoryEntry:
    from roleplay.memory.store import MemoryEntry as _MemoryEntry
    from roleplay.memory.store import MemoryKind

    return _MemoryEntry(
        id=row["entry_id"],
        party_id=row["party_id"],
        kind=MemoryKind(row["kind"]),
        content=row["content"],
        episode_index=row["episode_index"],
        importance=row["importance"],
        last_accessed_episode=row["last_accessed_episode"],
        access_count=row["access_count"],
        source_entry_ids=tuple(json.loads(row["source_entry_ids_json"])),
        created_at=_str_to_dt(row["created_at"]),
        forgotten=bool(row["forgotten"]),
    )


# ---------------------------------------------------------------------------
# StateChange row
# ---------------------------------------------------------------------------


def encode_state_change_row(
    session_id: str, party_id: str, change: StateChange
) -> dict[str, object]:
    return {
        "id": str(uuid4()),
        "party_id": party_id,
        "session_id": session_id,
        "key": change.key,
        "old_value_json": encode_state_value(change.old_value),
        "new_value_json": encode_state_value(change.new_value),
        "episode_index": change.episode_index,
        "reason": None,
    }
