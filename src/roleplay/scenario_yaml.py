"""YAML scenario loader.

Reads a YAML file that describes parties, environment, simulation settings,
clock, scheduler, and tools.  Returns a fully initialised
:class:`~roleplay.core.simulation_state.SimulationState` ready to pass to
:class:`~roleplay.engine.engine.SimulationEngine`.

Typical usage::

    from pathlib import Path
    from roleplay.scenario_yaml import load_yaml_scenario

    state, provider_name, max_episodes = load_yaml_scenario(Path("town.yaml"))
"""

from __future__ import annotations

import importlib
import logging
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import yaml

from roleplay.core.environment import Environment, EnvironmentRegistry
from roleplay.core.episode import (
    FixedOrderScheduler,
    FormattedIncrementClock,
    NoopClock,
    RandomOrderScheduler,
    RoundRobinScheduler,
    SimulatedTimeClock,
    SimulationHistory,
    TurnScheduler,
)
from roleplay.core.party import make_environment, make_organization, make_person
from roleplay.core.simulation_state import SimulationConfig, SimulationState

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public result type
# ---------------------------------------------------------------------------


@dataclass
class ScenarioResult:
    """Everything needed to start a simulation from a YAML scenario file."""

    state: SimulationState
    provider_name: str
    max_episodes: int | None
    tool_handlers: dict[str, Callable[..., Any]] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


@dataclass
class ValidationError(Exception):
    """One or more scenario validation errors."""

    errors: list[str]

    def __str__(self) -> str:
        bullet = "\n  - "
        return "Scenario validation failed:" + bullet + bullet.join(self.errors)


def _collect_errors(data: dict[str, Any]) -> list[str]:
    """Return all validation errors found in *data* (empty list = valid)."""
    errors: list[str] = []

    parties: list[dict[str, Any]] = data.get("parties", [])
    if not parties:
        errors.append("'parties' list is required and must not be empty")

    env_parties = [p for p in parties if p.get("kind") == "environment"]
    if len(env_parties) > 1:
        errors.append(
            f"Only one party with kind='environment' is allowed (found {len(env_parties)}). "
            "Named locations belong in the top-level 'environments:' list, not as extra "
            "kind=environment parties."
        )

    for p in parties:
        if "id" not in p:
            errors.append(f"Party is missing required field 'id': {p!r}")
        if "name" not in p:
            pid = p.get("id", "<unknown>")
            errors.append(f"Party '{pid}' is missing required field 'name'")

    scheduler = data.get("scheduler", {})
    sched_kind = scheduler.get("kind", "round_robin")
    valid_schedulers = {"round_robin", "random_order", "fixed"}
    if sched_kind not in valid_schedulers:
        errors.append(
            f"scheduler.kind must be one of {sorted(valid_schedulers)!r}; got {sched_kind!r}"
        )
    if sched_kind == "fixed":
        order = scheduler.get("order", [])
        if not order:
            errors.append("scheduler.kind='fixed' requires a non-empty 'order' list")

    env_ids = [e.get("id") for e in data.get("environments", []) if e.get("id")]
    if len(env_ids) != len(set(env_ids)):
        errors.append("environments: duplicate 'id' values are not allowed")
    for e in data.get("environments", []):
        if not e.get("id"):
            errors.append(f"environments: each entry requires an 'id' field: {e!r}")
        if not e.get("name"):
            errors.append(f"environments: entry {e.get('id', '<unknown>')!r} is missing 'name'")
        if not e.get("description"):
            errors.append(
                f"environments: entry {e.get('id', '<unknown>')!r} is missing 'description'"
            )

    clock = data.get("clock", {})
    clock_kind = clock.get("kind", "noop")
    valid_clocks = {"noop", "formatted_increment"}
    if clock_kind not in valid_clocks:
        if clock_kind == "lambda":
            errors.append(
                "clock.kind='lambda' is not supported from YAML (only from the Python API)"
            )
        else:
            errors.append(f"clock.kind must be one of {sorted(valid_clocks)!r}; got {clock_kind!r}")

    return errors


