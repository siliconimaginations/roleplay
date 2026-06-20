"""Tests for the YAML scenario loader."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from roleplay.core.episode import (
    FixedOrderScheduler,
    FormattedIncrementClock,
    NoopClock,
    RandomOrderScheduler,
    RoundRobinScheduler,
)
from roleplay.core.party import PartyKind
from roleplay.scenario_yaml import ScenarioResult, ValidationError, load_yaml_scenario

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "scenario.yaml"
    p.write_text(content, encoding="utf-8")
    return p


_MINIMAL = """\
session_id: test-session
config:
  default_provider: mock
  max_episodes: 2
parties:
  - id: alice
    kind: person
    name: Alice
    persona:
      description: A sharp person
      goals: [find the truth]
    state:
      mood: neutral
  - id: town
    kind: environment
    name: The Town
    persona:
      description: A quiet town
"""

_NO_SESSION_ID = """\
parties:
  - id: alice
    kind: person
    name: Alice
  - id: env
    kind: environment
    name: Env
"""

# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_load_returns_scenario_result(tmp_path: Path) -> None:
    assert isinstance(load_yaml_scenario(_write(tmp_path, _MINIMAL)), ScenarioResult)


def test_session_id_preserved(tmp_path: Path) -> None:
    result = load_yaml_scenario(_write(tmp_path, _MINIMAL))
    assert result.state.config.session_id == "test-session"


def test_session_id_auto_generated(tmp_path: Path) -> None:
    result = load_yaml_scenario(_write(tmp_path, _NO_SESSION_ID))
    assert len(result.state.config.session_id) > 8


def test_provider_name(tmp_path: Path) -> None:
    assert load_yaml_scenario(_write(tmp_path, _MINIMAL)).provider_name == "mock"


def test_max_episodes(tmp_path: Path) -> None:
    assert load_yaml_scenario(_write(tmp_path, _MINIMAL)).max_episodes == 2


def test_max_episodes_none_when_omitted(tmp_path: Path) -> None:
    yaml = _MINIMAL.replace("  max_episodes: 2\n", "")
    assert load_yaml_scenario(_write(tmp_path, yaml)).max_episodes is None


def test_parties_loaded(tmp_path: Path) -> None:
    result = load_yaml_scenario(_write(tmp_path, _MINIMAL))
    assert "alice" in result.state.parties
    assert result.state.parties["alice"].kind is PartyKind.PERSON


def test_environment_loaded(tmp_path: Path) -> None:
    result = load_yaml_scenario(_write(tmp_path, _MINIMAL))
    assert result.state.environment.id == "town"
    assert result.state.environment.kind is PartyKind.ENVIRONMENT


def test_party_initial_state(tmp_path: Path) -> None:
    result = load_yaml_scenario(_write(tmp_path, _MINIMAL))
    assert result.state.parties["alice"].state.get("mood") == "neutral"


def test_party_persona_fields(tmp_path: Path) -> None:
    result = load_yaml_scenario(_write(tmp_path, _MINIMAL))
    persona = result.state.parties["alice"].persona
    assert "find the truth" in persona.goals
    assert persona.description == "A sharp person"


def test_organization_party(tmp_path: Path) -> None:
    yaml = (
        _MINIMAL
        + "  - id: guild\n    kind: organization\n    name: The Guild\n"
        + "    persona:\n      description: A merchant guild\n"
    )
    result = load_yaml_scenario(_write(tmp_path, yaml))
    assert result.state.parties["guild"].kind is PartyKind.ORGANIZATION


def test_party_name(tmp_path: Path) -> None:
    result = load_yaml_scenario(_write(tmp_path, _MINIMAL))
    assert result.state.parties["alice"].name == "Alice"


# ---------------------------------------------------------------------------
# Scheduler variants
# ---------------------------------------------------------------------------


def test_scheduler_default_round_robin(tmp_path: Path) -> None:
    assert isinstance(
        load_yaml_scenario(_write(tmp_path, _MINIMAL)).state.scheduler, RoundRobinScheduler
    )


def test_scheduler_random_order(tmp_path: Path) -> None:
    yaml = _MINIMAL + "scheduler:\n  kind: random_order\n"
    assert isinstance(
        load_yaml_scenario(_write(tmp_path, yaml)).state.scheduler, RandomOrderScheduler
    )


def test_scheduler_fixed_order(tmp_path: Path) -> None:
    yaml = _MINIMAL + "scheduler:\n  kind: fixed\n  order: [alice]\n"
    assert isinstance(
        load_yaml_scenario(_write(tmp_path, yaml)).state.scheduler, FixedOrderScheduler
    )


# ---------------------------------------------------------------------------
# Clock variants
# ---------------------------------------------------------------------------


def test_clock_default_noop(tmp_path: Path) -> None:
    assert isinstance(load_yaml_scenario(_write(tmp_path, _MINIMAL)).state.clock, NoopClock)


def test_clock_formatted_increment(tmp_path: Path) -> None:
    clock_yaml = (
        "clock:\n  kind: formatted_increment\n  unit: hours\n"
        '  amount: 2\n  format: "%Y-%m-%d %H:%M"\n'
    )
    yaml = _MINIMAL + clock_yaml
    assert isinstance(
        load_yaml_scenario(_write(tmp_path, yaml)).state.clock, FormattedIncrementClock
    )


# ---------------------------------------------------------------------------
# Config fields
# ---------------------------------------------------------------------------


def test_config_fields(tmp_path: Path) -> None:
    yaml = """\
