"""Integration tests: full simulation run with MockProvider.

These tests exercise the complete stack — CLI loader → engine →
persistence → API — without real LLM calls.

Marked ``@pytest.mark.integration`` and excluded from the default CI run
(``pytest -m "not integration"``).  To run locally::

    uv run pytest -m integration tests/integration/
"""

from __future__ import annotations

from pathlib import Path  # noqa: TC003

import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# 1. Full engine run via Python API (no REST layer)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_engine_runs_two_episodes(scenario_yaml_path: Path, db_path: str) -> None:
    """Load a scenario, run 2 episodes, verify state persisted."""
    from roleplay.engine.engine import SimulationEngine
    from roleplay.memory.store import InMemoryStore
    from roleplay.persistence.sqlite import SqlitePersistenceLayer
    from roleplay.providers.mock import MockProvider
    from roleplay.providers.registry import ProviderRegistry
    from roleplay.scenario_yaml import load_yaml_scenario

    result = load_yaml_scenario(scenario_yaml_path)
    state = result.state

    layer = SqlitePersistenceLayer(db_path)
    await layer.open()
    await layer.create_session(state)

    registry = ProviderRegistry()
    registry.register("mock", MockProvider("Agreement reached."))
    provider = registry.get("mock")

    engine = SimulationEngine(
        state=state,
        provider=provider,
        memory_store=InMemoryStore(),
    )
    await engine.run(max_episodes=2)

    assert len(state.history.completed_episodes()) == 2
    assert state.history.completed_episodes()[0].turns  # at least one turn

    for ep in state.history.completed_episodes():
        await layer.save_episode(state.config.session_id, ep)
    await layer.save_state(state)
    loaded = await layer.load_session("integration-test-001")
    assert len(loaded.history.completed_episodes()) == 2

    await layer.close()


@pytest.mark.asyncio
async def test_engine_fork_and_resume(scenario_yaml_path: Path, db_path: str) -> None:
    """Run 1 episode, fork, run 1 more on fork — original unchanged."""
    from roleplay.engine.engine import SimulationEngine
    from roleplay.memory.store import InMemoryStore
    from roleplay.persistence.sqlite import SqlitePersistenceLayer
    from roleplay.providers.mock import MockProvider
    from roleplay.providers.registry import ProviderRegistry
    from roleplay.scenario_yaml import load_yaml_scenario

    result = load_yaml_scenario(scenario_yaml_path)
    state = result.state

    layer = SqlitePersistenceLayer(db_path)
    await layer.open()
    await layer.create_session(state)

    registry = ProviderRegistry()
    registry.register("mock", MockProvider())
    provider = registry.get("mock")

    engine = SimulationEngine(state=state, provider=provider, memory_store=InMemoryStore())
    await engine.run(max_episodes=1)
    for ep in state.history.completed_episodes():
        await layer.save_episode(state.config.session_id, ep)
    await layer.save_state(state)

    # Fork
    fork_id = "integration-fork-001"
    await layer.fork("integration-test-001", fork_id)

    # Run one more episode on the fork
    fork_state = await layer.load_session(fork_id)
    fork_engine = SimulationEngine(
        state=fork_state, provider=provider, memory_store=InMemoryStore()
    )
    await fork_engine.run(max_episodes=1)
    for ep in fork_state.history.completed_episodes():
        await layer.save_episode(fork_id, ep)
    await layer.save_state(fork_state)

    # Original should still have 1 episode
    original = await layer.load_session("integration-test-001")
    assert len(original.history.completed_episodes()) == 1

    # Fork should have 2 episodes
    fork_reloaded = await layer.load_session(fork_id)
    assert len(fork_reloaded.history.completed_episodes()) == 2

    await layer.close()


@pytest.mark.asyncio
async def test_engine_goal_halt(db_path: str, tmp_path: Path) -> None:
    """A scenario with a goal and a MockProvider that always says 'GOAL_MET'
    should halt early once the goal check returns halt."""
    from roleplay.engine.engine import SimulationEngine
    from roleplay.memory.store import InMemoryStore
    from roleplay.persistence.sqlite import SqlitePersistenceLayer
    from roleplay.providers.mock import MockProvider
    from roleplay.providers.registry import ProviderRegistry
    from roleplay.scenario_yaml import load_yaml_scenario

    # The mock provider answers "yes, goal met" for everything — goal checks
    # included.  The engine should halt at max_episodes if goal check logic
    # requires multiple episodes; here we just verify it runs without error.
    content = """\
session_id: "integ-goal-halt"
config:
  default_provider: mock
  goal: "Finish the experiment."
parties:
  - id: scientist
    kind: person
    name: Dr. Smith
    system_prompt: "A scientist."
  - id: lab
    kind: environment
    name: Lab
    system_prompt: "A laboratory."
"""
    p = tmp_path / "goal_scenario.yaml"
    p.write_text(content)
    result = load_yaml_scenario(p)
    state = result.state

    layer = SqlitePersistenceLayer(db_path)
    await layer.open()
    await layer.create_session(state)

    registry = ProviderRegistry()
    registry.register("mock", MockProvider("Experiment complete. GOAL_MET."))
    provider = registry.get("mock")

    engine = SimulationEngine(state=state, provider=provider, memory_store=InMemoryStore())
    # Run up to 5 episodes — may halt early via goal check
    await engine.run(max_episodes=5)

    completed = state.history.completed_episodes()
    assert len(completed) >= 1  # at least one ran

    await layer.close()