# ---------------------------------------------------------------------------
# Sub-loaders
# ---------------------------------------------------------------------------


def _build_scheduler(scheduler_data: dict[str, Any]) -> TurnScheduler:
    kind = scheduler_data.get("kind", "round_robin")
    if kind == "random_order":
        seed = scheduler_data.get("seed")
        return RandomOrderScheduler(seed=seed)
    if kind == "fixed":
        return FixedOrderScheduler(order=list(scheduler_data["order"]))
    return RoundRobinScheduler()


def _build_clock(clock_data: dict[str, Any]) -> SimulatedTimeClock:
    kind = clock_data.get("kind", "noop")
    if kind == "formatted_increment":
        return FormattedIncrementClock(
            unit=str(clock_data.get("unit", "hours")),
            amount=int(clock_data.get("amount", 1)),
            fmt=str(clock_data.get("format", "%Y-%m-%d %H:%M")),
        )
    return NoopClock()


def _build_persona_kwargs(persona: dict[str, Any]) -> dict[str, Any]:
    """Extract persona sub-dict into keyword args for make_person/make_org/make_env."""
    return {
        "description": str(persona.get("description", "")),
        "goals": tuple(str(g) for g in persona.get("goals", [])),
        "traits": tuple(str(t) for t in persona.get("traits", [])),
        "knowledge": tuple(str(k) for k in persona.get("knowledge", [])),
        "constraints": tuple(str(c) for c in persona.get("constraints", [])),
    }