session_id: cfg-test
config:
  default_provider: gemini
  context_window_episodes: 7
  memory_max_entries: 30
  forgetting_enabled: true
parties:
  - id: alice
    kind: person
    name: Alice
  - id: env
    kind: environment
    name: Env
"""
    cfg = load_yaml_scenario(_write(tmp_path, yaml)).state.config
    assert cfg.context_window_episodes == 7
    assert cfg.memory_max_entries == 30
    assert cfg.forgetting_enabled is True


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------


def test_no_parties_raises(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="must not be empty"):
        load_yaml_scenario(_write(tmp_path, "parties: []\n"))


def test_no_env_party_synthesises_world(tmp_path: Path) -> None:
    """No kind=environment party: a synthetic 'World' environment is created."""
    yaml = "parties:\n  - id: alice\n    kind: person\n    name: Alice\n"
    result = load_yaml_scenario(_write(tmp_path, yaml))
    assert result.state.environment is not None
    assert result.state.environment.kind.value == "environment"
    assert result.state.environment.id == "world"


def test_no_env_party_uses_description(tmp_path: Path) -> None:
    """description field is used as setting for the synthesised environment."""
    yaml = (
        "description: 'A rainy London afternoon'\n"
        "parties:\n  - id: alice\n    kind: person\n    name: Alice\n"
    )
    result = load_yaml_scenario(_write(tmp_path, yaml))
    assert "London" in result.state.environment.persona.description


def test_environments_only_no_env_party(tmp_path: Path) -> None:
    """YAML with only environments: list and no kind=environment party is valid."""
    yaml = (
        "environments:\n"
        "  - id: hall\n    name: Hall\n    description: A long corridor.\n"
        "parties:\n"
        "  - id: alice\n    kind: person\n    name: Alice\n"
        "    state:\n      location: hall\n"
    )
    result = load_yaml_scenario(_write(tmp_path, yaml))
    assert result.state.environment.id == "world"
    assert len(result.state.environments) == 1


def test_multiple_environments_raises(tmp_path: Path) -> None:
    yaml = (
        "parties:\n"
        "  - id: env1\n    kind: environment\n    name: Env1\n"
        "  - id: env2\n    kind: environment\n    name: Env2\n"
    )
    with pytest.raises(ValidationError, match=r"Only one party with kind=.environment"):
        load_yaml_scenario(_write(tmp_path, yaml))


def test_invalid_scheduler_kind_raises(tmp_path: Path) -> None:
    yaml = _MINIMAL + "scheduler:\n  kind: turntable\n"
    with pytest.raises(ValidationError, match=r"scheduler\.kind"):
        load_yaml_scenario(_write(tmp_path, yaml))


def test_fixed_scheduler_without_order_raises(tmp_path: Path) -> None:
    yaml = _MINIMAL + "scheduler:\n  kind: fixed\n  order: []\n"
    with pytest.raises(ValidationError, match="non-empty"):
        load_yaml_scenario(_write(tmp_path, yaml))


def test_invalid_clock_kind_raises(tmp_path: Path) -> None:
    yaml = _MINIMAL + "clock:\n  kind: sundial\n"
    with pytest.raises(ValidationError, match=r"clock\.kind"):
        load_yaml_scenario(_write(tmp_path, yaml))


def test_lambda_clock_raises(tmp_path: Path) -> None:
    yaml = _MINIMAL + "clock:\n  kind: lambda\n"
    with pytest.raises(ValidationError, match="lambda"):
        load_yaml_scenario(_write(tmp_path, yaml))


def test_file_not_found_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_yaml_scenario(tmp_path / "nonexistent.yaml")


def test_unknown_top_level_key_no_exception(tmp_path: Path) -> None:
    yaml = _MINIMAL + "unknown_key: value\n"
    # should NOT raise — the key is just logged as warning
    result = load_yaml_scenario(_write(tmp_path, yaml))
    assert result is not None


def test_unknown_top_level_key_logs_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    yaml = _MINIMAL + "unknown_key: value\n"
    with caplog.at_level(logging.WARNING, logger="roleplay.scenario_yaml"):
        load_yaml_scenario(_write(tmp_path, yaml))
    assert any("unknown_key" in r.message for r in caplog.records)


def test_missing_party_id_raises(tmp_path: Path) -> None:
    yaml = (
        "parties:\n"
        "  - kind: person\n    name: Alice\n"
        "  - id: env\n    kind: environment\n    name: Env\n"
    )
    with pytest.raises(ValidationError, match="missing required field 'id'"):
        load_yaml_scenario(_write(tmp_path, yaml))


# ---------------------------------------------------------------------------
# Tool import
# ---------------------------------------------------------------------------


def test_bad_tool_handler_raises(tmp_path: Path) -> None:
    yaml = _MINIMAL + "tools:\n  - name: broken\n    handler: roleplay.nonexistent.module.fn\n"
    with pytest.raises(ImportError):
        load_yaml_scenario(_write(tmp_path, yaml))


# ---------------------------------------------------------------------------
# Coverage gap tests
# ---------------------------------------------------------------------------


def test_environments_missing_id_raises(tmp_path: Path) -> None:
    """Line 116: environments entry with no 'id' → ValidationError."""
    yaml = _MINIMAL + "environments:\n  - name: Hall\n    description: A corridor.\n"
    with pytest.raises(ValidationError) as exc_info:
        load_yaml_scenario(_write(tmp_path, yaml))
    assert "requires an 'id' field" in str(exc_info.value)


def test_organization_party_with_initial_state(tmp_path: Path) -> None:
    """Line 278: organization + state → apply_state_update called."""
    yaml = (
        _MINIMAL
        + "  - id: guild\n    kind: organization\n    name: The Guild\n"
        + "    persona:\n      description: A merchant guild\n"
        + "    state:\n      mood: cheerful\n"
    )
    result = load_yaml_scenario(_write(tmp_path, yaml))
    party = result.state.parties["guild"]
    assert party.kind is PartyKind.ORGANIZATION
    assert any(c.key == "mood" for c in party.state_history)


def test_non_callable_handler_raises(tmp_path: Path) -> None:
    """Lines 186-189: attribute exists but is not callable → ImportError."""
    # os.sep is a string attribute — importable but not callable
    yaml = _MINIMAL + "tools:\n  - name: bad\n    handler: os.sep\n"
    with pytest.raises(ImportError, match="is not callable"):
        load_yaml_scenario(_write(tmp_path, yaml))


def test_tool_with_no_handler_path_skipped(tmp_path: Path) -> None:
    """Line 327: tools entry with empty handler is silently skipped (continue)."""
    yaml = _MINIMAL + "tools:\n  - name: no-handler\n"
    result = load_yaml_scenario(_write(tmp_path, yaml))
    assert result is not None
