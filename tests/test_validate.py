"""Tests for roleplay.validate — scenario TOML validator."""

from __future__ import annotations

from pathlib import Path

from roleplay.validate import ValidationError, ValidationResult, validate_scenario

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write(tmp_path: Path, content: str) -> Path:
    f = tmp_path / "scenario.toml"
    f.write_text(content)
    return f


def _field_names(result: ValidationResult) -> list[str]:
    return [e.field for e in result.errors]


# ---------------------------------------------------------------------------
# Valid scenarios
# ---------------------------------------------------------------------------


class TestValidScenarios:
    def test_minimal_valid(self, tmp_path: Path) -> None:
        p = _write(
            tmp_path,
            """
[[parties]]
id   = "alice"
name = "Alice"

[environment]
id   = "env"
name = "Env"
""",
        )
        r = validate_scenario(p)
        assert r.valid
        assert r.party_count == 1
        assert r.provider == "gemini"
        assert r.episodes == 3

    def test_full_valid(self, tmp_path: Path) -> None:
        p = _write(
            tmp_path,
            """
[simulation]
session_id = "s1"
provider   = "claude"
episodes   = 7

[[parties]]
id          = "alice"
kind        = "person"
name        = "Alice"
description = "A negotiator"
goals       = ["Win"]
traits      = ["calm"]
knowledge   = ["Rules"]
constraints = ["Budget"]

[[parties]]
id   = "acme"
kind = "organization"
name = "Acme"

[environment]
id      = "office"
name    = "Office"
setting = "A meeting room"
facts   = ["Coffee is ready"]

[environment.initial_state]
"time.simulated"    = "Day 1"
"weather.condition" = "clear"
"event.mood"        = "tense"
""",
        )
        r = validate_scenario(p)
        assert r.valid, [str(e) for e in r.errors]
        assert r.provider == "claude"
        assert r.episodes == 7

    def test_mock_provider_valid(self, tmp_path: Path) -> None:
        p = _write(
            tmp_path,
            """
[simulation]
provider = "mock"

[[parties]]
id   = "x"
name = "X"

[environment]
id   = "e"
name = "E"
""",
        )
        assert validate_scenario(p).valid

    def test_example_toml_is_valid(self) -> None:
        """The committed scenarios/example.toml must always pass validation."""
        example = Path(__file__).parent.parent / "scenarios" / "example.toml"
        r = validate_scenario(example)
        assert r.valid, [str(e) for e in r.errors]


# ---------------------------------------------------------------------------
# File-level errors
# ---------------------------------------------------------------------------


class TestFileErrors:
    def test_missing_file(self, tmp_path: Path) -> None:
        r = validate_scenario(tmp_path / "missing.toml")
        assert not r.valid
        assert "file" in _field_names(r)

    def test_invalid_toml(self, tmp_path: Path) -> None:
        p = _write(tmp_path, "[[broken\n")
        r = validate_scenario(p)
        assert not r.valid
        assert "toml" in _field_names(r)


# ---------------------------------------------------------------------------
# [simulation] errors
# ---------------------------------------------------------------------------


class TestSimulationErrors:
    def test_invalid_provider(self, tmp_path: Path) -> None:
        p = _write(
            tmp_path,
            """
[simulation]
provider = "openai"

[[parties]]
id = "a"
name = "A"

[environment]
id = "e"
name = "E"
""",
        )
        r = validate_scenario(p)
        assert not r.valid
        assert "simulation.provider" in _field_names(r)

    def test_episodes_zero(self, tmp_path: Path) -> None:
        p = _write(
            tmp_path,
            """
[simulation]
episodes = 0

[[parties]]
id = "a"
name = "A"

[environment]
id = "e"
name = "E"
""",
        )
        r = validate_scenario(p)
        assert not r.valid
        assert "simulation.episodes" in _field_names(r)

    def test_episodes_negative(self, tmp_path: Path) -> None:
        p = _write(
            tmp_path,
            """
[simulation]
episodes = -1

[[parties]]
id = "a"
name = "A"

[environment]
id = "e"
name = "E"
""",
        )
        r = validate_scenario(p)
        assert not r.valid

    def test_episodes_string_is_error(self, tmp_path: Path) -> None:
        p = _write(
            tmp_path,
            """
[simulation]
episodes = "five"

[[parties]]
id = "a"
name = "A"

[environment]
id = "e"
name = "E"
""",
        )
        r = validate_scenario(p)
        assert not r.valid
        assert "simulation.episodes" in _field_names(r)


