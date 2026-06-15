"""Proof-of-concept scenario runner.

Wires together all layers (core, memory, engine, providers) into a minimal
end-to-end simulation.  Can run with a real LLM API key, a TOML scenario
file, or a mock provider for offline testing.

Usage::

    # Built-in scenario, mock provider (no API key needed):
    uv run python -m roleplay.poc --mock

    # Built-in scenario, real Gemini (.env must contain GEMINI_API_KEY):
    uv run python -m roleplay.poc

    # Custom scenario from a TOML file:
    uv run python -m roleplay.poc --config scenarios/example.toml

    # Force mock even with a config file:
    uv run python -m roleplay.poc --config scenarios/my.toml --mock

    # Override episode count from command line:
    uv run python -m roleplay.poc --config scenarios/my.toml --episodes 5

    # Load API keys from a non-default location:
    uv run python -m roleplay.poc --config scenarios/my.toml --env-file secrets/keys.env

    # Quiet mode — one-line summary per episode, full dialog saved to a log file:
    uv run python -m roleplay.poc --config scenarios/my.toml --verbosity 0
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import logging
import shutil
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from roleplay.core.episode import (
    NoopClock,
    RoundRobinScheduler,
    SimulationHistory,
)
from roleplay.core.party import PartyKind, make_environment, make_person
from roleplay.core.simulation_state import SimulationConfig, SimulationState
from roleplay.engine.engine import SimulationEngine
from roleplay.engine.observer import ObserverDirective
from roleplay.memory.store import InMemoryStore
from roleplay.providers.base import CompletionRequest, CompletionResponse

if TYPE_CHECKING:
    from roleplay.engine.observer import ObserverHook
    from roleplay.engine.turn import Turn

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pretty-print observer — streams turns to the terminal in real time
# ---------------------------------------------------------------------------


@dataclass
class _CliObserver:
    """Prints episode headers and turn text to stdout as the simulation runs.

    Args:
        verbosity: Output detail level.

            * ``1`` (default) — full turn dialog streamed in real time.
            * ``0`` — one-line summary per party after each episode; full
              dialog is collected in memory and can be written to a file via
              :meth:`write_log`.
    """

    verbosity: int = 1
    _width: int = field(default_factory=lambda: min(shutil.get_terminal_size().columns, 100))
    _episode_turns: list[Turn] = field(default_factory=list, init=False, repr=False)
    _log_lines: list[str] = field(default_factory=list, init=False, repr=False)

    async def before_episode(
        self,
        state: SimulationState,
        episode_index: int,
    ) -> ObserverDirective:
        self._episode_turns.clear()
        rule = "─" * (self._width - 14 - len(str(episode_index + 1)))
        header = f"\n{'─' * 3}  Episode {episode_index + 1}  {rule}"
        print(header)
        self._log_lines.append(header)
        return ObserverDirective.continue_()

    async def after_turn(
        self,
        state: SimulationState,
        turn: Turn,
    ) -> ObserverDirective:
        self._episode_turns.append(turn)
        party = state.get_party(turn.party_id)
        label = party.name
        if party.kind is PartyKind.ENVIRONMENT:
            label += "  [env]"

        underline = "╌" * len(label)
        indent = "  "
        wrap_width = self._width - len(indent)

        lines: list[str] = [f"\n  {label}", f"  {underline}"]
        for para in turn.output.strip().split("\n\n"):
            lines.append(
                textwrap.fill(
                    para.strip(),
                    width=wrap_width,
                    initial_indent=indent,
                    subsequent_indent=indent,
                )
            )

        # Always accumulate for the log.
        self._log_lines.extend(lines)

        # Only print when verbosity is high enough.
        if self.verbosity >= 1:
            for line in lines:
                print(line)

        return ObserverDirective.continue_()

    async def after_episode(
        self,
        state: SimulationState,
        episode: object,
    ) -> ObserverDirective:
        if self.verbosity == 0:
            # Print one-line snippet per party so the user can follow progress.
            for turn in self._episode_turns:
                party = state.get_party(turn.party_id)
                raw = turn.output.strip().replace("\n", " ")
                snippet = raw[:100] + "…" if len(raw) > 100 else raw
                print(f"  {party.name}: {snippet}")
        return ObserverDirective.continue_()

    def write_log(self, path: Path) -> None:
        """Write the full dialog collected during the run to *path*."""
        path.write_text("\n".join(self._log_lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Mock provider — scripted responses, no API key needed
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
            "weather.condition": "clear",
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
    """Return a live provider instance for the given name string."""
    if provider_name == "claude":
        from roleplay.providers.claude_provider import ClaudeProvider

        return ClaudeProvider()
    from roleplay.providers.gemini import GeminiProvider

    return GeminiProvider()


async def run_poc(
    *,
    use_mock: bool = False,
    max_episodes: int = 3,
    config_path: Path | None = None,
    env_file: Path = Path(".env"),
    observer: ObserverHook | None = None,
) -> None:
    """Run the simulation end-to-end.

    Args:
        use_mock: Use the built-in mock provider (no API key required).
        max_episodes: Number of episodes to run.
        config_path: Optional path to a TOML scenario file.  If omitted the
            built-in Alice/Bob scenario is used.
        env_file: Path to a ``.env`` file with API keys.  Silently ignored if
            missing.
        observer: Optional :class:`~roleplay.engine.observer.ObserverHook` for
            real-time output or intervention.  ``_CliObserver`` is used when
            running from the command line.
    """
    from roleplay.config import load_env_file, load_scenario

    load_env_file(env_file)

    if config_path is not None:
        state, provider_name, file_episodes = load_scenario(config_path)
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
    else:
        provider = _resolve_provider(provider_name)

    engine = SimulationEngine(
        state=state, provider=provider, memory_store=memory_store, observer=observer
    )
    await engine.run(max_episodes=max_episodes)


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
    parser.add_argument(
        "--verbosity",
        type=int,
        default=1,
        choices=[0, 1],
        metavar="{0,1}",
        help=(
            "Output verbosity: 1=full dialog (default), "
            "0=episode summaries only (full dialog saved to a log file)"
        ),
    )
    args = parser.parse_args()

    # Silence noisy third-party loggers — especially httpx which would log the
    # full request URL including the API key.
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("roleplay").setLevel(logging.WARNING)

    observer = _CliObserver(verbosity=args.verbosity)

    # Print a brief header before the simulation starts.
    from roleplay.config import load_env_file, load_scenario

    load_env_file(args.env_file)
    if args.config is not None:
        _state, provider_name, file_episodes = load_scenario(args.config)
        ep_count = args.episodes if args.episodes != -1 else file_episodes
        party_names = ", ".join(p.name for p in _state.parties.values())
        env_name = _state.environment.name
        provider_label = "mock" if args.mock else provider_name
        print(f"\nScenario : {args.config.name}")
        print(f"Setting  : {env_name}")
        print(f"Parties  : {party_names}")
        print(f"Provider : {provider_label}")
        print(f"Episodes : {ep_count}")
    else:
        ep_count = args.episodes if args.episodes != -1 else 3
        print("\nScenario : built-in (Alice & Bob)")
        print(f"Provider : {'mock' if args.mock else 'gemini'}")
        print(f"Episodes : {ep_count}")

    asyncio.run(
        run_poc(
            use_mock=args.mock,
            max_episodes=args.episodes,
            config_path=args.config,
            env_file=args.env_file,
            observer=observer,
        )
    )

    total_ep = ep_count
    print(f"\n✓  Done — {total_ep} episode(s) complete\n")

    if args.verbosity == 0:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = Path(f"roleplay_{ts}.log")
        observer.write_log(log_path)
        print(f"Full dialog: {log_path}\n")


if __name__ == "__main__":
    main()
