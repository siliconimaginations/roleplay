"""WebSocket endpoint tests using starlette's sync TestClient."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import threading
from typing import TYPE_CHECKING

from starlette.testclient import TestClient

from roleplay.api.app import create_app
from roleplay.persistence.sqlite import SqlitePersistenceLayer
from tests.api.conftest import MINIMAL_YAML

if TYPE_CHECKING:
    from collections.abc import Callable


async def _open_layer(db_path: str) -> SqlitePersistenceLayer:
    layer = SqlitePersistenceLayer(db_path)
    await layer.open()
    return layer


async def _close_layer(layer: SqlitePersistenceLayer) -> None:
    await layer.close()


def _setup_app(api_key: str | None = None) -> tuple[object, Callable[[], None]]:
    tmpdir = tempfile.mkdtemp(prefix="roleplay_ws_", dir="/tmp")
    db_path = tmpdir + "/ws.db"

    old_db = os.environ.get("ROLEPLAY_DB_PATH")
    old_key = os.environ.get("ROLEPLAY_API_KEY")

    os.environ["ROLEPLAY_DB_PATH"] = db_path
    if api_key is not None:
        os.environ["ROLEPLAY_API_KEY"] = api_key
    else:
        os.environ.pop("ROLEPLAY_API_KEY", None)

    app = create_app()
    layer = asyncio.run(_open_layer(db_path))
    app.state.layer = layer
    app.state.runners = {}

    def cleanup() -> None:
        asyncio.run(_close_layer(layer))
        if old_db is None:
            os.environ.pop("ROLEPLAY_DB_PATH", None)
        else:
            os.environ["ROLEPLAY_DB_PATH"] = old_db
        if old_key is None:
            os.environ.pop("ROLEPLAY_API_KEY", None)
        else:
            os.environ["ROLEPLAY_API_KEY"] = old_key

    return app, cleanup


class TestWebSocket:
    def test_ws_connected_event_no_auth(self) -> None:
        """WebSocket sends connected event when no API key configured."""
        app, cleanup = _setup_app(api_key=None)
        try:
            with (
                TestClient(app) as client,
                client.websocket_connect("/sessions/test-session/stream") as ws,
            ):
                msg = json.loads(ws.receive_text())
                assert msg["type"] == "connected"
        finally:
            cleanup()

    def test_ws_requires_auth_when_key_configured(self) -> None:
        """WebSocket sends error event when wrong key is provided."""
        app, cleanup = _setup_app(api_key="secret-key")
        try:
            with (
                TestClient(app) as client,
                client.websocket_connect("/sessions/test-session/stream") as ws,
            ):
                ws.send_text(json.dumps({"api_key": "wrong-key"}))
                msg = json.loads(ws.receive_text())
                assert msg["type"] == "error"
                assert "Invalid API key" in msg["message"]
        finally:
            cleanup()

    def test_ws_accepts_correct_key(self) -> None:
        """WebSocket sends connected when correct key provided."""
        app, cleanup = _setup_app(api_key="secret-key")
        try:
            with (
                TestClient(app) as client,
                client.websocket_connect("/sessions/test-session/stream") as ws,
            ):
                ws.send_text(json.dumps({"api_key": "secret-key"}))
                msg = json.loads(ws.receive_text())
                assert msg["type"] == "connected"
        finally:
            cleanup()

    def test_ws_receives_simulation_complete(self) -> None:
        """Subscribing before run starts yields simulation_complete event."""
        app, cleanup = _setup_app(api_key=None)
        events: list[dict] = []
        ws_ready = threading.Event()
        done = threading.Event()

        def ws_thread(tc: TestClient) -> None:
            with tc.websocket_connect("/sessions/test-session-001/stream") as ws:
                connected = json.loads(ws.receive_text())
                assert connected["type"] == "connected"
                ws_ready.set()
                for _ in range(40):
                    try:
                        raw = ws.receive_text()
                        ev = json.loads(raw)
                        events.append(ev)
                        if ev.get("type") == "simulation_complete":
                            break
                    except Exception:
                        break
            done.set()

        try:
            with TestClient(app, raise_server_exceptions=False) as tc:
                r = tc.post("/sessions", content=MINIMAL_YAML)
                assert r.status_code == 201

                t = threading.Thread(target=ws_thread, args=(tc,), daemon=True)
                t.start()
                ws_ready.wait(timeout=5)
                tc.post("/sessions/test-session-001/run?episodes=1")
                done.wait(timeout=15)
                t.join(timeout=2)

            types = {e["type"] for e in events}
            assert "simulation_complete" in types, f"Got: {types}"
        finally:
            cleanup()

    def test_ws_auth_invalid_json_closes_with_error(self) -> None:
        """WebSocket closes with auth error when first message is not valid JSON."""
        app, cleanup = _setup_app(api_key="secret-key")
        try:
            with (
                TestClient(app) as client,
                client.websocket_connect("/sessions/test-session/stream") as ws,
            ):
                ws.send_text("not-valid-json!!!")
                msg = json.loads(ws.receive_text())
                assert msg["type"] == "error"
                assert "Auth timeout" in msg["message"] or "invalid" in msg["message"].lower()
        finally:
            cleanup()