# ---------------------------------------------------------------------------
# [[parties]] errors
# ---------------------------------------------------------------------------


class TestPartyErrors:
    def test_no_parties(self, tmp_path: Path) -> None:
        p = _write(
            tmp_path,
            """
[environment]
id   = "e"
name = "E"
""",
        )
        r = validate_scenario(p)
        assert not r.valid
        assert "parties" in _field_names(r)

    def test_missing_id(self, tmp_path: Path) -> None:
        p = _write(
            tmp_path,
            """
[[parties]]
name = "Alice"

[environment]
id = "e"
name = "E"
""",
        )
        r = validate_scenario(p)
        assert not r.valid
        assert any("id" in f for f in _field_names(r))

    def test_missing_name(self, tmp_path: Path) -> None:
        p = _write(
            tmp_path,
            """
[[parties]]
id = "alice"

[environment]
id = "e"
name = "E"
""",
        )
        r = validate_scenario(p)
        assert not r.valid
        assert any("name" in f for f in _field_names(r))

    def test_invalid_kind(self, tmp_path: Path) -> None:
        p = _write(
            tmp_path,
            """
[[parties]]
id   = "alice"
name = "Alice"
kind = "robot"

[environment]
id = "e"
name = "E"
""",
        )
        r = validate_scenario(p)
        assert not r.valid
        assert any("kind" in f for f in _field_names(r))

    def test_duplicate_id(self, tmp_path: Path) -> None:
        p = _write(
            tmp_path,
            """
[[parties]]
id   = "alice"
name = "Alice"

[[parties]]
id   = "alice"
name = "Alice 2"

[environment]
id = "e"
name = "E"
""",
        )
        r = validate_scenario(p)
        assert not r.valid
        assert any("id" in f for f in _field_names(r))

    def test_goals_not_list_of_strings(self, tmp_path: Path) -> None:
        p = _write(
            tmp_path,
            """
[[parties]]
id    = "alice"
name  = "Alice"
goals = "win"

[environment]
id = "e"
name = "E"
""",
        )
        r = validate_scenario(p)
        assert not r.valid
        assert any("goals" in f for f in _field_names(r))

    def test_error_message_mentions_party_id(self, tmp_path: Path) -> None:
        p = _write(
            tmp_path,
            """
[[parties]]
id   = "bob"
name = "Bob"
kind = "alien"

[environment]
id = "e"
name = "E"
""",
        )
        r = validate_scenario(p)
        assert any("bob" in e.message for e in r.errors)


# ---------------------------------------------------------------------------
# [environment] errors
# ---------------------------------------------------------------------------


class TestEnvironmentErrors:
    def test_missing_environment(self, tmp_path: Path) -> None:
        p = _write(
            tmp_path,
            """
[[parties]]
id   = "a"
name = "A"
""",
        )
        r = validate_scenario(p)
        assert not r.valid
        assert "environment" in _field_names(r)

    def test_missing_env_id(self, tmp_path: Path) -> None:
        p = _write(
            tmp_path,
            """
[[parties]]
id   = "a"
name = "A"

[environment]
name = "E"
""",
        )
        r = validate_scenario(p)
        assert not r.valid
        assert any("environment.id" in f for f in _field_names(r))

    def test_missing_env_name(self, tmp_path: Path) -> None:
        p = _write(
            tmp_path,
            """
[[parties]]
id   = "a"
name = "A"

[environment]
id = "e"
""",
        )
        r = validate_scenario(p)
        assert not r.valid
        assert any("environment.name" in f for f in _field_names(r))

    def test_facts_not_list_of_strings(self, tmp_path: Path) -> None:
        p = _write(
            tmp_path,
            """
[[parties]]
id   = "a"
name = "A"

[environment]
id    = "e"
name  = "E"
facts = "one fact"
""",
        )
        r = validate_scenario(p)
        assert not r.valid
        assert any("facts" in f for f in _field_names(r))


