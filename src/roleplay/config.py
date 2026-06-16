"""Scenario TOML loader — **deprecated**.

.. deprecated::
   This module is used only by ``poc.py``, which has been superseded by the
   Stage 7 CLI (``roleplay run <scenario.yaml>``).
   For new scenarios use :mod:`roleplay.scenario_yaml` with a ``.yaml`` file.
   This module will be removed when ``poc.py`` is retired.

Also provides :func:`load_env_file` for loading API keys from a ``.env`` file;
 that helper remains useful and is not deprecated.
"""

from __future__ import annotations

import logging
import os
import tomllib
from typing import TYPE_CHECKING, Any

from roleplay.core.episode import NoopClock, RoundRobinScheduler, SimulationHistory
from roleplay.core.party import make_environment, make_organization, make_person
from roleplay.core.simulation_state import SimulationConfig, SimulationState

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# .env file loader
# ---------------------------------------------------------------------------


def load_env_file(path: Path) -> None:
    """Load ``KEY=VALUE`` pairs from *path* into :data:`os.environ`.

    * Lines starting with ``#`` and blank lines are ignored.
    * Surrounding single or double quotes on values are stripped.
    * Existing environment variables are **not** overwritten — real env vars
      always take precedence over the file, so ``GEMINI_API_KEY=x python …``
      beats whatever is in ``.env``.
    * Missing file is silently ignored (no error).

    Args:
        path: Path to the ``.env`` file (e.g. ``Path(".env")``).
    """
    if not path.exists():
        logger.debug(".env file not found at %s — skipping", path)
        return

    loaded = 0
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
            loaded += 1

    if loaded:
        logger.debug("Loaded %d variable(s) from %s", loaded, path)
    else:
        logger.debug("No new variables loaded from %s (all already set)", path)


# ---------------------------------------------------------------------------
# TOML scenario loader
# ---------------------------------------------------------------------------


def load_scenario(path: Path) -> tuple[SimulationState, str, int]:
    """Parse a TOML scenario file and return ``(state, provider_name, max_episodes)``.

    See ``scenarios/example.toml`` for the full schema.

    Args:
        path: Path to the ``.toml`` scenario file.

    Returns:
        A 3-tuple of:

        * :class:`~roleplay.core.simulation_state.SimulationState` built from
          the file
        * Provider name: ``"gemini"``, ``"claude"``, or ``"mock"``
        * Default episode count from ``[simulation] episodes``

    Raises:
        FileNotFoundError: If *path* does not exist.
        tomllib.TOMLDecodeError: If the file is not valid TOML.
        KeyError: If a required field (e.g. ``id``, ``name``) is missing.
    """
    with path.open("rb") as f:
        data: dict[str, Any] = tomllib.load(f)

    sim: dict[str, Any] = data.get("simulation", {})
    provider_name: str = str(sim.get("provider", "gemini"))
    max_episodes: int = int(sim.get("episodes", 3))

    config = SimulationConfig(
        session_id=str(sim.get("session_id", "session-001")),
        context_window_episodes=int(sim.get("context_window_episodes", 5)),
        memory_max_entries=int(sim.get("memory_max_entries", 20)),
        environment_reactive=bool(sim.get("environment_reactive", True)),
        auto_checkpoint=bool(sim.get("auto_checkpoint", False)),
        goal=str(sim.get("goal", "")),
    )

    parties: dict[str, Any] = {}
    for party_data in data.get("parties", []):
        pid: str = str(party_data["id"])
        kind: str = str(party_data.get("kind", "person"))
        name: str = str(party_data["name"])
        description: str = str(party_data.get("description", ""))
        goals: tuple[str, ...] = tuple(str(g) for g in party_data.get("goals", []))
        traits: tuple[str, ...] = tuple(str(t) for t in party_data.get("traits", []))
        knowledge: tuple[str, ...] = tuple(str(k) for k in party_data.get("knowledge", []))
        constraints: tuple[str, ...] = tuple(str(c) for c in party_data.get("constraints", []))

        if kind == "organization":
            parties[pid] = make_organization(
                pid,
                name,
                description,
                goals=goals,
                traits=traits,
                knowledge=knowledge,
                constraints=constraints,
            )
        else:
            parties[pid] = make_person(
                pid,
                name,
                description,
                goals=goals,
                traits=traits,
                knowledge=knowledge,
                constraints=constraints,
            )

    env_data: dict[str, Any] = data.get("environment", {})
    environment = make_environment(
        str(env_data.get("id", "env")),
        str(env_data.get("name", "Environment")),
        setting=str(env_data.get("setting", "")),
        facts=tuple(str(f) for f in env_data.get("facts", [])),
        initial_state={str(k): v for k, v in env_data.get("initial_state", {}).items()},
    )

    state = SimulationState(
        config=config,
        parties=parties,
        environment=environment,
        history=SimulationHistory(),
        scheduler=RoundRobinScheduler(),
        clock=NoopClock(),
    )

    logger.info(
        "Loaded scenario %r: %d parties, provider=%s, episodes=%d",
        path.name,
        len(parties),
        provider_name,
        max_episodes,
    )
    return state, provider_name, max_episodes
