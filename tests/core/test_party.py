"""Tests for src/roleplay/core/party.py."""

from __future__ import annotations

import warnings

import pytest

from roleplay.core.party import (
    Party,
    PartyKind,
    Persona,
    StateChange,
    make_environment,
    make_organization,
    make_person,
    validate_environment_state,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _alice() -> Party:
    return make_person(
        "alice",
        "Alice",
        "A retired schoolteacher.",
        goals=("Tend the garden",),
        traits=("warm", "stubborn"),
        knowledge=("She knows the mayor.",),
        constraints=("Never lies.",),
    )


def _world() -> Party:
    return make_environment(
        "world",
        "Millhaven",
        "A small town in 1987.",
        facts=("Population ~800.",),
        initial_state={
            "time.simulated": "Monday 09:00",
            "time.episode": 0,
            "weather.condition": "sunny",
        },
    )


# ---------------------------------------------------------------------------
# Persona
# ---------------------------------------------------------------------------


class TestPersona:
    def test_defaults(self) -> None:
        p = Persona(description="A wanderer.")
        assert p.goals == ()
        assert p.traits == ()
        assert p.knowledge == ()
        assert p.constraints == ()

    def test_frozen(self) -> None:
        p = Persona(description="X")
        with pytest.raises((AttributeError, TypeError)):
            p.description = "Y"  # type: ignore[misc]

    def test_all_fields(self) -> None:
        p = Persona(
            description="D",
            goals=("g",),
            traits=("t",),
            knowledge=("k",),
            constraints=("c",),
        )
        assert p.goals == ("g",)


# ---------------------------------------------------------------------------
# StateChange
# ---------------------------------------------------------------------------


class TestStateChange:
    def test_namedtuple_fields(self) -> None:
        sc = StateChange(
            key="mood",
            old_value=None,
            new_value="happy",
            episode_index=1,
            reason="promotion",
        )
        assert sc.key == "mood"
        assert sc.old_value is None
        assert sc.new_value == "happy"
        assert sc.episode_index == 1
        assert sc.reason == "promotion"

    def test_no_reason(self) -> None:
        sc = StateChange("x", "a", "b", 0, None)
        assert sc.reason is None


# ---------------------------------------------------------------------------
# Party construction
# ---------------------------------------------------------------------------


class TestPartyConstruction:
    def test_make_person(self) -> None:
        p = make_person("bob", "Bob", "A harbour master.")
        assert p.id == "bob"
        assert p.kind is PartyKind.PERSON
        assert p.persona.description == "A harbour master."
        assert p.state == {}
        assert p.state_history == []

    def test_make_organization(self) -> None:
        org = make_organization("acme", "Acme Corp", "A widget maker.")
        assert org.kind is PartyKind.ORGANIZATION

    def test_make_environment(self) -> None:
        env = _world()
        assert env.kind is PartyKind.ENVIRONMENT
        assert env.state["time.simulated"] == "Monday 09:00"
        assert env.state["time.episode"] == 0

    def test_make_environment_initial_state_copied(self) -> None:
        initial: dict[str, object] = {"time.episode": 0}
        env = make_environment("w", "World", "desc", initial_state={"time.episode": 0})
        env.state["time.episode"] = 1
        assert initial["time.episode"] == 0  # original dict not mutated

    def test_created_at_is_utc(self) -> None:
        p = make_person("x", "X", "desc")
        assert p.created_at.tzinfo is not None

    def test_persona_kwargs_forwarded(self) -> None:
        p = make_person(
            "alice",
            "Alice",
            "desc",
            goals=("goal1",),
            traits=("brave",),
        )
        assert p.persona.goals == ("goal1",)
        assert p.persona.traits == ("brave",)


# ---------------------------------------------------------------------------
# apply_state_update
# ---------------------------------------------------------------------------


class TestApplyStateUpdate:
    def test_basic_update(self) -> None:
        p = _alice()
        changes = p.apply_state_update({"mood": "happy"}, episode_index=1)
        assert p.state["mood"] == "happy"
        assert len(changes) == 1
        assert changes[0].key == "mood"
        assert changes[0].new_value == "happy"
        assert changes[0].old_value is None
        assert changes[0].episode_index == 1

    def test_multiple_keys(self) -> None:
        p = _alice()
        changes = p.apply_state_update({"mood": "sad", "location": "home"}, episode_index=0)
        assert len(changes) == 2
        assert p.state["mood"] == "sad"
        assert p.state["location"] == "home"

    def test_old_value_recorded(self) -> None:
        p = _alice()
        p.apply_state_update({"mood": "neutral"}, episode_index=0)
        changes = p.apply_state_update({"mood": "happy"}, episode_index=1)
        assert changes[0].old_value == "neutral"

    def test_reason_recorded(self) -> None:
        p = _alice()
        changes = p.apply_state_update({"mood": "excited"}, episode_index=2, reason="won award")
        assert changes[0].reason == "won award"

    def test_history_is_appended(self) -> None:
        p = _alice()
        p.apply_state_update({"a": 1}, episode_index=0)
        p.apply_state_update({"a": 2}, episode_index=1)
        assert len(p.state_history) == 2

    def test_all_state_value_types(self) -> None:
        p = _alice()
        p.apply_state_update(
            {"s": "text", "i": 42, "f": 3.14, "b": True, "n": None},
            episode_index=0,
        )
        assert p.state["s"] == "text"
        assert p.state["i"] == 42
        assert p.state["f"] == pytest.approx(3.14)
        assert p.state["b"] is True
        assert p.state["n"] is None

    def test_invalid_value_raises(self) -> None:
        p = _alice()
        with pytest.raises(ValueError, match="list"):
            p.apply_state_update({"bad": [1, 2, 3]}, episode_index=0)  # type: ignore[dict-item]

    def test_invalid_rejects_all(self) -> None:
        """If one value is invalid the whole batch is rejected."""
        p = _alice()
        with pytest.raises(ValueError):
            p.apply_state_update({"ok": "yes", "bad": {"nested": True}}, episode_index=0)  # type: ignore[dict-item]
        assert "ok" not in p.state

    def test_empty_update(self) -> None:
        p = _alice()
        changes = p.apply_state_update({}, episode_index=0)
        assert changes == []

    def test_bool_not_confused_with_int(self) -> None:
        """bool is a subtype of int — both must be accepted."""
        p = _alice()
        p.apply_state_update({"flag": True, "count": 0}, episode_index=0)
        assert p.state["flag"] is True
        assert p.state["count"] == 0


# ---------------------------------------------------------------------------
# get_state / state_snapshot
# ---------------------------------------------------------------------------


class TestStateAccessors:
    def test_get_state_present(self) -> None:
        p = _alice()
        p.apply_state_update({"mood": "calm"}, episode_index=0)
        assert p.get_state("mood") == "calm"

    def test_get_state_missing_default_none(self) -> None:
        p = _alice()
        assert p.get_state("missing") is None

    def test_get_state_custom_default(self) -> None:
        p = _alice()
        assert p.get_state("missing", "fallback") == "fallback"

    def test_state_snapshot_is_copy(self) -> None:
        p = _alice()
        p.apply_state_update({"x": 1}, episode_index=0)
        snap = p.state_snapshot()
        snap["x"] = 999
        assert p.state["x"] == 1  # original unchanged


# ---------------------------------------------------------------------------
# replace_persona
# ---------------------------------------------------------------------------


class TestReplacePersona:
    def test_returns_new_party(self) -> None:
        alice = _alice()
        alice2 = alice.replace_persona(goals=("New goal",))
        assert alice2 is not alice

    def test_persona_updated(self) -> None:
        alice = _alice()
        alice2 = alice.replace_persona(goals=("New goal",), traits=("bold",))
        assert alice2.persona.goals == ("New goal",)
        assert alice2.persona.traits == ("bold",)

    def test_original_unchanged(self) -> None:
        alice = _alice()
        _ = alice.replace_persona(goals=("New goal",))
        assert alice.persona.goals == ("Tend the garden",)

    def test_state_preserved(self) -> None:
        alice = _alice()
        alice.apply_state_update({"mood": "happy"}, episode_index=0)
        alice2 = alice.replace_persona(description="Updated desc")
        assert alice2.state["mood"] == "happy"

    def test_invalid_field_raises(self) -> None:
        alice = _alice()
        with pytest.raises(TypeError, match="unexpected keyword argument"):
            alice.replace_persona(nonexistent_field="value")


# ---------------------------------------------------------------------------
# to_prompt_context
# ---------------------------------------------------------------------------


class TestToPromptContext:
    def test_person_includes_name_and_kind(self) -> None:
        alice = _alice()
        ctx = alice.to_prompt_context()
        assert "Alice" in ctx
        assert "person" in ctx

    def test_person_includes_description(self) -> None:
        alice = _alice()
        assert "retired schoolteacher" in alice.to_prompt_context()

    def test_person_includes_goals(self) -> None:
        alice = _alice()
        ctx = alice.to_prompt_context()
        assert "Tend the garden" in ctx

    def test_person_includes_traits(self) -> None:
        alice = _alice()
        ctx = alice.to_prompt_context()
        assert "warm" in ctx
        assert "stubborn" in ctx

    def test_person_includes_knowledge(self) -> None:
        alice = _alice()
        assert "She knows the mayor" in alice.to_prompt_context()

    def test_person_includes_constraints(self) -> None:
        alice = _alice()
        assert "Never lies" in alice.to_prompt_context()

    def test_person_includes_state_by_default(self) -> None:
        alice = _alice()
        alice.apply_state_update({"mood": "content"}, episode_index=0)
        assert "mood: content" in alice.to_prompt_context()

    def test_person_omits_state_when_flagged(self) -> None:
        alice = _alice()
        alice.apply_state_update({"mood": "content"}, episode_index=0)
        assert "mood" not in alice.to_prompt_context(include_state=False)

    def test_person_empty_sections_omitted(self) -> None:
        p = make_person("x", "X", "Just a person.")
        ctx = p.to_prompt_context()
        assert "Goals:" not in ctx
        assert "Traits:" not in ctx

    def test_environment_header(self) -> None:
        world = _world()
        ctx = world.to_prompt_context()
        assert "World: Millhaven" in ctx
        assert "environment" in ctx

    def test_environment_includes_facts(self) -> None:
        world = _world()
        assert "Population ~800" in world.to_prompt_context()

    def test_environment_includes_state(self) -> None:
        world = _world()
        ctx = world.to_prompt_context()
        assert "time.simulated: Monday 09:00" in ctx

    def test_environment_omits_goals_header(self) -> None:
        world = _world()
        assert "Goals:" not in world.to_prompt_context()


# ---------------------------------------------------------------------------
# validate_environment_state
# ---------------------------------------------------------------------------


class TestValidateEnvironmentState:
    def test_valid_keys_no_warnings(self) -> None:
        state = {
            "time.simulated": "Day 1",
            "time.episode": 0,
            "weather.condition": "sunny",
            "weather.temp_c": 22,
            "loc.alice.place": "town_square",
            "loc.alice.visible_to": "all",
            "obj.key_item.place": "chest",
            "obj.key_item.visible_to": "alice",
            "event.current": "festival",
        }
        assert validate_environment_state(state) == []

    def test_unknown_key_generates_warning(self) -> None:
        warnings = validate_environment_state({"random_key": "value"})
        assert len(warnings) == 1
        assert "random_key" in warnings[0]

    def test_multiple_unknown_keys(self) -> None:
        warnings = validate_environment_state({"a": 1, "b": 2})
        assert len(warnings) == 2

    def test_empty_state_no_warnings(self) -> None:
        assert validate_environment_state({}) == []

    def test_make_environment_warns_on_bad_keys(self) -> None:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            make_environment("w", "W", "desc", initial_state={"bad_key": "val"})
        assert any("bad_key" in str(w.message) for w in caught)
