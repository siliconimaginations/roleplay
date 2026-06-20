"""Named environments (locations) for multi-environment simulations.

Each :class:`Environment` describes a discrete space a party can occupy.
Parties declare their current location via a ``location`` state key; the
engine enriches their prompt with the matching environment description and
applies co-location filtering so only parties sharing a space interact.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from roleplay.core.party import StateValue


@dataclass
class Environment:
    """A named location within the simulation world.

    Args:
        id:          Unique identifier referenced by party ``location`` state.
        name:        Human-readable display name shown in prompts.
        description: Narrative description injected into co-located party prompts.
        state:       Optional static key/value metadata included in the prompt block.
    """

    id: str
    name: str
    description: str
    state: dict[str, StateValue] = field(default_factory=dict)


class EnvironmentRegistry:
    """Lookup table from environment id → :class:`Environment`.

    An empty registry (no environments defined) is falsy and causes the engine
    to skip all location-based logic, preserving backward compatibility.
    """

    def __init__(self, environments: list[Environment] | None = None) -> None:
        self._registry: dict[str, Environment] = {}
        for env in environments or []:
            self._registry[env.id] = env

    def get(self, env_id: str) -> Environment | None:
        """Return the environment with *env_id*, or ``None`` if not found."""
        return self._registry.get(env_id)

    def ids(self) -> list[str]:
        """Return all registered environment ids."""
        return list(self._registry.keys())

    def __bool__(self) -> bool:
        return bool(self._registry)

    def __len__(self) -> int:
        return len(self._registry)
