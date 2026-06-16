"""Scenario validator — accepts YAML (preferred) and TOML (deprecated).

Parses a scenario file and returns structured errors with actionable messages.
Designed to be used directly and also to be AI-readable — error messages are
written so that pasting them back to an AI assistant is enough to get a fix.

YAML is the canonical format for new scenarios::

    uv run python -m roleplay.validate scenarios/my-scenario.yaml
    uv run python -m roleplay.validate scenarios/*.yaml

TOML files are still accepted but deprecated — migrate to YAML.
"""

from __future__ import annotations

import sys
import tomllib
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Valid values for [simulation] provider
_VALID_PROVIDERS = {"gemini", "claude", "mock"}

# Valid party kinds
_VALID_KINDS = {"person", "organization"}

# Types accepted as StateValue
_VALID_STATE_TYPES = (str, int, float, bool, type(None))


@dataclass
class ValidationError:
    """A single problem found in a scenario file."""

    field: str
    message: str
    hint: str = ""

    def __str__(self) -> str:
        parts = [f"  [{self.field}] {self.message}"]
        if self.hint:
            parts.append(f"    → {self.hint}")
        return "\n".join(parts)


@dataclass
class ValidationResult:
    """Outcome of validating one scenario file."""

    path: Path
    errors: list[ValidationError] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    # Populated only when validation succeeds
    party_count: int = 0
    provider: str = ""
    episodes: int = 0

    @property
    def valid(self) -> bool:
        return len(self.errors) == 0

    def summary(self) -> str:
        if self.valid:
            w = f", {len(self.warnings)} warning(s)" if self.warnings else ""
            return (
                f"✓  Valid — {self.party_count} party/parties, "
                f"provider={self.provider}, {self.episodes} episode(s){w}"
            )
        n = len(self.errors)
        plural = "error" if n == 1 else "errors"
        return f"✗  {n} {plural}"


def validate_scenario(path: Path) -> ValidationResult:
    """Parse and validate *path*; return a :class:`ValidationResult`.

    Never raises — all problems are captured as :class:`ValidationError`
    entries so callers can format them however they like.

    Args:
        path: Path to the scenario file (``.yaml`` preferred; ``.toml`` deprecated).
    """
    result = ValidationResult(path=path)

    # ── File existence ────────────────────────────────────────────────────
    if not path.exists():
        result.errors.append(
            ValidationError(
                field="file",
                message=f"File not found: {path}",
                hint="Check the path and try again.",
            )
        )
        return result

    # ── Parse: YAML (preferred) or TOML (deprecated) ─────────────────────
    suffix = path.suffix.lower()
    if suffix in {".yaml", ".yml"}:
        try:
            from roleplay.scenario_yaml import (
                ValidationError as YAMLValidationError,
            )
            from roleplay.scenario_yaml import (
                load_yaml_scenario,
            )

            scenario = load_yaml_scenario(path)
            # Populate summary fields from the loaded scenario.
            result.party_count = len(scenario.state.parties)
            result.provider = scenario.provider_name
            result.episodes = scenario.max_episodes or 0
            return result
        except YAMLValidationError as exc:
            for msg in exc.errors:
                result.errors.append(ValidationError(field="yaml", message=msg, hint=""))
            return result
        except Exception as exc:
            result.errors.append(
                ValidationError(
                    field="yaml",
                    message=f"Invalid YAML: {exc}",
                    hint="Validate with: uv run python -m roleplay.validate <file>.yaml",
                )
            )
            return result

    # TOML — deprecated; still supported for backwards compatibility
    warnings.warn(
        f"{path}: TOML scenario files are deprecated. "
        "Please migrate to YAML format (see scenarios/example.yaml).",
        DeprecationWarning,
        stacklevel=2,
    )
    try:
        with path.open("rb") as f:
            data: dict[str, Any] = tomllib.load(f)
    except tomllib.TOMLDecodeError as exc:
        result.errors.append(
            ValidationError(
                field="toml",
                message=f"Invalid TOML: {exc}",
                hint="Run your TOML through a linter (e.g. https://www.toml-lint.com).",
            )
        )
        return result

    # ── [simulation] ─────────────────────────────────────────────────────
    sim: dict[str, Any] = data.get("simulation", {})
    _check_simulation(sim, result)

    # ── [[parties]] ──────────────────────────────────────────────────────
    parties_raw: list[Any] = data.get("parties", [])
    if not isinstance(parties_raw, list) or len(parties_raw) == 0:
        result.errors.append(
            ValidationError(
                field="parties",
                message="At least one [[parties]] block is required.",
                hint="Add a [[parties]] block with id and name fields.",
            )
        )
    else:
        seen_ids: set[str] = set()
        for i, p in enumerate(parties_raw):
            _check_party(p, i, seen_ids, result)

    # ── [environment] ────────────────────────────────────────────────────
    env_raw: Any = data.get("environment")
    if env_raw is None:
        result.errors.append(
            ValidationError(
                field="environment",
                message="[environment] section is required.",
                hint="Add an [environment] block with at least id and name.",
            )
        )
    elif not isinstance(env_raw, dict):
        result.errors.append(
            ValidationError(
                field="environment",
                message="[environment] must be a TOML table, not a scalar.",
            )
        )
    else:
        _check_environment(env_raw, result)

    # ── Populate summary fields ───────────────────────────────────────────
    if result.valid:
        result.party_count = len(parties_raw) if isinstance(parties_raw, list) else 0
        result.provider = str(sim.get("provider", "gemini"))
        result.episodes = int(sim.get("episodes", 3))

    return result