# ---------------------------------------------------------------------------
# [environment.initial_state] errors
# ---------------------------------------------------------------------------


class TestInitialStateErrors:
    def test_unquoted_dotted_key_detected(self, tmp_path: Path) -> None:
        """Unquoted dotted TOML key creates a nested dict — must be caught."""
        p = _write(
            tmp_path,
            """
[[parties]]
id   = "a"
name = "A"

[environment]
id   = "e"
name = "E"

[environment.initial_state]
weather.condition = "clear"
""",
        )
        r = validate_scenario(p)
        assert not r.valid
        # The nested dict error should mention 'weather'
        assert any("weather" in e.message for e in r.errors)

    def test_unquoted_key_hint_mentions_quoting(self, tmp_path: Path) -> None:
        p = _write(
            tmp_path,
            """
[[parties]]
id   = "a"
name = "A"

[environment]
id   = "e"
name = "E"

[environment.initial_state]
time.simulated = "Day 1"
""",
        )
        r = validate_scenario(p)
        # Hint should guide towards the quoted-key fix
        assert any("quote" in e.hint.lower() or '"' in e.hint for e in r.errors)

    def test_list_value_rejected(self, tmp_path: Path) -> None:
        # Lists can't appear in initial_state — but TOML arrays are valid TOML
        # so we need to catch them at the validation layer
        p = _write(
            tmp_path,
            """
[[parties]]
id   = "a"
name = "A"

[environment]
id   = "e"
name = "E"

[environment.initial_state]
"event.tags" = ["urgent", "public"]
""",
        )
        r = validate_scenario(p)
        assert not r.valid
        assert any("event.tags" in e.field for e in r.errors)

    def test_valid_scalar_types_accepted(self, tmp_path: Path) -> None:
        p = _write(
            tmp_path,
            """
[[parties]]
id   = "a"
name = "A"

[environment]
id   = "e"
name = "E"

[environment.initial_state]
"time.simulated"  = "Day 1"
"event.count"     = 3
"weather.temp_c"  = 18.5
"event.raining"   = false
""",
        )
        r = validate_scenario(p)
        assert r.valid, [str(e) for e in r.errors]

    def test_unknown_key_family_is_warning_not_error(self, tmp_path: Path) -> None:
        p = _write(
            tmp_path,
            """
[[parties]]
id   = "a"
name = "A"

[environment]
id   = "e"
name = "E"

[environment.initial_state]
"unknown.key" = "value"
""",
        )
        r = validate_scenario(p)
        assert r.valid  # warning, not error
        assert len(r.warnings) > 0

    def test_multiple_errors_reported_together(self, tmp_path: Path) -> None:
        p = _write(
            tmp_path,
            """
[[parties]]
id   = "a"
name = "A"

[environment]
id   = "e"
name = "E"

[environment.initial_state]
"event.tags" = ["a", "b"]
weather.condition = "clear"
""",
        )
        r = validate_scenario(p)
        assert not r.valid
        assert len(r.errors) >= 2


# ---------------------------------------------------------------------------
# ValidationResult helpers
# ---------------------------------------------------------------------------


class TestValidationResult:
    def test_summary_valid(self, tmp_path: Path) -> None:
        p = _write(
            tmp_path,
            """
[[parties]]
id   = "a"
name = "A"

[environment]
id = "e"
name = "E"
""",
        )
        r = validate_scenario(p)
        assert r.valid
        s = r.summary()
        assert "✓" in s
        assert "1" in s  # party count

    def test_summary_invalid(self, tmp_path: Path) -> None:
        p = _write(
            tmp_path,
            """
[environment]
id = "e"
name = "E"
""",
        )
        r = validate_scenario(p)
        assert not r.valid
        assert "✗" in r.summary()

    def test_error_str_includes_field_and_message(self, tmp_path: Path) -> None:
        e = ValidationError(field="foo.bar", message="Something wrong", hint="Fix it")
        s = str(e)
        assert "foo.bar" in s
        assert "Something wrong" in s
        assert "Fix it" in s
