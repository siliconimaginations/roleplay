"""Tests for roleplay.config — load_env_file and load_scenario."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from roleplay.config import load_env_file, load_scenario
from roleplay.core.party import PartyKind
from roleplay.core.simulation_state import SimulationState

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MINIMAL_TOML = """\
[[parties]]
id   = "alice"
name = "Alice"

[[parties]]
id   = "bob"
name = "Bob"

[environment]
id   = "env"
name = "Test Environment"
"""

_FULL_TOML = """\
[simulation]
session_id              = "test-001"
provider                = "claude"
episodes                = 7
context_window_episodes = 3
memory_max_entries      = 10
environment_reactive    = false
auto_checkpoint         = true

[[parties]]
id          = "alice"
kind        = "person"
name        = "Alice"
description = "A negotiator"
goals       = ["Win", "Be fair"]
traits      = ["calm"]
knowledge   = ["Knows the rules"]
constraints = ["No cheating"]

[[parties]]
id   = "acme"
kind = "organization"
name = "Acme Corp"

[environment]
id      = "office"
name    = "The Office"
setting = "A corporate meeting room"
facts   = ["Coffee is available", "The projector works"]

[environment.initial_state]
"time.simulated" = "Day 1"
"weather.condition" = "indoor"
"""


# ---------------------------------------------------------------------------
# load_env_file
# ---------------------------------------------------------------------------


class TestLoadEnvFile:
    def test_loads_key_value(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        env.write_text("MY_TEST_KEY=hello\n")
        os.environ.pop("MY_TEST_KEY", None)
        load_env_file(env)
        assert os.environ.get("MY_TEST_KEY") == "hello"
        del os.environ["MY_TEST_KEY"]

    def test_strips_double_quotes(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        env.write_text('QUOTED_KEY="double"\n')
        os.environ.pop("QUOTED_KEY", None)
        load_env_file(env)
        assert os.environ.get("QUOTED_KEY") == "double"
        del os.environ["QUOTED_KEY"]

    def test_strips_single_quotes(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        env.write_text("SINGLE_KEY='single'\n")
        os.environ.pop("SINGLE_KEY", None)
        load_env_file(env)
        assert os.environ.get("SINGLE_KEY") == "single"
        del os.environ["SINGLE_KEY"]

    def test_does_not_override_existing(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        env.write_text("EXISTING_KEY=from_file\n")
        os.environ["EXISTING_KEY"] = "from_env"
        load_env_file(env)
        assert os.environ["EXISTING_KEY"] == "from_env"
        del os.environ["EXISTING_KEY"]

    def test_skips_comments_and_blanks(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        env.write_text("# comment\n\nREAL_KEY=value\n# another\n")
        os.environ.pop("REAL_KEY", None)
        load_env_file(env)
        assert os.environ.get("REAL_KEY") == "value"
        del os.environ["REAL_KEY"]

    def test_missing_file_is_silent(self, tmp_path: Path) -> None:
        load_env_file(tmp_path / "nonexistent.env")  # must not raise

    def test_multiple_keys(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        env.write_text("KEY_A=aaa\nKEY_B=bbb\n")
        for k in ("KEY_A", "KEY_B"):
            os.environ.pop(k, None)
        load_env_file(env)
        assert os.environ.get("KEY_A") == "aaa"
        assert os.environ.get("KEY_B") == "bbb"
        del os.environ["KEY_A"], os.environ["KEY_B"]


# ---------------------------------------------------------------------------
# load_scenario — minimal TOML
# ---------------------------------------------------------------------------


class TestLoadScenarioMinimal:
    def test_returns_three_tuple(self, tmp_path: Path) -> None:
        f = tmp_path / "s.toml"
        f.write_text(_MINIMAL_TOML)
        result = load_scenario(f)
        assert isinstance(result, tuple) and len(result) == 3

    def test_default_provider_is_gemini(self, tmp_path: Path) -> None:
        f = tmp_path / "s.toml"
        f.write_text(_MINIMAL_TOML)
        _, provider, _ = load_scenario(f)
        assert provider == "gemini"

    def test_default_episodes_is_3(self, tmp_path: Path) -> None:
        f = tmp_path / "s.toml"
        f.write_text(_MINIMAL_TOML)
        _, _, episodes = load_scenario(f)
        assert episodes == 3

    def test_parties_loaded(self, tmp_path: Path) -> None:
        f = tmp_path / "s.toml"
        f.write_text(_MINIMAL_TOML)
        state, _, _ = load_scenario(f)
        assert isinstance(state, SimulationState)
        assert "alice" in state.parties
        assert "bob" in state.parties

    def test_environment_loaded(self, tmp_path: Path) -> None:
        f = tmp_path / "s.toml"
        f.write_text(_MINIMAL_TOML)
        state, _, _ = load_scenario(f)
        assert state.environment.id == "env"
        assert state.environment.kind is PartyKind.ENVIRONMENT


# ---------------------------------------------------------------------------
# load_scenario — full TOML
# ---------------------------------------------------------------------------


class TestLoadScenarioFull:
    def _load(self, tmp_path: Path) -> tuple[SimulationState, str, int]:
        f = tmp_path / "full.toml"
        f.write_text(_FULL_TOML)
        return load_scenario(f)

    def test_provider_from_file(self, tmp_path: Path) -> None:
        _, provider, _ = self._load(tmp_path)
        assert provider == "claude"

    def test_episodes_from_file(self, tmp_path: Path) -> None:
        _, _, episodes = self._load(tmp_path)
        assert episodes == 7

    def test_session_id(self, tmp_path: Path) -> None:
        state, _, _ = self._load(tmp_path)
        assert state.config.session_id == "test-001"

    def test_context_window(self, tmp_path: Path) -> None:
        state, _, _ = self._load(tmp_path)
        assert state.config.context_window_episodes == 3

    def test_environment_reactive_false(self, tmp_path: Path) -> None:
        state, _, _ = self._load(tmp_path)
        assert state.config.environment_reactive is False

    def test_person_party_loaded(self, tmp_path: Path) -> None:
        state, _, _ = self._load(tmp_path)
        alice = state.parties["alice"]
        assert alice.name == "Alice"
        assert alice.kind is PartyKind.PERSON
        assert "Win" in alice.persona.goals
        assert "calm" in alice.persona.traits

    def test_organization_party_loaded(self, tmp_path: Path) -> None:
        state, _, _ = self._load(tmp_path)
        acme = state.parties["acme"]
        assert acme.name == "Acme Corp"
        assert acme.kind is PartyKind.ORGANIZATION

    def test_environment_setting(self, tmp_path: Path) -> None:
        state, _, _ = self._load(tmp_path)
        assert "meeting room" in state.environment.persona.description

    def test_environment_initial_state(self, tmp_path: Path) -> None:
        state, _, _ = self._load(tmp_path)
        assert state.environment.get_state("weather.condition") == "indoor"

    def test_environment_facts(self, tmp_path: Path) -> None:
        state, _, _ = self._load(tmp_path)
        assert "Coffee is available" in state.environment.persona.knowledge

    def test_get_party_resolves_environment(self, tmp_path: Path) -> None:
        state, _, _ = self._load(tmp_path)
        env = state.get_party("office")
        assert env.kind is PartyKind.ENVIRONMENT

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_scenario(tmp_path / "missing.toml")