# ---------------------------------------------------------------------------
# Internal checkers
# ---------------------------------------------------------------------------


def _check_simulation(sim: dict[str, Any], result: ValidationResult) -> None:
    provider = sim.get("provider")
    if provider is not None and str(provider) not in _VALID_PROVIDERS:
        result.errors.append(
            ValidationError(
                field="simulation.provider",
                message=f"Invalid provider {provider!r}.",
                hint=f"Valid values: {', '.join(sorted(_VALID_PROVIDERS))}.",
            )
        )

    episodes = sim.get("episodes")
    if episodes is not None:
        if not isinstance(episodes, int) or isinstance(episodes, bool):
            result.errors.append(
                ValidationError(
                    field="simulation.episodes",
                    message=f"episodes must be an integer, got {type(episodes).__name__!r}.",
                )
            )
        elif episodes < 1:
            result.errors.append(
                ValidationError(
                    field="simulation.episodes",
                    message=f"episodes must be >= 1, got {episodes}.",
                )
            )

    ctx = sim.get("context_window_episodes")
    if ctx is not None and (not isinstance(ctx, int) or isinstance(ctx, bool) or ctx < 1):
        result.errors.append(
            ValidationError(
                field="simulation.context_window_episodes",
                message=f"context_window_episodes must be an integer >= 1, got {ctx!r}.",
            )
        )


def _check_party(
    p: object,
    index: int,
    seen_ids: set[str],
    result: ValidationResult,
) -> None:
    prefix = f"parties[{index}]"

    if not isinstance(p, dict):
        result.errors.append(
            ValidationError(
                field=prefix,
                message="Each [[parties]] entry must be a TOML table.",
            )
        )
        return

    # id
    pid = p.get("id")
    if not pid or not isinstance(pid, str):
        result.errors.append(
            ValidationError(
                field=f"{prefix}.id",
                message="id is required and must be a non-empty string.",
                hint='Example: id = "alice"',
            )
        )
        pid = f"<party {index}>"
    elif pid in seen_ids:
        result.errors.append(
            ValidationError(
                field=f"{prefix}.id",
                message=f"Duplicate party id {pid!r}.",
                hint="Each party must have a unique id.",
            )
        )
    else:
        seen_ids.add(pid)

    # name
    if not p.get("name") or not isinstance(p.get("name"), str):
        result.errors.append(
            ValidationError(
                field=f"{prefix}.name",
                message=f"Party {pid!r} is missing a required 'name' field.",
                hint='Example: name = "Alice"',
            )
        )

    # kind
    kind = p.get("kind", "person")
    if kind not in _VALID_KINDS:
        result.errors.append(
            ValidationError(
                field=f"{prefix}.kind",
                message=f"Party {pid!r} has invalid kind {kind!r}.",
                hint=f"Valid values: {', '.join(sorted(_VALID_KINDS))}.",
            )
        )

    # string-list fields
    for fld in ("goals", "traits", "knowledge", "constraints"):
        val = p.get(fld)
        if val is not None and (
            not isinstance(val, list) or not all(isinstance(v, str) for v in val)
        ):
            result.errors.append(
                ValidationError(
                    field=f"{prefix}.{fld}",
                    message=f"Party {pid!r} field '{fld}' must be a list of strings.",
                    hint=f'Example: {fld} = ["item one", "item two"]',
                )
            )


