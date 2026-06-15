"""Party model — the fundamental unit of the simulator.

A Party is any participant: a person, organisation, or the environment itself.
All are represented by the same ``Party`` dataclass; the ``PartyKind``
discriminator tells the engine how to handle each one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from dataclasses import fields as dc_fields
from dataclasses import replace as dc_replace
from datetime import UTC, datetime
from enum import Enum
from typing import NamedTuple

# ---------------------------------------------------------------------------
# Public type alias
# ---------------------------------------------------------------------------

StateValue = str | int | float | bool | None
"""Allowed types for a Party state variable.

``bool`` must be checked before ``int`` in isinstance tests because
``bool`` is a subclass of ``int``.
"""

_VALID_TYPES: tuple[type, ...] = (str, bool, int, float, type(None))


def _is_state_value(value: object) -> bool:
    """Return True if *value* is an allowed StateValue."""
    return isinstance(value, _VALID_TYPES)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class PartyKind(Enum):
    """Discriminator for the three kinds of simulation participant."""

    PERSON = "person"
    ORGANIZATION = "organization"
    ENVIRONMENT = "environment"


# ---------------------------------------------------------------------------
# Persona (immutable)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Persona:
    """Who a party is — stable identity injected verbatim into LLM prompts.

    Only ``description`` is required. All other fields default to empty tuples.
    """

    description: str
    goals: tuple[str, ...] = ()
    traits: tuple[str, ...] = ()
    knowledge: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# StateChange (immutable log entry)
# ---------------------------------------------------------------------------


class StateChange(NamedTuple):
    """An immutable record of a single state variable mutation."""

    key: str
    old_value: StateValue
    new_value: StateValue
    episode_index: int
    reason: str | None


# ---------------------------------------------------------------------------
# Party
# ---------------------------------------------------------------------------


@dataclass
class Party:
    """A simulation participant with identity, persona, and mutable state.

    ``state`` and ``state_history`` must only be mutated via
    :meth:`apply_state_update`.  Direct dict assignment to ``state`` bypasses
    the audit log and should never be used by engine code.
    """

    id: str
    name: str
    kind: PartyKind
    persona: Persona
    state: dict[str, StateValue] = field(default_factory=dict)
    state_history: list[StateChange] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    # ------------------------------------------------------------------
    # State mutation
    # ------------------------------------------------------------------

    def apply_state_update(
        self,
        updates: dict[str, StateValue],
        episode_index: int,
        reason: str | None = None,
    ) -> list[StateChange]:
        """Apply a batch of state updates and return the new change records.

        All updates are applied atomically — if any value is invalid the whole
        batch is rejected before any change is written.

        Args:
            updates: Mapping of ``{key: new_value}`` pairs.
            episode_index: The episode during which this update occurs.
            reason: Optional free-text rationale recorded in each change.

        Returns:
            The ``StateChange`` records appended to ``state_history``.

        Raises:
            ValueError: If any value in *updates* is not a valid
                ``StateValue`` type (nested dicts, lists, etc.).
        """
        for key, value in updates.items():
            if not _is_state_value(value):
                raise ValueError(
                    f"Invalid StateValue for key '{key}': "
                    f"{type(value).__name__!r} is not allowed. "
                    "Use str, int, float, bool, or None."
                )

        changes: list[StateChange] = []
        for key, new_value in updates.items():
            old_value: StateValue = self.state.get(key)
            change = StateChange(
                key=key,
                old_value=old_value,
                new_value=new_value,
                episode_index=episode_index,
                reason=reason,
            )
            self.state[key] = new_value
            self.state_history.append(change)
            changes.append(change)

        return changes

    def get_state(self, key: str, default: StateValue = None) -> StateValue:
        """Return the current value of a state variable, or *default*."""
        return self.state.get(key, default)

    def state_snapshot(self) -> dict[str, StateValue]:
        """Return a shallow copy of the current state dict."""
        return dict(self.state)

    # ------------------------------------------------------------------
    # Persona update
    # ------------------------------------------------------------------

    def replace_persona(self, **changes: object) -> Party:
        """Return a *new* Party with an updated Persona.

        This is the deliberate escape hatch for the rare case where a party's
        identity genuinely evolves (character arc, company pivot). The caller
        is responsible for recording why the persona changed.

        Example::

            alice = alice.replace_persona(goals=("Find the thief", "Stay safe"))

        Raises:
            TypeError: If any keyword argument is not a valid Persona field.
        """
        valid_fields = {f.name for f in dc_fields(Persona)}
        invalid = set(changes) - valid_fields
        if invalid:
            raise TypeError(
                "replace_persona() got unexpected keyword argument(s): "
                + ", ".join(sorted(invalid))
            )
        new_persona = dc_replace(self.persona, **changes)  # type: ignore[arg-type]
        return dc_replace(self, persona=new_persona)

    # ------------------------------------------------------------------
    # LLM context
    # ------------------------------------------------------------------

    def to_prompt_context(self, *, include_state: bool = True) -> str:
        """Serialise this party into a text block for LLM prompt injection.

        For ENVIRONMENT parties the block is titled "World" and renders
        ``persona.knowledge`` as a "Background facts" section instead of Goals
        and Traits.

        For PERSON and ORGANIZATION parties the block includes Goals, Traits,
        Knowledge, and Constraints if any are present.
        """
        kind_label = self.kind.value  # "person" / "organization" / "environment"

        if self.kind is PartyKind.ENVIRONMENT:
            return self._env_context(include_state=include_state)

        lines: list[str] = [
            f"## {self.name} ({kind_label})",
            "",
            self.persona.description,
        ]

        if self.persona.goals:
            lines += ["", "Goals:"]
            lines += [f"- {g}" for g in self.persona.goals]

        if self.persona.traits:
            lines += ["", f"Traits: {', '.join(self.persona.traits)}"]

        if self.persona.knowledge:
            lines += ["", "Knowledge:"]
            lines += [f"- {k}" for k in self.persona.knowledge]

        if self.persona.constraints:
            lines += ["", "Constraints:"]
            lines += [f"- {c}" for c in self.persona.constraints]

        if include_state and self.state:
            lines += ["", "Current state:"]
            lines += [f"- {k}: {v}" for k, v in sorted(self.state.items())]

        return "\n".join(lines)

    def _env_context(self, *, include_state: bool) -> str:
        lines: list[str] = [
            f"## World: {self.name} (environment)",
            "",
            self.persona.description,
        ]

        if self.persona.knowledge:
            lines += ["", "Background facts:"]
            lines += [f"- {k}" for k in self.persona.knowledge]

        if include_state and self.state:
            lines += ["", "Current world state:"]
            lines += [f"- {k}: {v}" for k, v in sorted(self.state.items())]

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Construction helpers
# ---------------------------------------------------------------------------


def make_person(
    id: str,
    name: str,
    description: str,
    *,
    goals: tuple[str, ...] = (),
    traits: tuple[str, ...] = (),
    knowledge: tuple[str, ...] = (),
    constraints: tuple[str, ...] = (),
) -> Party:
    """Construct a PERSON party."""
    persona = Persona(
        description=description,
        goals=goals,
        traits=traits,
        knowledge=knowledge,
        constraints=constraints,
    )
    return Party(id=id, name=name, kind=PartyKind.PERSON, persona=persona)


def make_organization(
    id: str,
    name: str,
    description: str,
    *,
    goals: tuple[str, ...] = (),
    traits: tuple[str, ...] = (),
    knowledge: tuple[str, ...] = (),
    constraints: tuple[str, ...] = (),
) -> Party:
    """Construct an ORGANIZATION party."""
    persona = Persona(
        description=description,
        goals=goals,
        traits=traits,
        knowledge=knowledge,
        constraints=constraints,
    )
    return Party(id=id, name=name, kind=PartyKind.ORGANIZATION, persona=persona)


def make_environment(
    id: str,
    name: str,
    setting: str,
    facts: tuple[str, ...] = (),
    initial_state: dict[str, StateValue] | None = None,
) -> Party:
    """Construct the ENVIRONMENT party.

    Args:
        id: Unique party id (e.g. ``"world"``).
        name: Display name shown in prompts.
        setting: Narrative world description → ``persona.description``.
        facts: Immutable background facts → ``persona.knowledge``.
        initial_state: Optional starting state validated against key schema.
    """
    persona = Persona(description=setting, knowledge=facts)
    env = Party(id=id, name=name, kind=PartyKind.ENVIRONMENT, persona=persona)
    if initial_state:
        schema_warnings = validate_environment_state(initial_state)
        if schema_warnings:
            import warnings as _w

            for msg in schema_warnings:
                _w.warn(msg, stacklevel=2)
        # episode_index=0 means pre-simulation setup; apply_state_update also
        # validates that every value is a valid StateValue type.
        env.apply_state_update(initial_state, episode_index=0, reason="initial state")
    return env


# ---------------------------------------------------------------------------
# Environment state validation
# ---------------------------------------------------------------------------

# Recognised dot-prefix families for environment state keys.
_ENV_KEY_PATTERN = re.compile(
    r"^("
    r"time\.\w+"
    r"|weather\.\w+"
    r"|loc\.[^.]+\.(place|visible_to)"
    r"|obj\.[^.]+\.(place|visible_to)"
    r"|event\.\w+"
    r")$"
)


def validate_environment_state(state: dict[str, StateValue]) -> list[str]:
    """Return warning strings for env state keys that violate the schema.

    Does not raise — callers log the warnings at construction time.

    Recognised families::

        time.*            time.simulated, time.episode, …
        weather.*         weather.condition, weather.temp_c, …
        loc.<id>.place
        loc.<id>.visible_to
        obj.<id>.place
        obj.<id>.visible_to
        event.*           event.current, event.recent, …

    Any key that does not match one of these families generates a warning.
    """
    warnings: list[str] = []
    for key in state:
        if not _ENV_KEY_PATTERN.match(key):
            warnings.append(
                f"Environment state key '{key}' does not follow the dot-prefix "
                "schema (expected families: time.*, weather.*, "
                "loc.<id>.place/visible_to, obj.<id>.place/visible_to, event.*)."
            )
    return warnings
