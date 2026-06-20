"""SimulationConfig and SimulationState — the top-level in-memory snapshot."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from roleplay.core.environment import EnvironmentRegistry
from roleplay.core.party import Party, PartyKind

if TYPE_CHECKING:
    from roleplay.core.episode import SimulatedTimeClock, SimulationHistory, TurnScheduler


@dataclass
class SimulationConfig:
    """All tunable parameters for a simulation run.  Loaded from YAML."""

    session_id: str
    context_window_episodes: int = 10
    memory_max_entries: int = 20
    memory_char_budget: int = 4_000
    memory_write_mode: str = "template"  # "template" | "llm"
    compaction_threshold: int = 200
    compaction_batch_size: int = 50
    compaction_importance_floor: float = 0.7
    compaction_char_limit: int = 80_000
    forgetting_enabled: bool = False
    forgetting_idle_episodes: int = 100
    memory_retrieve_fail_mode: str = "raise"  # "raise" | "empty"
    retrieval_weights: dict[str, float] = field(
        default_factory=lambda: {
            "alpha": 0.5,
            "beta": 0.25,
            "gamma": 0.15,
            "delta": 0.10,
        }
    )
    default_provider: str = "gemini"
    default_model: str = ""  # If set, used as the primary model for the chosen provider.
    environment_reactive: bool = True
    auto_checkpoint: bool = True
    passive_observation_parties: list[str] = field(default_factory=list)
    prompt_char_budget: int = 20_000
    goal: str = ""  # Optional end condition; checked after every episode via LLM.


@dataclass
class SimulationState:
    """Single source of truth for a running simulation.

    ``parties`` holds all non-environment parties keyed by id.
    ``environment`` is exactly one ENVIRONMENT party.
    """

    config: SimulationConfig
    parties: dict[str, Party]
    environment: Party
    history: SimulationHistory
    scheduler: TurnScheduler
    clock: SimulatedTimeClock
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    environments: EnvironmentRegistry = field(default_factory=EnvironmentRegistry)

    def __post_init__(self) -> None:
        if self.environment.kind is not PartyKind.ENVIRONMENT:
            raise ValueError(
                f"environment must be an ENVIRONMENT party, got {self.environment.kind}"
            )
        for party_id, party in self.parties.items():
            if party.kind is PartyKind.ENVIRONMENT:
                raise ValueError(
                    f"Non-environment parties dict must not contain ENVIRONMENT party '{party_id}'"
                )

    def party_ids(self) -> list[str]:
        """Return all non-environment party ids in insertion order."""
        return list(self.parties.keys())

    def get_party(self, party_id: str) -> Party:
        """Return the party with *party_id*.

        Checks both the non-environment parties dict and the environment party.
        Raises :exc:`KeyError` if not found.
        """
        if party_id in self.parties:
            return self.parties[party_id]
        if party_id == self.environment.id:
            return self.environment
        raise KeyError(party_id)

    def all_parties_including_env(self) -> list[Party]:
        """Return all parties plus the environment, in registration order.

        Non-environment parties come first (insertion order), environment last.
        """
        return [*self.parties.values(), self.environment]
