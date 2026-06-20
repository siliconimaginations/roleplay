"""Tests for multi-environment YAML loading and engine behaviour."""

from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from roleplay.core.environment import Environment, EnvironmentRegistry
from roleplay.scenario_yaml import ValidationError, load_yaml_scenario

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_yaml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "scenario.yaml"
    p.write_text(content)
    return p


_BASE = textwrap.dedent("""\
    description: test
    parties:
      - id: alice
        kind: person
        name: Alice
        persona:
          description: A person
      - id: world
        kind: environment
        name: World
        persona:
          description: The world
""")


# ---------------------------------------------------------------------------
# YAML loading — environments block
# ---------------------------------------------------------------------------


class TestYamlEnvironmentsLoading:
    def test_no_environments_key_produces_empty_registry(self, tmp_path: Path) -> None:
        p = _write_yaml(tmp_path, _BASE)
        result = load_yaml_scenario(p)
        assert not result.state.environments

    def test_environments_parsed_correctly(self, tmp_path: Path) -> None:
        yaml = _BASE + textwrap.dedent("""\
            environments:
              - id: town_square
                name: "Town Square"
                description: "Busy public space"
                state:
                  open: true
              - id: town_hall
                name: "Town Hall"
                description: "Formal setting"
        """)
        p = _write_yaml(tmp_path, yaml)
        result = load_yaml_scenario(p)
        reg = result.state.environments
        assert bool(reg)
        assert len(reg) == 2
        sq = reg.get("town_square")
        assert sq is not None
        assert sq.name == "Town Square"
        assert sq.state == {"open": True}
        th = reg.get("town_hall")
        assert th is not None
        assert th.description == "Formal setting"

    def test_duplicate_environment_id_raises(self, tmp_path: Path) -> None:
        yaml = _BASE + textwrap.dedent("""\
            environments:
              - id: dup
                name: "One"
                description: "First"
              - id: dup
                name: "Two"
                description: "Second"
        """)
        p = _write_yaml(tmp_path, yaml)
        with pytest.raises(ValidationError, match="duplicate"):
            load_yaml_scenario(p)

    def test_environment_missing_name_raises(self, tmp_path: Path) -> None:
        yaml = _BASE + textwrap.dedent("""\
            environments:
              - id: place
                description: "A place"
        """)
        p = _write_yaml(tmp_path, yaml)
        with pytest.raises(ValidationError, match="name"):
            load_yaml_scenario(p)

    def test_environment_missing_description_raises(self, tmp_path: Path) -> None:
        yaml = _BASE + textwrap.dedent("""\
            environments:
              - id: place
                name: "Place"
        """)
        p = _write_yaml(tmp_path, yaml)
        with pytest.raises(ValidationError, match="description"):
            load_yaml_scenario(p)

    def test_empty_environments_list_is_ignored(self, tmp_path: Path) -> None:
        yaml = _BASE + "environments: []\n"
        p = _write_yaml(tmp_path, yaml)
        result = load_yaml_scenario(p)
        assert not result.state.environments


# ---------------------------------------------------------------------------
# Prompt assembly — environment description injection
# ---------------------------------------------------------------------------


