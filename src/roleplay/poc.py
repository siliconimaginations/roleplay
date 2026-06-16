"""Proof-of-concept scenario runner — **legacy**.

.. deprecated::
   ``poc.py`` is superseded by the Stage 7 CLI.  Use ``roleplay run`` instead::

       uv run roleplay run scenarios/example.yaml

   ``poc.py`` will be removed in a future release.  It continues to work with
   existing ``.toml`` scenario files for now.

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

    # Medium verbosity — 80-char excerpts per turn + AI summary (good for long runs):
    uv run python -m roleplay.poc --config scenarios/my.toml --verbosity 2

    # Spinner while LLM generates (dots printed to stderr):
    uv run python -m roleplay.poc --watch

    # Resume after a crash (checkpoint auto-saved per episode):
    uv run python -m roleplay.poc --config scenarios/my.toml --resume
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import datetime
import json
import logging
import shutil
import sys
import textwrap
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

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
    from collections.abc import Awaitable

    from roleplay.engine.observer import ObserverHook
    from roleplay.engine.turn import Turn


class _Summarizable(Protocol):
    """Minimal structural type for providers that can summarise text."""

    async def complete(self, request: CompletionRequest) -> CompletionResponse: ...


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
            * ``0`` — AI-generated one-line summary per episode; full dialog
              collected and written to a log file via :meth:`write_log`.
            * ``2`` — first 80 characters of each turn + AI summary per
              episode.  Good for monitoring long runs without full dialog.
        watch: When ``True``, print an animated spinner to stderr while
            ``provider.complete()`` is in flight.  Cleared when the response
            arrives.
        max_episodes: Total episode count, used to print ``N / M`` in the
            header.  ``0`` means unknown (header shows only ``N``).  Set
            automatically by :func:`run_poc`.
        provider: LLM provider used to generate summaries at verbosity 0/2.
            Set automatically by :func:`run_poc` after the provider is
            resolved.  ``None`` produces a fallback "(no summarizer)" line.
    """

    verbosity: int = 1
    watch: bool = False
    max_episodes: int = 0
    provider: _Summarizable | None = None
    _width: int = field(default_factory=lambda: min(shutil.get_terminal_size().columns, 100))
    _episode_turns: list[Turn] = field(default_factory=list, init=False, repr=False)
    _log_lines: list[str] = field(default_factory=list, init=False, repr=False)
    _episode_start_env_state: dict[str, object] = field(
        default_factory=dict, init=False, repr=False
    )
    # Timing
    _episode_start_time: float = field(default=0.0, init=False, repr=False)
    _session_start_time: float = field(default_factory=time.monotonic, init=False, repr=False)
    # Model tracking (per-episode and session-level)
    _episode_models: set[str] = field(default_factory=set, init=False, repr=False)
    _default_model: str = field(default="", init=False, repr=False)
    # model → [episode_count, prompt_tokens, completion_tokens]
    _model_stats: dict[str, list[int]] = field(default_factory=dict, init=False, repr=False)
    # Goal trend
    _goal_met_count: int = field(default=0, init=False, repr=False)
    _goal_check_count: int = field(default=0, init=False, repr=False)
    # Separate episode counter so write_session_summary is accurate even when
    # multiple models are used in the same episode (per-model stats would
    # overcount if we summed them).
    _total_episodes: int = field(default=0, init=False, repr=False)
    # Saved for write_env_snapshot / write_session_summary
    _final_state: SimulationState | None = field(default=None, init=False, repr=False)

    async def before_episode(
        self,
        state: SimulationState,
        episode_index: int,
    ) -> ObserverDirective:
        self._episode_turns.clear()
        self._episode_models.clear()
        self._episode_start_time = time.monotonic()
        # Snapshot environment state now so after_episode can diff it.
        self._episode_start_env_state = dict(state.environment.state_snapshot())

        # Episode counter: "Episode N / M" when total is known, "Episode N" otherwise.
        ep_num = str(episode_index + 1)
        ep_label = (
            f"Episode {ep_num} / {self.max_episodes}" if self.max_episodes else f"Episode {ep_num}"
        )
        rule = "─" * max(0, self._width - 6 - len(ep_label))
        header = f"\n{'─' * 3}  {ep_label}  {rule}"
        print(header)
        self._log_lines.append(header)
        return ObserverDirective.continue_()

    async def after_turn(
        self,
        state: SimulationState,
        turn: Turn,
    ) -> ObserverDirective:
        self._episode_turns.append(turn)

        # Track model usage (use getattr for CoreTurn compat in tests).
        model = getattr(turn, "model_used", "")
        if model:
            self._episode_models.add(model)
            stats = self._model_stats.setdefault(model, [0, 0, 0])
            stats[1] += getattr(turn, "prompt_tokens", 0)
            stats[2] += getattr(turn, "completion_tokens", 0)

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

        if self.verbosity == 1:
            # Full dialog streamed in real time.
            for line in lines:
                print(line)
        elif self.verbosity == 2:
            # 80-char excerpt per turn — enough context without full noise.
            raw = turn.output.strip().replace("\n", " ")
            excerpt = raw[:80] + ("…" if len(raw) > 80 else "")
            print(f"\n  {label}: {excerpt}")

        return ObserverDirective.continue_()

    async def after_episode(
        self,
        state: SimulationState,
        episode: object,
    ) -> ObserverDirective:
        self._final_state = state

        if self.verbosity in (0, 2):
            await self._print_episode_summary(state)

        # Model notice + timing ------------------------------------------------
        elapsed = time.monotonic() - self._episode_start_time
        self._total_episodes += 1
        # Increment per-model episode counter (tracks how many episodes each model ran).
        for model in self._episode_models:
            self._model_stats.setdefault(model, [0, 0, 0])[0] += 1

        non_default = {m for m in self._episode_models if m and m != self._default_model}
        timing_str = f"{elapsed:.1f}s"
        if non_default:
            model_label = ", ".join(sorted(non_default))
            info_line = f"  ⚡ {model_label}  ·  {timing_str}"
        else:
            info_line = f"  ⏱ {timing_str}"
        print(info_line)
        self._log_lines.append(info_line)

        # Goal check -----------------------------------------------------------
        if state.config.goal:
            self._goal_check_count += 1
            status, met = await self._check_goal_progress(state)
            if met:
                self._goal_met_count += 1
            tally = f"(goal achieved {self._goal_met_count} / {self._goal_check_count} ep)"
            goal_line = f"  ⊙ {status}  {tally}"
            print(goal_line)
            self._log_lines.append(goal_line)
            if met:
                return ObserverDirective.halt(reason="Goal achieved")

        return ObserverDirective.continue_()

    def _build_dialog_text(self, state: SimulationState) -> str:
        """Build a plain-text transcript of this episode's turns."""
        parts: list[str] = []
        for turn in self._episode_turns:
            party = state.get_party(turn.party_id)
            parts.append(f"{party.name}: {turn.output.strip()}")
        return "\n\n".join(parts)

    async def _print_episode_summary(self, state: SimulationState) -> None:
        """Print an AI-generated paragraph + environment state diff for the episode."""
        dialog_text = self._build_dialog_text(state)
        summary = await self._summarize(dialog_text)

        # Show only the environment keys that changed *during* this episode.
        curr_env = dict(state.environment.state_snapshot())
        state_changes = {
            k: (self._episode_start_env_state.get(k), v)
            for k, v in curr_env.items()
            if self._episode_start_env_state.get(k) != v
        }

        indent = "  "
        wrap_width = self._width - len(indent)
        print(
            textwrap.fill(
                summary,
                width=wrap_width,
                initial_indent=indent,
                subsequent_indent=indent,
            )
        )
        if state_changes:
            for k, (old, new) in state_changes.items():
                print(f"  ↳ {k}: {old!r} → {new!r}")

    async def _summarize(self, dialog_text: str) -> str:
        """Return a 1-2 sentence LLM summary of *dialog_text*, or a fallback string."""
        if self.provider is None:
            return "(no summarizer configured)"
        if not dialog_text.strip():
            return "(no dialog recorded this episode)"
        prompt = (
            "You are summarizing a roleplay scene. "
            "Write 1-2 sentences describing what happened, any decisions reached, "
            "and key dynamics. Be specific and concise. "
            "Output only the summary — no bullet points, no headers, no preamble.\n\n"
            "Dialog:\n" + dialog_text + "\n\nSummary:"
        )
        try:
            resp = await self.provider.complete(
                CompletionRequest(prompt=prompt, max_output_tokens=200)
            )
            text = str(resp.text).strip()
            return text or "(model returned empty summary)"
        except Exception as exc:
            logger.debug("Episode summary generation failed: %s", exc)
            return "(summary unavailable)"

    async def _check_goal_progress(self, state: SimulationState) -> tuple[str, bool]:
        """Ask the LLM if the simulation goal is met; return (one-sentence status, met)."""
        if self.provider is None:
            return ("(no provider — cannot evaluate goal)", False)
        dialog_text = self._build_dialog_text(state)
        prompt = (
            f"Goal: {state.config.goal}\n\n"
            f"Dialog:\n{dialog_text}\n\n"
            "Has the goal been fully achieved? "
            "Reply with exactly one sentence. "
            "Start with 'Goal met:' if yes, or 'Goal not yet met:' if no. "
            "Output only that sentence — nothing else.\n\nAnswer:"
        )
        try:
            resp = await self.provider.complete(
                CompletionRequest(prompt=prompt, max_output_tokens=80)
            )
            text = str(resp.text).strip()
            # Strip a leading "Answer:" echo in case the model repeats the anchor
            if text.lower().startswith("answer:"):
                text = text[len("answer:") :].strip()
            return (text, text.lower().startswith("goal met:"))
        except Exception as exc:
            logger.debug("Goal progress check failed: %s", exc)
            return ("(goal check unavailable)", False)

    async def _spin_while(
        self, label: str, coro: Awaitable[CompletionResponse]
    ) -> CompletionResponse:
        """Run *coro* while printing an animated spinner to stderr.

        The spinner is ``label ....`` with one dot added every 0.4 s.
        The line is cleared (ANSI escape) when the coroutine finishes.
        Falls back to a plain await if stderr is not a TTY.
        """
        if not self.watch or not sys.stderr.isatty():
            return await coro

        async def _spinner(prefix: str) -> None:
            dot_count = 0
            while True:
                dots = "." * (dot_count % 6 + 1)
                sys.stderr.write(f"\r  {prefix} {dots:<6}")
                sys.stderr.flush()
                await asyncio.sleep(0.4)
                dot_count += 1

        spinner_task = asyncio.create_task(_spinner(label))
        try:
            result = await coro
        finally:
            spinner_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await spinner_task
            # Clear the spinner line.
            sys.stderr.write("\r" + " " * (len(label) + 10) + "\r")
            sys.stderr.flush()
        return result

    def write_log(self, path: Path) -> None:
        """Write the full dialog collected during the run to *path*."""
        path.write_text("\n".join(self._log_lines), encoding="utf-8")

    def write_session_summary(self) -> None:
        """Print a per-model usage table and total wall-clock duration."""
        total_seconds = time.monotonic() - self._session_start_time
        m, s = divmod(int(total_seconds), 60)
        duration_str = f"{m}m {s:02d}s" if m else f"{total_seconds:.1f}s"

        rule = "─" * max(0, self._width - 22)
        print(f"\n{'─' * 3}  Session summary  {rule}")
        print(f"  Episodes  : {self._total_episodes}")
        print(f"  Duration  : {duration_str}")
        if self._model_stats:
            print("  Models")
            for model, (eps, prompt_tok, comp_tok) in sorted(self._model_stats.items()):
                print(f"    {model:<36}  {eps:>3} ep  {prompt_tok + comp_tok:>9,} tok")
        print()

    def write_env_snapshot(self) -> None:
        """Print the final environment state as a labelled key-value block."""
        if self._final_state is None:
            return
        snap = self._final_state.environment.state_snapshot()
        rule = "─" * max(0, self._width - 30)
        print(f"{'─' * 3}  Final environment state  {rule}")
        if snap:
            key_width = max(len(k) for k in snap) + 2
            for k, v in snap.items():
                print(f"  {k:<{key_width}} {v}")
        else:
            print("  (no state)")
        print()


