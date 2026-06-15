"""Proof-of-concept scenario runner.

Wires together all layers (core, memory, engine, providers) into a minimal
end-to-end simulation.  Can run with a real LLM API key, a TOML scenario
file, or a mock provider for offline testing.

Usage::

    # Built-in scenario, mock provider (no API key needed):
    python -m roleplay.poc --mock

    # Built-in scenario, real Gemini (GEMINI_API_KEY must be set):
    python -m roleplay.poc

    # Custom scenario from a TOML file (provider and episodes taken from file):
    python -m roleplay.poc --config scenarios/example.toml

    # Custom scenario, force mock (ignores provider in TOML):
    python -m roleplay.poc --config scenarios/my.toml --mock

    # Custom episode count (overrides value in TOML):
    python -m roleplay.poc --config scenarios/my.toml --episodes 5

    # Load API keys from a non-default .env file:
    python -m roleplay.poc --config scenarios/my.toml --env-file secrets/keys.env
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path

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
# Built-in scenario (used when --config is not passed)
# ---------------------------------------------------------------------------


def _build_default_state(config: SimulationConfig) -> SimulationState:
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
    return SimulationState(
        config=config,
        parties={"alice": alice, "bob": bob},
        environment=town,
        history=SimulationHistory(),
        scheduler=RoundRobinScheduler(),
        clock=NoopClock(),
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def _resolve_provider(provider_name: str) -> object:
    """Return a provider instance for the given name."""
    if provider_name == "claude":
        from roleplay.providers.claude_provider import ClaudeProvider

        return ClaudeProvider()
    # default: gemini
    from roleplay.providers.gemini import GeminiProvider

    return GeminiProvider()


async def run_poc(
    *,
    use_mock: bool = False,
    max_episodes: int = 3,
    config_path: Path | None = None,
    env_file: Path = Path(".env"),
) -> None:
    """Run the simulation end-to-end.

    Args:
        use_mock: Use the built-in mock provider (no API key required).
        max_episodes: Number of episodes to run.
        config_path: Optional path to a TOML scenario file.  If omitted the
            built-in Alice/Bob scenario is used.
        env_file: Path to a ``.env`` file to load API keys from.  Defaults to
            ``.env`` in the current directory.  Silently ignored if missing.
    """
    # Always load .env first so API keys are available before any provider import
    from roleplay.config import load_env_file, load_scenario

    load_env_file(env_file)

    if config_path is not None:
        state, provider_name, file_episodes = load_scenario(config_path)
        # CLI --episodes overrides file value only when explicitly provided
        # (we detect this via the sentinel -1 set in main())
        if max_episodes == -1:
            max_episodes = file_episodes
        if use_mock:
            provider_name = "mock"
    else:
        sim_config = SimulationConfig(
            session_id="poc-001",
            context_window_episodes=5,
            memory_max_entries=20,
            environment_reactive=True,
            auto_checkpoint=False,
        )
        state = _build_default_state(sim_config)
        provider_name = "mock" if use_mock else "gemini"
        if max_episodes == -1:
            max_episodes = 3

    memory_store = InMemoryStore()

    if provider_name == "mock":
        provider: object = _MockProvider()
        logger.info("Using mock provider (no API key required)")
    else:
        provider = _resolve_provider(provider_name)
        logger.info("Using provider: %s", provider_name)

    engine = SimulationEngine(state=state, provider=provider, memory_store=memory_store)

    logger.info("Starting simulation: %d episodes", max_episodes)
    await engine.run(max_episodes=max_episodes)

    logger.info("Simulation complete. Summary:")
    for ep in state.history.completed_episodes():
        logger.info("  Episode %d: %d turns", ep.index, len(ep.turns))
        for t in ep.turns:
            party = state.get_party(t.party_id)
            preview = t.output[:80].replace("\n", " ")
            logger.info("    %s: %s…", party.name, preview)


def main() -> None:
    parser = argparse.ArgumentParser(description="Roleplay scenario runner")
    parser.add_argument(
        "--config",
        metavar="PATH",
        type=Path,
        default=None,
        help="Path to a TOML scenario file (default: built-in Alice/Bob scenario)",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use the mock provider — no API key required",
    )
    parser.add_argument(
        "--episodes",
        type=int,
        default=-1,
        metavar="N",
        help="Number of episodes to run (overrides value in --config file)",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        metavar="PATH",
        help="Path to a .env file with API keys (default: .env)",
    )
    args = parser.parse_args()
    asyncio.run(
        run_poc(
            use_mock=args.mock,
            max_episodes=args.episodes,
            config_path=args.config,
            env_file=args.env_file,
        )
    )


if __name__ == "__main__":
    main()