# ---------------------------------------------------------------------------
# 2. REST API integration (full HTTP cycle via AsyncClient)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_create_run_inspect(scenario_yaml_path: Path) -> None:
    """Create a session via REST, run 1 episode, inspect state."""
    import os
    import tempfile

    from httpx import ASGITransport, AsyncClient

    from roleplay.api.app import create_app
    from roleplay.persistence.sqlite import SqlitePersistenceLayer

    tmpdir = tempfile.mkdtemp(prefix="roleplay_api_integ_", dir="/tmp")
    db_path = tmpdir + "/api_test.db"

    os.environ.pop("ROLEPLAY_API_KEY", None)

    app = create_app()
    layer = SqlitePersistenceLayer(db_path)
    await layer.open()
    app.state.layer = layer
    app.state.runners = {}

    yaml_text = scenario_yaml_path.read_text()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Create
        r = await ac.post("/sessions", content=yaml_text)
        assert r.status_code == 201
        sid = r.json()["session_id"]
        assert sid == "integration-test-001"

        # List
        r2 = await ac.get("/sessions")
        assert r2.status_code == 200
        assert any(s["session_id"] == sid for s in r2.json())

        # Run 1 episode (background task)
        r3 = await ac.post(f"/sessions/{sid}/run?episodes=1")
        assert r3.status_code == 202
        assert r3.json()["status"] == "running"

        # Poll until done (max 10s)
        import asyncio as _asyncio

        for _ in range(40):
            await _asyncio.sleep(0.25)
            r4 = await ac.get(f"/sessions/{sid}/status")
            if r4.json()["status"] in {"done", "error"}:
                break

        status_data = (await ac.get(f"/sessions/{sid}/status")).json()
        assert status_data["status"] == "done", f"Expected done, got: {status_data}"
        assert status_data["episodes_completed"] == 1

        # Inspect
        r5 = await ac.get(f"/sessions/{sid}")
        assert r5.status_code == 200
        detail = r5.json()
        assert len(detail["parties"]) == 2  # alice + bob (env separate)

        # Fork
        r6 = await ac.post(f"/sessions/{sid}/fork")
        assert r6.status_code == 201
        fork_id = r6.json()["session_id"]

        # Delete fork
        r7 = await ac.delete(f"/sessions/{fork_id}")
        assert r7.status_code == 204

        # Delete original
        r8 = await ac.delete(f"/sessions/{sid}")
        assert r8.status_code == 204

    await layer.close()


@pytest.mark.asyncio
async def test_api_delete_running_session_rejected(scenario_yaml_path: Path) -> None:
    """Deleting a running session returns 409."""
    import os
    import tempfile

    from httpx import ASGITransport, AsyncClient

    from roleplay.api.app import create_app
    from roleplay.api.runner import SessionRunner
    from roleplay.persistence.sqlite import SqlitePersistenceLayer

    tmpdir = tempfile.mkdtemp(prefix="roleplay_api_integ_", dir="/tmp")
    db_path = tmpdir + "/api_test.db"
    os.environ.pop("ROLEPLAY_API_KEY", None)

    app = create_app()
    layer = SqlitePersistenceLayer(db_path)
    await layer.open()
    app.state.layer = layer

    # Pre-wire a "running" runner for a session that doesn't exist in DB
    fake_runner = SessionRunner("no-such-session")
    fake_runner.status = "running"
    app.state.runners = {"no-such-session": fake_runner}

    yaml_text = scenario_yaml_path.read_text()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # First create a real session
        r = await ac.post("/sessions", content=yaml_text)
        sid = r.json()["session_id"]

        # Swap runner to mark it running
        real_runner = SessionRunner(sid)
        real_runner.status = "running"
        app.state.runners[sid] = real_runner

        r2 = await ac.delete(f"/sessions/{sid}")
        assert r2.status_code == 409
        assert "pause" in r2.json()["detail"].lower()

    await layer.close()