# ---------------------------------------------------------------------------
# Watch-mode provider wrapper — adds spinner around every complete() call
# ---------------------------------------------------------------------------


@dataclass
class _WatchProvider:
    """Wraps any provider to show an animated spinner during completion calls.

    Instantiated by :func:`run_poc` when ``--watch`` is active.  The spinner
    prints to stderr so it does not pollute the episode dialog on stdout.
    """

    _inner: _Summarizable
    _observer: _CliObserver

    @property
    def default_model(self) -> str:
        return getattr(self._inner, "default_model", "")

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        return await self._observer._spin_while("Generating", self._inner.complete(request))


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


# ---------------------------------------------------------------------------
# Checkpoint helpers — lightweight JSON-based resume support
# ---------------------------------------------------------------------------

_CHECKPOINT_VERSION = 1


def _load_checkpoint(path: Path, max_episodes: int, resume: bool) -> int:
    """Return the number of already-completed episodes, or 0.

    If *resume* is ``False`` or the checkpoint does not exist, returns 0.
    If a checkpoint is found and *resume* is ``True``, prints a notice and
    returns the completed episode count so the caller can skip them.
    If the checkpoint says all episodes are done, exits immediately.
    """
    if not resume or not path.exists():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        done = int(data.get("episodes_completed", 0))
        total = int(data.get("max_episodes", max_episodes))
    except Exception:
        print(f"[checkpoint] Could not read {path} — starting from scratch.")
        return 0

    if done >= total:
        print(f"[checkpoint] All {total} episodes already completed. Nothing to resume.")
        raise SystemExit(0)

    print(f"[checkpoint] Resuming from episode {done + 1} / {total}  ({path})")
    return done