def _import_handler(dotted: str) -> Callable[..., Any]:
    """Import an async callable by dotted path.  Raises ImportError on failure."""
    from typing import cast

    module_path, _, attr = dotted.rpartition(".")
    if not module_path:
        raise ImportError(
            f"Handler path {dotted!r} must be a dotted path "
            "(e.g. 'roleplay.tools.builtin.mock_search')"
        )
    mod = importlib.import_module(module_path)
    fn = getattr(mod, attr)
    if not callable(fn):
        raise ImportError(f"Handler {dotted!r} is not callable")
    return cast("Callable[..., Any]", fn)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def load_yaml_scenario(path: Path) -> ScenarioResult:
    """Parse a YAML scenario file and return a :class:`ScenarioResult`.

    Args:
        path: Path to the ``.yaml`` scenario file.

    Returns:
        A :class:`ScenarioResult` with fully initialised state.

    Raises:
        FileNotFoundError: If *path* does not exist.
        yaml.YAMLError: If the file is not valid YAML.
        :class:`ValidationError`: If the scenario is structurally invalid.
        ImportError: If a tool handler cannot be imported.
    """
    raw = path.read_text(encoding="utf-8")
    data: dict[str, Any] = yaml.safe_load(raw) or {}

    # Warn on unknown top-level keys (forward-compatibility)
    known_keys = {
        "session_id",
        "description",
        "config",
        "parties",
        "environments",
        "clock",
        "scheduler",
        "tools",
    }
    for key in data:
        if key not in known_keys:
            logger.warning("Unknown top-level key in scenario YAML: %r", key)

    errors = _collect_errors(data)
    if errors:
        raise ValidationError(errors)

    # ── Config ───────────────────────────────────────────────────────────────
    cfg_raw: dict[str, Any] = data.get("config", {})
    display_name = str(data.get("session_id", "") or "").strip()
    session_id = str(uuid.uuid4())
    provider_name = str(cfg_raw.get("default_provider", "gemini"))
    max_episodes: int | None = int(cfg_raw["max_episodes"]) if "max_episodes" in cfg_raw else None

    config = SimulationConfig(
        session_id=session_id,
        display_name=display_name,
        context_window_episodes=int(cfg_raw.get("context_window_episodes", 10)),
        memory_max_entries=int(cfg_raw.get("memory_max_entries", 20)),
        memory_char_budget=int(cfg_raw.get("memory_char_budget", 4000)),
        memory_write_mode=str(cfg_raw.get("memory_write_mode", "template")),
        compaction_threshold=int(cfg_raw.get("compaction_threshold", 200)),
        forgetting_enabled=bool(cfg_raw.get("forgetting_enabled", False)),
        default_provider=provider_name,
        default_model=str(cfg_raw.get("default_model", "")),
        environment_reactive=bool(cfg_raw.get("environment_reactive", True)),
        auto_checkpoint=bool(cfg_raw.get("auto_checkpoint", True)),
        passive_observation_parties=list(cfg_raw.get("passive_observation_parties", [])),
        goal=str(cfg_raw.get("goal", "")),
    )

    # ── Parties ───────────────────────────────────────────────────────────────
    parties: dict[str, Any] = {}
    environment = None

    for p in data.get("parties", []):
        pid = str(p["id"])
        kind = str(p.get("kind", "person"))
        name = str(p["name"])
        persona = _build_persona_kwargs(p.get("persona", {}))
        initial_state: dict[str, Any] = dict(p.get("state", {}))

        if kind == "environment":
            environment = make_environment(
                pid,
                name,
                setting=persona["description"],
                facts=persona["knowledge"],
                initial_state=initial_state,
            )
        elif kind == "organization":
            party = make_organization(pid, name, **persona)
            if initial_state:
                party.apply_state_update(initial_state, episode_index=0)
            parties[pid] = party
        else:  # person (default)
            party = make_person(pid, name, **persona)
            if initial_state:
                party.apply_state_update(initial_state, episode_index=0)
            parties[pid] = party

    # If no kind=environment party was declared, synthesise a minimal one so that
    # scenarios relying solely on the top-level ``environments:`` list still work.
    if environment is None:
        description_text = str(data.get("description", "")) or "The world of this simulation."
        environment = make_environment(
            "world",
            "World",
            setting=description_text,
        )

    # ── Environments ─────────────────────────────────────────────────────────
    named_environments: list[Environment] = []
    for e in data.get("environments", []):
        # Entries missing id/name/description are caught by _collect_errors;
        # skip them here so a partial error list doesn't cause a KeyError.
        env_id = e.get("id")
        env_name = e.get("name")
        env_desc = e.get("description")
        if env_id and env_name and env_desc:
            named_environments.append(
                Environment(
                    id=str(env_id),
                    name=str(env_name),
                    description=str(env_desc),
                    state={str(k): v for k, v in e.get("state", {}).items()},
                )
            )
    env_registry = EnvironmentRegistry(named_environments)

    # ── Clock & Scheduler ────────────────────────────────────────────────────
    scheduler = _build_scheduler(data.get("scheduler", {}))
    clock = _build_clock(data.get("clock", {}))

    # ── State ────────────────────────────────────────────────────────────────
    state = SimulationState(
        config=config,
        parties=parties,
        environment=environment,
        history=SimulationHistory(),
        scheduler=scheduler,
        clock=clock,
        environments=env_registry,
    )

    # ── Tools ────────────────────────────────────────────────────────────────
    tool_handlers: dict[str, Callable[..., Any]] = {}
    for tool in data.get("tools", []):
        handler_path = str(tool.get("handler", ""))
        if not handler_path:
            continue
        tool_name = str(tool.get("name", handler_path))
        try:
            tool_handlers[tool_name] = _import_handler(handler_path)
        except (ImportError, AttributeError) as exc:
            raise ImportError(
                f"Failed to import tool handler {handler_path!r} for tool '{tool_name}': {exc}"
            ) from exc

    logger.info(
        "Loaded YAML scenario %r: %d parties, provider=%s",
        path.name,
        len(parties),
        provider_name,
    )

    return ScenarioResult(
        state=state,
        provider_name=provider_name,
        max_episodes=max_episodes,
        tool_handlers=tool_handlers,
    )
