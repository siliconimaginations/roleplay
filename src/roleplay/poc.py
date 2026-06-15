"""Proof-of-concept scenario runner.

Wires together all layers (core, memory, engine, providers) into a minimal
end-to-end simulation that can run with a real Gemini API key or a mock
provider for offline testing.

Usage::

    # Real (requires GEMINI_API_KEY):
    python -m roleplay.poc

    # Mock (no API key needed):
    python -m roleplay.poc --mock
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from dataclasses import dataclass, field

from roleplay.core.episode import (
    NoopClock,
    RoundRobinScheduler,
    SimulationHistory,
)
from roleplay.core.party import make_environment, make_person
from roleplay.core.simulation_state import SimulationConfig, SimulationState
from roleplay.engine.engine import SimulationEngine
from roleplay.memory.store import InMemoryStore
from roleplay.providers.base import CompletionRequest, CompletionResponse

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Mock provider — returns scripted responses so the POC runs without an API key
# ---------------------------------------------------------------------------


@dataclass
class _MockProvider:
    """Cycles through a fixed list of responses."""

    _responses: list[str] = field(
        default_factory=lambda: [
            "I think we should approach this carefully.",
            "Agreed. Let's start with a plan.",
            "The environment looks stable. Proceed.",
            "I'll gather the necessary resources.",
            "Ready when you are.",
        ]
    )
    _idx: int = field(default=0, init=False)

    @property
    def default_model(self) -> str:
        return "mock"

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        text = self._responses[self._idx % len(self._responses)]
        self._idx += 1
        return CompletionResponse(text=text, model_used="mock")


# ---------------------------------------------------------------------------
# Scenario builder
# ---------------------------------------------------------------------------


def _build_state(config: SimulationConfig) -> SimulationState:
    alice = make_person(
        "alice",
        "Alice",
        "A pragmatic negotiator",
        goals=("Reach a fair agreement", "Preserve the relationship"),
        traits=("calm", "strategic", "direct"),
        knowledge=("Experienced in conflict resolution",),
        constraints=("Must stay within budget",),
    )
    bob = make_person(
        "bob",
        "Bob",
        "An optimistic entrepreneur",
        goals=("Secure funding", "Build a strong partnership"),
        traits=("enthusiastic", "creative", "risk-tolerant"),
        knowledge=("Has a viable product idea",),
        constraints=("Limited runway — 6 months",),
    )
    town = make_environment(
        "town",
        "Riverside Town",
        setting="A small riverside town in early autumn",
        facts=("The annual trade fair is in two weeks", "Economic times are cautious"),
        initial_state={
            "time.simulated": "Day 1, Morning",
            "weather": "clear",
            "event.mood": "cautious optimism",
        },
    )
    clock = NoopClock()
    scheduler = RoundRobinScheduler()
    history = SimulationHistory()

    return SimulationState(
        config=config,
        parties={"alice": alice, "bob": bob},
        environment=town,
        history=history,
        scheduler=scheduler,
        clock=clock,
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def run_poc(*, use_mock: bool = False, max_episodes: int = 3) -> None:
    config = SimulationConfig(
        session_id="poc-001",
        context_window_episodes=5,
        memory_max_entries=20,
        environment_reactive=True,
        auto_checkpoint=False,
    )
    state = _build_state(config)
    memory_store = InMemoryStore()

    if use_mock:
        provider: object = _MockProvider()
        logger.info("Using mock provider (no API key required)")
    else:
        from roleplay.providers.gemini import GeminiProvider

        provider = GeminiProvider()
        logger.info("Using GeminiProvider (GEMINI_API_KEY must be set)")

    engine = SimulationEngine(state=state, provider=provider, memory_store=memory_store)

    logger.info("Starting POC simulation: %d episodes", max_episodes)
    await engine.run(max_episodes=max_episodes)

    logger.info("Simulation complete. Summary:")
    for ep in state.history.completed_episodes():
        logger.info("  Episode %d: %d turns", ep.index, len(ep.turns))
        for t in ep.turns:
            party = state.get_party(t.party_id)
            preview = t.output[:80].replace("\n", " ")
            logger.info("    %s: %s…", party.name, preview)


def main() -> None:
    parser = argparse.ArgumentParser(description="Roleplay POC runner")
    parser.add_argument("--mock", action="store_true", help="Use mock provider (no API key)")
    parser.add_argument("--episodes", type=int, default=3, help="Number of episodes to run")
    args = parser.parse_args()
    asyncio.run(run_poc(use_mock=args.mock, max_episodes=args.episodes))


if __name__ == "__main__":
    main()
