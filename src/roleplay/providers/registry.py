"""ProviderRegistry — maps provider names to Provider instances."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from roleplay.providers.base import Provider

logger = logging.getLogger(__name__)


@dataclass
class ProviderRegistry:
    """Holds named Provider instances and dispatches completion requests.

    Usage::

        registry = ProviderRegistry()
        registry.register("gemini", GeminiProvider())
        registry.register("claude", ClaudeProvider())
        provider = registry.get("gemini")
    """

    _providers: dict[str, Provider] = field(default_factory=dict, init=False, repr=False)

    def register(self, name: str, provider: Provider) -> None:
        """Register a provider under *name*, replacing any existing entry."""
        self._providers[name] = provider
        logger.debug("Registered provider '%s' (%s)", name, type(provider).__name__)

    def get(self, name: str) -> Provider:
        """Return provider by name; raises KeyError if not registered."""
        try:
            return self._providers[name]
        except KeyError:
            available = list(self._providers)
            raise KeyError(f"Provider '{name}' not registered. Available: {available}") from None

    def names(self) -> list[str]:
        """Return sorted list of registered provider names."""
        return sorted(self._providers)

    def __contains__(self, name: object) -> bool:
        return name in self._providers
