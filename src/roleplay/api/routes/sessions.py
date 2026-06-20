"""Session CRUD endpoints."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse

from roleplay.api.auth import require_api_key
from roleplay.api.schemas import PartySchema, SessionDetail, SessionSummary
from roleplay.persistence.base import SessionNotFoundError

if TYPE_CHECKING:
    from roleplay.api.runner import RunStatusLiteral
    from roleplay.core.simulation_state import SimulationState
    from roleplay.persistence.base import PersistenceLayer

router = APIRouter(prefix="/sessions", tags=["sessions"])

Auth = Annotated[None, Depends(require_api_key)]


def _runner_store(request: Request) -> dict[str, Any]:
    return request.app.state.runners  # type: ignore[no-any-return]


def _layer(request: Request) -> PersistenceLayer:
    return request.app.state.layer  # type: ignore[no-any-return]


def _session_status(session_id: str, runners: dict[str, Any]) -> RunStatusLiteral:
    runner = runners.get(session_id)
    if runner is None:
        return "idle"
    return cast("RunStatusLiteral", runner.status)


def _parties_from_state(
    state: SimulationState,
) -> tuple[list[PartySchema], PartySchema | None]:
    parties = [
        PartySchema(
            id=p.id,
            kind=p.kind.value if hasattr(p.kind, "value") else str(p.kind),
            name=p.name,
            state=dict(p.state_snapshot()),
        )
        for p in state.parties.values()
    ]
    env = state.environment
    env_schema = (
        PartySchema(
            id=env.id,
            kind="environment",
            name=env.name,
            state=dict(env.state_snapshot()),
        )
        if env is not None
        else None
    )
    return parties, env_schema


# ---------------------------------------------------------------------------
# POST /sessions — create from YAML body
# ---------------------------------------------------------------------------


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_session(
    request: Request,
    _auth: Auth,
) -> SessionSummary:
    """Create a new session from a YAML scenario body (text/plain or application/x-yaml)."""
    import tempfile
    from datetime import UTC, datetime
    from pathlib import Path

    from roleplay.scenario_yaml import ValidationError, load_yaml_scenario

    body = await request.body()
    try:
        yaml_text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Request body must be valid UTF-8 YAML text",
        ) from exc

    if not yaml_text.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Request body is empty",
        )

    with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as tmp:
        tmp.write(yaml_text)
        tmp_path = Path(tmp.name)

    try:
        result = load_yaml_scenario(tmp_path)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"errors": exc.errors},
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid YAML: {exc}",
        ) from exc
    finally:
        tmp_path.unlink(missing_ok=True)

    state = result.state
    layer = _layer(request)

    try:
        await layer.create_session(state)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to persist session: {exc}",
        ) from exc

    return SessionSummary(
        session_id=state.config.session_id,
        created_at=datetime.now(tz=UTC),
        episode_count=0,
        status="idle",
    )


# ---------------------------------------------------------------------------
# GET /sessions — list all
# ---------------------------------------------------------------------------


@router.get("")
async def list_sessions(request: Request, _auth: Auth) -> list[SessionSummary]:
    """List all sessions."""
    layer = _layer(request)
    runners = _runner_store(request)
    summaries = await layer.list_sessions()
    return [
        SessionSummary(
            session_id=s.session_id,
            created_at=s.started_at,
            episode_count=s.episode_count,
            status=_session_status(s.session_id, runners),
        )
        for s in summaries
    ]


# ---------------------------------------------------------------------------
# GET /sessions/{session_id}
# ---------------------------------------------------------------------------


@router.get("/{session_id}")
async def get_session(
    session_id: str,
    request: Request,
    _auth: Auth,
) -> SessionDetail:
    """Get full session detail including party state."""
    from datetime import UTC, datetime

    layer = _layer(request)
    runners = _runner_store(request)
    try:
        state = await layer.load_session(session_id)
    except SessionNotFoundError:
        raise HTTPException(status_code=404, detail=f"Session {session_id!r} not found") from None

    parties, env = _parties_from_state(state)
    cfg = state.config

    return SessionDetail(
        session_id=session_id,
        created_at=datetime.now(tz=UTC),
        episode_count=len(state.history.completed_episodes()),
        status=_session_status(session_id, runners),
        config={
            "default_provider": cfg.default_provider,
            "max_episodes": None,
            "goal": cfg.goal,
            "context_window_episodes": cfg.context_window_episodes,
            "memory_max_entries": cfg.memory_max_entries,
            "environment_reactive": cfg.environment_reactive,
        },
        parties=parties,
        environment=env,
    )


# ---------------------------------------------------------------------------
# DELETE /sessions/{session_id}
# ---------------------------------------------------------------------------


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: str,
    request: Request,
    _auth: Auth,
) -> None:
    """Delete a session and all associated data."""
    layer = _layer(request)
    runners = _runner_store(request)
    runner = runners.get(session_id)
    if runner and runner.status == "running":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot delete a running session — pause it first",
        )
    # delete_session is a no-op on a missing session, so check first
    try:
        await layer.load_session(session_id)
    except SessionNotFoundError:
        raise HTTPException(status_code=404, detail=f"Session {session_id!r} not found") from None
    await layer.delete_session(session_id)
    runners.pop(session_id, None)


# ---------------------------------------------------------------------------
# POST /sessions/{session_id}/fork
# ---------------------------------------------------------------------------


@router.post("/{session_id}/fork", status_code=status.HTTP_201_CREATED)
async def fork_session(
    session_id: str,
    request: Request,
    _auth: Auth,
) -> SessionSummary:
    """Fork a session at its current state."""
    from datetime import UTC, datetime

    layer = _layer(request)
    new_id = str(uuid.uuid4())
    try:
        await layer.fork(session_id, new_id)
    except SessionNotFoundError:
        raise HTTPException(status_code=404, detail=f"Session {session_id!r} not found") from None

    return SessionSummary(
        session_id=new_id,
        created_at=datetime.now(tz=UTC),
        episode_count=0,
        status="idle",
    )


# ---------------------------------------------------------------------------
# POST /sessions/validate — validate YAML without creating a session
# ---------------------------------------------------------------------------


@router.post("/validate")
async def validate_session(
    request: Request,
    _auth: Auth,
) -> dict[str, object]:
    """Validate a YAML scenario body without persisting anything.

    Returns ``{"valid": true}`` on success or
    ``{"valid": false, "errors": [...]}`` with a list of human-readable
    error strings on failure.
    """
    import tempfile
    from pathlib import Path

    from roleplay.scenario_yaml import ValidationError, load_yaml_scenario

    body = await request.body()
    try:
        yaml_text = body.decode("utf-8")
    except UnicodeDecodeError:
        return JSONResponse(
            status_code=422,
            content={"valid": False, "errors": ["Request body must be valid UTF-8 YAML text"]},
        )

    if not yaml_text.strip():
        return JSONResponse(
            status_code=422,
            content={"valid": False, "errors": ["Scenario is empty"]},
        )

    with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as tmp:
        tmp.write(yaml_text)
        tmp_path = Path(tmp.name)

    try:
        load_yaml_scenario(tmp_path)
        return {"valid": True, "errors": []}
    except ValidationError as exc:
        return JSONResponse(
            status_code=422,
            content={"valid": False, "errors": exc.errors},
        )
    except Exception as exc:
        return JSONResponse(
            status_code=422,
            content={"valid": False, "errors": [f"Invalid YAML: {exc}"]},
        )
    finally:
        tmp_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# GET /sessions/{session_id}/history
# ---------------------------------------------------------------------------


@router.get("/{session_id}/history")
async def get_session_history(
    session_id: str,
    request: Request,
    _auth: Auth,
) -> list[dict[str, object]]:
    """Return completed episodes and their turns for replay in the UI."""
    from roleplay.persistence import SessionNotFoundError

    layer = _layer(request)
    try:
        history = await layer.load_history(session_id)
    except SessionNotFoundError:
        raise HTTPException(status_code=404, detail=f"Session {session_id!r} not found") from None

    result = []
    for ep in history.completed_episodes():
        result.append(
            {
                "episode": ep.index,
                "done": True,
                "summary": ep.summary,
                "turns": [
                    {
                        "episode": ep.index,
                        "party_id": t.party_id,
                        "output": t.output,
                        "state_update_proposals": t.state_update_proposals,
                    }
                    for t in ep.turns
                ],
            }
        )
    return result
