"""Pydantic schemas for the Roleplay REST API."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Any, Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Session schemas
# ---------------------------------------------------------------------------


class PartySchema(BaseModel):
    id: str
    kind: str
    name: str
    state: dict[str, Any] = Field(default_factory=dict)


class SessionSummary(BaseModel):
    session_id: str
    created_at: datetime
    episode_count: int
    status: Literal["idle", "running", "paused", "done", "error"]


class SessionDetail(BaseModel):
    session_id: str
    created_at: datetime
    episode_count: int
    status: Literal["idle", "running", "paused", "done", "error"]
    config: dict[str, Any]
    parties: list[PartySchema]
    environment: PartySchema | None


# ---------------------------------------------------------------------------
# Simulation control schemas
# ---------------------------------------------------------------------------


class RunStatus(BaseModel):
    session_id: str
    status: Literal["idle", "running", "paused", "done", "error"]
    episodes_completed: int
    episodes_requested: int
    error: str | None = None


class InjectRequest(BaseModel):
    text: str = Field(..., min_length=1, description="Narrative event to inject")


# ---------------------------------------------------------------------------
# WebSocket event schemas
# ---------------------------------------------------------------------------


class TurnEvent(BaseModel):
    type: Literal["turn"] = "turn"
    episode: int
    party_id: str
    output: str
    state_update_proposals: dict[str, Any] = Field(default_factory=dict)


class EpisodeStartEvent(BaseModel):
    type: Literal["episode_start"] = "episode_start"
    episode: int


class EpisodeEndEvent(BaseModel):
    type: Literal["episode_end"] = "episode_end"
    episode: int


class SimulationCompleteEvent(BaseModel):
    type: Literal["simulation_complete"] = "simulation_complete"
    episodes_completed: int


class ErrorEvent(BaseModel):
    type: Literal["error"] = "error"
    message: str


class ConnectedEvent(BaseModel):
    type: Literal["connected"] = "connected"
