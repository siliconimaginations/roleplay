"""Simulation control and WebSocket endpoints."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, status

from roleplay.api.auth import require_api_key
from roleplay.api.runner import SessionRunner
from roleplay.api.schemas import InjectRequest, RunStatus
from roleplay.persistence.base import SessionNotFoundError

if TYPE_CHECKING:
    from roleplay.persistence.base import PersistenceLayer

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sessions", tags=["simulation"])

Auth = Annotated[None, Depends(require_api_key)]


def _get_runner(request: Request, session_id: str) -> SessionRunner:
    runner = request.app.state.runners.get(session_id)
    if runner is None:
        runner = SessionRunner(session_id)
        request.app.state.runners[session_id] = runner
    return runner


def _layer(request: Request) -> PersistenceLayer:
    return request.app.state.layer  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# POST /sessions/{session_id}/run
# ---------------------------------------------------------------------------


@router.post("/{session_id}/run", status_code=status.HTTP_202_ACCEPTED)
async def run_session(
    session_id: str,
    request: Request,
    _auth: Auth,
    episodes: int = Query(default=1, ge=1, le=100),
) -> RunStatus:
    """Start running N episodes in the background.

    Returns ``409 Conflict`` if the session is already running.
    """
    runner = _get_runner(request, session_id)
    if runner.status == "running":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Session is already running",
        )

    layer = _layer(request)
    try:
        state = await layer.load_session(session_id)
    except SessionNotFoundError:
        raise HTTPException(status_code=404, detail=f"Session {session_id!r} not found") from None

    # Open a fresh persistence layer for the background task
    from roleplay.persistence.sqlite import SqlitePersistenceLayer

    bg_layer = SqlitePersistenceLayer(layer._db_path)  # type: ignore[attr-defined]
    await bg_layer.open()

    runner.start(state, bg_layer, episodes)

    return RunStatus(
        session_id=session_id,
        status=runner.status,
        episodes_completed=runner.episodes_completed,
        episodes_requested=runner.episodes_requested,
    )


# ---------------------------------------------------------------------------
# GET /sessions/{session_id}/status
# ---------------------------------------------------------------------------


@router.get("/{session_id}/status")
async def get_status(
    session_id: str,
    request: Request,
    _auth: Auth,
) -> RunStatus:
    """Get the current run status of a session."""
    runner = _get_runner(request, session_id)
    return RunStatus(
        session_id=session_id,
        status=runner.status,
        episodes_completed=runner.episodes_completed,
        episodes_requested=runner.episodes_requested,
        error=runner.error,
    )


# ---------------------------------------------------------------------------
# POST /sessions/{session_id}/pause
# ---------------------------------------------------------------------------


@router.post("/{session_id}/pause")
async def pause_session(
    session_id: str,
    request: Request,
    _auth: Auth,
) -> RunStatus:
    """Request the running session to pause after the current turn."""
    runner = _get_runner(request, session_id)
    if runner.status != "running":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Session is not running (status={runner.status!r})",
        )
    runner.request_pause()
    return RunStatus(
        session_id=session_id,
        status=runner.status,
        episodes_completed=runner.episodes_completed,
        episodes_requested=runner.episodes_requested,
    )


# ---------------------------------------------------------------------------
# POST /sessions/{session_id}/inject
# ---------------------------------------------------------------------------


@router.post("/{session_id}/inject")
async def inject_event(
    session_id: str,
    body: InjectRequest,
    request: Request,
    _auth: Auth,
) -> RunStatus:
    """Inject a narrative event into the running simulation."""
    runner = _get_runner(request, session_id)
    if runner.status not in {"running", "paused"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Session is not active (status={runner.status!r})",
        )
    await runner.inject(body.text)
    return RunStatus(
        session_id=session_id,
        status=runner.status,
        episodes_completed=runner.episodes_completed,
        episodes_requested=runner.episodes_requested,
    )


# ---------------------------------------------------------------------------
# WS /sessions/{session_id}/stream
# ---------------------------------------------------------------------------


@router.websocket("/{session_id}/stream")
async def stream_session(
    session_id: str,
    websocket: WebSocket,
) -> None:
    """WebSocket endpoint for live turn streaming.

    Auth flow:
    1. Client connects.
    2. Client sends ``{"api_key": "<key>"}`` as first message.
    3. Server sends ``{"type": "connected"}`` on success.
    4. Server streams :class:`~roleplay.api.schemas.TurnEvent` JSON objects.
    5. Server sends ``{"type": "simulation_complete"}`` and closes on done.
    """
    import json
    import os

    await websocket.accept()

    # Auth
    configured_key = os.environ.get("ROLEPLAY_API_KEY")
    if configured_key is not None:
        try:
            raw = await asyncio.wait_for(websocket.receive_text(), timeout=10.0)
            msg = json.loads(raw)
            if msg.get("api_key") != configured_key:
                await websocket.send_text(
                    json.dumps({"type": "error", "message": "Invalid API key"})
                )
                await websocket.close(code=4003)
                return
        except (TimeoutError, KeyError, ValueError):
            await websocket.send_text(
                json.dumps({"type": "error", "message": "Auth timeout or invalid message"})
            )
            await websocket.close(code=4001)
            return

    await websocket.send_text(json.dumps({"type": "connected"}))

    # Access app state directly from the WebSocket scope
    _app_state = websocket.app.state
    runners: dict = _app_state.runners
    if session_id not in runners:
        runners[session_id] = SessionRunner(session_id)
    runner = runners[session_id]
    q = runner.subscribe()
    try:
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=30.0)
            except TimeoutError:
                # Send keepalive ping
                try:
                    await websocket.send_text(json.dumps({"type": "ping"}))
                except Exception:
                    break
                continue

            if event is None:  # sentinel — simulation ended
                break

            try:
                await websocket.send_text(json.dumps(event))
            except Exception:
                break

            if event.get("type") == "simulation_complete":
                break
    finally:
        import contextlib
        runner.unsubscribe(q)
        with contextlib.suppress(Exception):
            await websocket.close()
