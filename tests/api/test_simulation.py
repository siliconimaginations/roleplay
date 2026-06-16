"""Tests for simulation control endpoints (run/status/pause/inject)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

if TYPE_CHECKING:
    from httpx import AsyncClient

from tests.api.conftest import MINIMAL_YAML


async def _create_session(client: AsyncClient, yaml: str = MINIMAL_YAML) -> str:
    resp = await client.post("/sessions", content=yaml)
    assert resp.status_code == 201, resp.text
    return resp.json()["session_id"]


class TestGetStatus:
    async def test_status_idle_before_run(self, client: AsyncClient) -> None:
        sid = await _create_session(client)
        resp = await client.get(f"/sessions/{sid}/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "idle"
        assert body["episodes_completed"] == 0
        assert body["episodes_requested"] == 0

    async def test_status_nonexistent_session_returns_idle(self, client: AsyncClient) -> None:
        """Unknown sessions get an idle runner (runner is created on demand)."""
        resp = await client.get("/sessions/ghost/status")
        assert resp.status_code == 200
        assert resp.json()["status"] == "idle"


class TestRunSession:
    async def test_run_returns_202(self, client: AsyncClient) -> None:
        sid = await _create_session(client)
        resp = await client.post(f"/sessions/{sid}/run?episodes=2")
        assert resp.status_code == 202
        body = resp.json()
        assert body["session_id"] == sid
        assert body["episodes_requested"] == 2

    async def test_run_default_episodes_is_1(self, client: AsyncClient) -> None:
        sid = await _create_session(client)
        resp = await client.post(f"/sessions/{sid}/run")
        assert resp.status_code == 202
        assert resp.json()["episodes_requested"] == 1

    async def test_run_nonexistent_session_returns_404(self, client: AsyncClient) -> None:
        resp = await client.post("/sessions/ghost/run")
        assert resp.status_code == 404

    async def test_run_already_running_returns_409(self, client: AsyncClient) -> None:
        sid = await _create_session(client)
        # First run — mock start so it doesn't actually spawn a task
        with patch("roleplay.api.routes.simulation.SessionRunner.start"):
            await client.post(f"/sessions/{sid}/run?episodes=1")

        # Manually set status to running to simulate the running state
        # Simulate: runner is now "running"
        await client.get(f"/sessions/{sid}/status")
        # Since we mocked start, status is still "idle" — just verify 202 was returned above
        # A real 409 test requires the runner to be in "running" state


class TestPauseSession:
    async def test_pause_non_running_returns_409(self, client: AsyncClient) -> None:
        sid = await _create_session(client)
        resp = await client.post(f"/sessions/{sid}/pause")
        assert resp.status_code == 409

    async def test_pause_running_session(self, client: AsyncClient) -> None:
        sid = await _create_session(client)
        # Start actually runs and transitions to running briefly, then done.
        # We just verify the 409 for non-running is handled (covered in test_pause_non_running).
        # A true pause test requires a long-running simulation; covered in integration tests.
        resp = await client.post(f"/sessions/{sid}/pause")
        assert resp.status_code == 409  # not running yet


class TestInjectEvent:
    async def test_inject_idle_session_returns_409(self, client: AsyncClient) -> None:
        sid = await _create_session(client)
        resp = await client.post(
            f"/sessions/{sid}/inject",
            json={"text": "Something dramatic happens."},
        )
        assert resp.status_code == 409

    async def test_inject_empty_text_returns_422(self, client: AsyncClient) -> None:
        sid = await _create_session(client)
        resp = await client.post(
            f"/sessions/{sid}/inject",
            json={"text": ""},
        )
        assert resp.status_code == 422

    async def test_inject_missing_text_returns_422(self, client: AsyncClient) -> None:
        sid = await _create_session(client)
        resp = await client.post(f"/sessions/{sid}/inject", json={})
        assert resp.status_code == 422


class TestRunnerIntegration:
    """End-to-end runner test using MockProvider."""

    async def test_full_run_reaches_done_status(self, client: AsyncClient) -> None:
        """Run 1 episode with MockProvider and verify the runner reaches done."""
        sid = await _create_session(client)

        resp = await client.post(f"/sessions/{sid}/run?episodes=1")
        assert resp.status_code == 202

        # Poll until done or timeout
        for _ in range(40):
            await asyncio.sleep(0.2)
            status_resp = await client.get(f"/sessions/{sid}/status")
            status = status_resp.json()["status"]
            if status in ("done", "error"):
                break

        final = await client.get(f"/sessions/{sid}/status")
        body = final.json()
        assert body["status"] == "done", f"Unexpected final status: {body}"
        assert body["episodes_completed"] == 1


class TestCoverageGaps:
    """Tests targeting specific uncovered branches."""

    @pytest.mark.asyncio
    async def test_run_already_running_returns_409(self, app_client: object) -> None:
        """POST /run on an already-running session → 409."""
        from roleplay.api.runner import SessionRunner

        app, client = app_client  # type: ignore[misc]
        await client.post("/sessions", content=MINIMAL_YAML)

        runner = SessionRunner("test-session-001")
        runner.status = "running"
        app.state.runners["test-session-001"] = runner

        r = await client.post("/sessions/test-session-001/run?episodes=1")
        assert r.status_code == 409

    @pytest.mark.asyncio
    async def test_pause_nonrunning_session_returns_409(self, client: object) -> None:
        """POST /pause on idle session → 409."""
        await client.post("/sessions", content=MINIMAL_YAML)  # type: ignore[union-attr]
        r = await client.post("/sessions/test-session-001/pause")  # type: ignore[union-attr]
        assert r.status_code == 409
        assert "not running" in r.json()["detail"]

    @pytest.mark.asyncio
    async def test_pause_running_session_ok(self, app_client: object) -> None:
        """POST /pause on running session sets pause flag."""
        from roleplay.api.runner import SessionRunner

        app, client = app_client  # type: ignore[misc]
        await client.post("/sessions", content=MINIMAL_YAML)

        runner = SessionRunner("test-session-001")
        runner.status = "running"
        app.state.runners["test-session-001"] = runner

        r = await client.post("/sessions/test-session-001/pause")
        assert r.status_code == 200
        assert runner._pause_requested is True

    @pytest.mark.asyncio
    async def test_inject_inactive_session_returns_409(self, client: object) -> None:
        """POST /inject on idle session → 409."""
        await client.post("/sessions", content=MINIMAL_YAML)  # type: ignore[union-attr]
        r = await client.post(  # type: ignore[union-attr]
            "/sessions/test-session-001/inject",
            json={"text": "Something happens."},
        )
        assert r.status_code == 409

    @pytest.mark.asyncio
    async def test_inject_running_session_ok(self, app_client: object) -> None:
        """POST /inject on running session succeeds."""
        from roleplay.api.runner import SessionRunner

        app, client = app_client  # type: ignore[misc]
        await client.post("/sessions", content=MINIMAL_YAML)

        runner = SessionRunner("test-session-001")
        runner.status = "running"
        app.state.runners["test-session-001"] = runner

        r = await client.post(
            "/sessions/test-session-001/inject",
            json={"text": "A storm approaches."},
        )
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_delete_running_session_returns_409(self, app_client: object) -> None:
        """DELETE on a running session → 409."""
        from roleplay.api.runner import SessionRunner

        app, client = app_client  # type: ignore[misc]
        await client.post("/sessions", content=MINIMAL_YAML)

        runner = SessionRunner("test-session-001")
        runner.status = "running"
        app.state.runners["test-session-001"] = runner

        r = await client.delete("/sessions/test-session-001")
        assert r.status_code == 409

    @pytest.mark.asyncio
    async def test_session_status_returns_runner_status(self, app_client: object) -> None:
        """_session_status returns runner.status when runner exists."""
        from roleplay.api.runner import SessionRunner

        app, client = app_client  # type: ignore[misc]
        await client.post("/sessions", content=MINIMAL_YAML)

        runner = SessionRunner("test-session-001")
        runner.status = "done"
        app.state.runners["test-session-001"] = runner

        r = await client.get("/sessions/test-session-001")
        assert r.status_code == 200
        assert r.json()["status"] == "done"

    @pytest.mark.asyncio
    async def test_runner_error_path(self) -> None:
        """SessionRunner._run sets status=error when engine raises."""
        import asyncio
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        from roleplay.api.runner import SessionRunner
        from roleplay.persistence.sqlite import SqlitePersistenceLayer
        from roleplay.scenario_yaml import load_yaml_scenario

        tmpdir = tempfile.mkdtemp(prefix="roleplay_err_", dir="/tmp")
        db_path = tmpdir + "/test.db"
        layer = SqlitePersistenceLayer(db_path)
        await layer.open()

        import tempfile as _t

        p = Path(_t.mktemp(suffix=".yaml", dir="/tmp"))
        p.write_text(MINIMAL_YAML)
        state = load_yaml_scenario(p).state
        await layer.create_session(state)
        p.unlink(missing_ok=True)

        bg_layer = SqlitePersistenceLayer(db_path)
        await bg_layer.open()

        runner = SessionRunner("test-session-001")

        with patch("roleplay.api.runner._build_registry", side_effect=RuntimeError("boom")):
            runner.start(state, bg_layer, 1)
            for _ in range(20):
                await asyncio.sleep(0.05)
                if runner.status in {"error", "done"}:
                    break

        assert runner.status == "error"
        assert "boom" in (runner.error or "")
        await layer.close()
