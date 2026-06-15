"""ObserverHook protocol, ObserverDirective, and InjectionPayload."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from roleplay.core.party import StateValue
    from roleplay.core.simulation_state import SimulationState
    from roleplay.engine.turn import Turn
    from roleplay.memory.store import MemoryEntry


class _DirectiveKind(Enum):
    CONTINUE = auto()
    HALT = auto()
    INJECT = auto()


@dataclass
class InjectionPayload:
    """What the observer wants to change before the next turn."""

    context_override: str | None = None
    state_updates: dict[str, dict[str, StateValue]] = field(default_factory=dict)
    persona_overrides: dict[str, dict[str, object]] = field(default_factory=dict)
    memory_writes: list[MemoryEntry] = field(default_factory=list)
    force_scheduler: list[str] | None = None


class ObserverDirective:
    """Sealed return type for observer callbacks."""

    def __init__(
        self, kind: _DirectiveKind, reason: str = "", payload: InjectionPayload | None = None
    ) -> None:
        self._kind = kind
        self._reason = reason
        self._payload = payload

    @staticmethod
    def continue_() -> ObserverDirective:
        return ObserverDirective(_DirectiveKind.CONTINUE)

    @staticmethod
    def halt(reason: str = "") -> ObserverDirective:
        return ObserverDirective(_DirectiveKind.HALT, reason=reason)

    @staticmethod
    def inject(payload: InjectionPayload) -> ObserverDirective:
        return ObserverDirective(_DirectiveKind.INJECT, payload=payload)

    @property
    def is_halt(self) -> bool:
        return self._kind is _DirectiveKind.HALT

    @property
    def is_inject(self) -> bool:
        return self._kind is _DirectiveKind.INJECT

    @property
    def reason(self) -> str:
        return self._reason

    @property
    def payload(self) -> InjectionPayload | None:
        return self._payload


class ObserverHook(Protocol):
    """Human intervention interface — called at defined episode lifecycle points."""

    async def before_episode(
        self,
        state: SimulationState,
        episode_index: int,
    ) -> ObserverDirective: ...

    async def after_turn(
        self,
        state: SimulationState,
        turn: Turn,
    ) -> ObserverDirective: ...

    async def after_episode(
        self,
        state: SimulationState,
        episode: object,
    ) -> ObserverDirective: ...