def _write_checkpoint(path: Path, episodes_completed: int, max_episodes: int) -> None:
    """Write (or overwrite) a checkpoint file after each completed episode."""
    data = {
        "version": _CHECKPOINT_VERSION,
        "episodes_completed": episodes_completed,
        "max_episodes": max_episodes,
        "saved_at": datetime.datetime.now(datetime.UTC).isoformat(),
    }
    try:
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not write checkpoint to %s: %s", path, exc)


@dataclass
class _CheckpointObserver:
    """Wraps another observer and writes a checkpoint after every episode.

    This is transparent to the inner observer — all directives pass through
    unchanged.  The checkpoint is written *after* the inner observer's
    ``after_episode`` returns so that a halt directive still produces a
    checkpoint for the completed episode.
    """

    inner: ObserverHook | None
    checkpoint_path: Path
    max_episodes: int
    _episodes_completed: int = field(default=0, init=False)

    async def before_episode(
        self,
        state: SimulationState,
        episode_index: int,
    ) -> ObserverDirective:
        if self.inner is not None:
            return await self.inner.before_episode(state, episode_index)
        return ObserverDirective.continue_()

    async def after_turn(
        self,
        state: SimulationState,
        turn: Turn,
    ) -> ObserverDirective:
        if self.inner is not None:
            return await self.inner.after_turn(state, turn)
        return ObserverDirective.continue_()

    async def after_episode(
        self,
        state: SimulationState,
        episode: object,
    ) -> ObserverDirective:
        directive = ObserverDirective.continue_()
        if self.inner is not None:
            directive = await self.inner.after_episode(state, episode)
        self._episodes_completed += 1
        _write_checkpoint(self.checkpoint_path, self._episodes_completed, self.max_episodes)
        return directive


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
    resume: bool = False,
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
        resume: If ``True``, look for a ``.checkpoint.json`` next to
            *config_path* (or ``poc.checkpoint.json`` for the built-in
            scenario) and skip already-completed episodes.
    """
    from roleplay.config import load_env_file, load_scenario

    load_env_file(env_file)

    if config_path is not None:
        state, provider_name, file_episodes = load_scenario(config_path)
        if max_episodes == -1:
            max_episodes = file_episodes
        if use_mock:
            provider_name = "mock"
        checkpoint_path = config_path.with_suffix(".checkpoint.json")
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
        checkpoint_path = Path("poc.checkpoint.json")

    # ── Checkpoint resume ────────────────────────────────────────────────────
    episodes_done = _load_checkpoint(checkpoint_path, max_episodes, resume)

    memory_store = InMemoryStore()

    if provider_name == "mock":
        provider: _Summarizable = _MockProvider()
    else:
        provider = _resolve_provider(provider_name)  # type: ignore[assignment]

    # Give the CLI observer access to the provider so it can generate
    # AI summaries at verbosity 0/2, and pass the total episode count and
    # default model so it can render the counter and detect model switches.
    if isinstance(observer, _CliObserver):
        observer.provider = provider
        observer.max_episodes = max_episodes
        observer._default_model = getattr(provider, "default_model", "")
        if observer.watch:
            provider = _WatchProvider(_inner=provider, _observer=observer)

    # ── Checkpoint-aware observer wrapper ────────────────────────────────────
    # Wrap the caller's observer to write a checkpoint after every episode.
    wrapped_observer: ObserverHook | None = (
        _CheckpointObserver(
            inner=observer,
            checkpoint_path=checkpoint_path,
            max_episodes=max_episodes,
        )
        if checkpoint_path is not None
        else observer
    )

    engine = SimulationEngine(
        state=state,
        provider=provider,
        memory_store=memory_store,
        observer=wrapped_observer,
    )
    await engine.run(max_episodes=max_episodes - episodes_done if episodes_done else max_episodes)


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
        choices=[0, 1, 2],
        metavar="{0,1,2}",
        help=(
            "Output verbosity: "
            "1=full dialog (default), "
            "0=episode summaries only (dialog saved to log file), "
            "2=80-char excerpts + AI summary (good for long runs)"
        ),
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Print animated spinner to stderr while the LLM generates (useful with Gemma 4 26B)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help=(
            "Resume from .checkpoint.json if present "
            "(checkpoint is written automatically after every episode)"
        ),
    )
    args = parser.parse_args()

    # Silence noisy third-party loggers — especially httpx which would log the
    # full request URL including the API key.
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("roleplay").setLevel(logging.WARNING)

    observer = _CliObserver(verbosity=args.verbosity, watch=args.watch)

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
            resume=args.resume,
        )
    )

    # Post-run output ----------------------------------------------------------
    observer.write_session_summary()

    if args.verbosity in (0, 2):
        observer.write_env_snapshot()

    if args.verbosity == 0:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = Path(f"roleplay_{ts}.log")
        observer.write_log(log_path)
        print(f"Full dialog: {log_path}\n")


if __name__ == "__main__":
    main()