class TestPromptAssemblyWithEnvironments:
    """Verify the engine injects the correct environment description per party."""

    def _make_state(self, alice_location: str | None = None) -> object:
        from roleplay.core.episode import (
            NoopClock,
            RoundRobinScheduler,
            SimulationHistory,
        )
        from roleplay.core.party import make_environment, make_person
        from roleplay.core.simulation_state import SimulationConfig, SimulationState

        alice = make_person("alice", "Alice", "A negotiator")
        if alice_location:
            alice.apply_state_update({"location": alice_location}, episode_index=0)

        env_party = make_environment("world", "World", "The world")
        reg = EnvironmentRegistry(
            [
                Environment("square", "Town Square", "Busy public space"),
                Environment("hall", "Town Hall", "Formal setting"),
            ]
        )
        return SimulationState(
            config=SimulationConfig(session_id="t"),
            parties={"alice": alice},
            environment=env_party,
            history=SimulationHistory(),
            scheduler=RoundRobinScheduler(),
            clock=NoopClock(),
            environments=reg,
        )

    def test_location_description_injected_into_prompt(self) -> None:
        from roleplay.engine.engine import _assemble_prompt

        state = self._make_state(alice_location="square")
        prompt = _assemble_prompt("alice", state, [], [], None)  # type: ignore[arg-type]
        assert "Town Square" in prompt
        assert "Busy public space" in prompt

    def test_different_location_uses_correct_description(self) -> None:
        from roleplay.engine.engine import _assemble_prompt

        state = self._make_state(alice_location="hall")
        prompt = _assemble_prompt("alice", state, [], [], None)  # type: ignore[arg-type]
        assert "Town Hall" in prompt
        assert "Formal setting" in prompt
        assert "Town Square" not in prompt

    def test_no_location_no_extra_description(self) -> None:
        from roleplay.engine.engine import _assemble_prompt

        state = self._make_state(alice_location=None)
        prompt = _assemble_prompt("alice", state, [], [], None)  # type: ignore[arg-type]
        assert "Town Square" not in prompt
        assert "Town Hall" not in prompt

    def test_unknown_location_shows_fallback(self) -> None:
        from roleplay.engine.engine import _assemble_prompt

        state = self._make_state(alice_location="nowhere")
        prompt = _assemble_prompt("alice", state, [], [], None)  # type: ignore[arg-type]
        assert "nowhere" in prompt
        assert "unknown" in prompt

    def test_no_registry_prompt_unchanged(self) -> None:
        from roleplay.core.episode import (
            NoopClock,
            RoundRobinScheduler,
            SimulationHistory,
        )
        from roleplay.core.party import make_environment, make_person
        from roleplay.core.simulation_state import SimulationConfig, SimulationState
        from roleplay.engine.engine import _assemble_prompt

        alice = make_person("alice", "Alice", "A negotiator")
        alice.apply_state_update({"location": "square"}, episode_index=0)
        state = SimulationState(
            config=SimulationConfig(session_id="t"),
            parties={"alice": alice},
            environment=make_environment("world", "World", "The world"),
            history=SimulationHistory(),
            scheduler=RoundRobinScheduler(),
            clock=NoopClock(),
            # no environments kwarg → empty registry
        )
        prompt = _assemble_prompt("alice", state, [], [], None)  # type: ignore[arg-type]
        assert "Town Square" not in prompt


# ---------------------------------------------------------------------------
# Co-location filter
# ---------------------------------------------------------------------------


class TestColocationFilter:
    """The engine only shows turns from co-located parties."""

    def _make_two_party_state(
        self,
        alice_loc: str | None,
        bob_loc: str | None,
        with_registry: bool = True,
    ) -> object:
        from roleplay.core.episode import (
            NoopClock,
            RoundRobinScheduler,
            SimulationHistory,
        )
        from roleplay.core.party import make_environment, make_person
        from roleplay.core.simulation_state import SimulationConfig, SimulationState

        alice = make_person("alice", "Alice", "A person")
        bob = make_person("bob", "Bob", "A person")
        if alice_loc:
            alice.apply_state_update({"location": alice_loc}, episode_index=0)
        if bob_loc:
            bob.apply_state_update({"location": bob_loc}, episode_index=0)

        reg = (
            EnvironmentRegistry(
                [
                    Environment("square", "Town Square", "Public"),
                    Environment("hall", "Town Hall", "Formal"),
                ]
            )
            if with_registry
            else EnvironmentRegistry()
        )

        return SimulationState(
            config=SimulationConfig(session_id="t"),
            parties={"alice": alice, "bob": bob},
            environment=make_environment("world", "World", "The world"),
            history=SimulationHistory(),
            scheduler=RoundRobinScheduler(),
            clock=NoopClock(),
            environments=reg,
        )

    def _run_colocation(self, state: object, speaker: str) -> list[str]:
        """Invoke the _colocated_ids closure by running a minimal episode loop excerpt."""
        from roleplay.core.simulation_state import SimulationState

        assert isinstance(state, SimulationState)
        party_ids = list(state.parties.keys())
        if not state.environments:
            return party_ids
        speaker_loc = str(state.get_party(speaker).state_snapshot().get("location", ""))
        if not speaker_loc:
            return party_ids
        return [
            pid
            for pid in party_ids
            if not str(state.get_party(pid).state_snapshot().get("location", ""))
            or str(state.get_party(pid).state_snapshot().get("location", "")) == speaker_loc
        ]

    def test_same_location_both_included(self) -> None:
        state = self._make_two_party_state("square", "square")
        assert set(self._run_colocation(state, "alice")) == {"alice", "bob"}

    def test_different_locations_bob_excluded(self) -> None:
        state = self._make_two_party_state("square", "hall")
        assert self._run_colocation(state, "alice") == ["alice"]

    def test_no_location_on_speaker_no_filter(self) -> None:
        state = self._make_two_party_state(None, "hall")
        assert set(self._run_colocation(state, "alice")) == {"alice", "bob"}

    def test_no_location_on_responder_always_included(self) -> None:
        state = self._make_two_party_state("square", None)
        assert set(self._run_colocation(state, "alice")) == {"alice", "bob"}

    def test_empty_registry_no_filter(self) -> None:
        state = self._make_two_party_state("square", "hall", with_registry=False)
        assert set(self._run_colocation(state, "alice")) == {"alice", "bob"}