def _check_environment(env: dict[str, Any], result: ValidationResult) -> None:
    # id
    eid = env.get("id")
    if not eid or not isinstance(eid, str):
        result.errors.append(
            ValidationError(
                field="environment.id",
                message="environment.id is required and must be a non-empty string.",
                hint='Example: id = "town"',
            )
        )

    # name
    if not env.get("name") or not isinstance(env.get("name"), str):
        result.errors.append(
            ValidationError(
                field="environment.name",
                message="environment.name is required and must be a non-empty string.",
                hint='Example: name = "Riverside Town"',
            )
        )

    # facts
    facts = env.get("facts")
    if facts is not None and (
        not isinstance(facts, list) or not all(isinstance(f, str) for f in facts)
    ):
        result.errors.append(
            ValidationError(
                field="environment.facts",
                message="environment.facts must be a list of strings.",
                hint='Example: facts = ["The market opens at dawn", "Rain is expected"]',
            )
        )

    # initial_state
    initial_state = env.get("initial_state")
    if initial_state is not None:
        if not isinstance(initial_state, dict):
            result.errors.append(
                ValidationError(
                    field="environment.initial_state",
                    message="initial_state must be a TOML table.",
                )
            )
        else:
            _check_initial_state(initial_state, result)


def _check_initial_state(state: dict[str, Any], result: ValidationResult) -> None:
    import re

    _key_pattern = re.compile(
        r"^("
        r"time\.\w+"
        r"|weather\.\w+"
        r"|loc\.[^.]+\.(place|visible_to)"
        r"|obj\.[^.]+\.(place|visible_to)"
        r"|event\.\w+"
        r")$"
    )

    for key, value in state.items():
        # Detect nested dicts — the classic unquoted dotted-key mistake
        if isinstance(value, dict):
            result.errors.append(
                ValidationError(
                    field=f"environment.initial_state.{key}",
                    message=(
                        f"Value for {key!r} is a nested table, not a scalar. "
                        "This is almost always caused by an unquoted dotted key."
                    ),
                    hint=(
                        f'Quote the key with double quotes: "{key}.<subkey>" = "value"\n'
                        f"    Unquoted ({key}.subkey = ...) creates a nested table in TOML;\n"
                        f'    quoted ("{key}.subkey" = ...) creates a flat string key.'
                    ),
                )
            )
            continue

        if isinstance(value, list):
            result.errors.append(
                ValidationError(
                    field=f"environment.initial_state.{key}",
                    message=f"{key!r} has a list value — lists are not allowed as state values.",
                    hint="Use a scalar: string, integer, float, or boolean.",
                )
            )
            continue

        if not isinstance(value, _VALID_STATE_TYPES):
            result.errors.append(
                ValidationError(
                    field=f"environment.initial_state.{key}",
                    message=(
                        f"{key!r} has an unsupported type {type(value).__name__!r}. "
                        "Only string, integer, float, and boolean are allowed."
                    ),
                )
            )
            continue

        # Schema warning (not a hard error)
        if not _key_pattern.match(key):
            result.warnings.append(
                f"environment.initial_state key {key!r} does not match any recognised "
                "family (time.*, weather.*, event.*, loc.<id>.place/visible_to, "
                "obj.<id>.place/visible_to). The simulation will still run."
            )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    """Validate one or more scenario files (YAML preferred, TOML deprecated).

    Exit code: 0 if all files are valid, 1 if any have errors.
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="Validate a Roleplay scenario file (YAML preferred; TOML deprecated).",
        epilog="See scenarios/example.yaml for the full schema.",
    )
    parser.add_argument(
        "files",
        nargs="+",
        metavar="FILE",
        type=Path,
        help="Path(s) to .yaml (preferred) or .toml (deprecated) scenario file(s).",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Only print errors, not warnings.",
    )
    args = parser.parse_args()

    any_invalid = False
    for path in args.files:
        result = validate_scenario(path)
        print(f"\n{path}")
        print(result.summary())

        if result.errors:
            any_invalid = True
            for err in result.errors:
                print(err)

        if not args.quiet and result.warnings:
            for w in result.warnings:
                print(f"  ⚠  {w}")

    print()
    sys.exit(1 if any_invalid else 0)


if __name__ == "__main__":
    main()
