"""Full CLI for the roleplay simulator.

Commands::

    roleplay run <scenario.yaml>                      # new simulation
    roleplay resume <session_id>                      # continue a saved session
    roleplay inspect <session_id>                     # dump session state
    roleplay list                                     # list all sessions
    roleplay fork <session_id>                        # branch a session
    roleplay forget <session_id> <party> <entry_id>  # delete a memory entry
    roleplay delete <session_id> --confirm            # delete a session

Set the DB path with ``--db`` (default ``./roleplay.db``).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, cast

import typer

from roleplay.config import load_env_file

if TYPE_CHECKING:
    from collections.abc import Coroutine

    from roleplay.core.simulation_state import SimulationState
    from roleplay.engine.observer import InjectionPayload, ObserverDirective
    from roleplay.engine.turn import Turn
    from roleplay.persistence import SqlitePersistenceLayer
    from roleplay.providers.registry import ProviderRegistry

app = typer.Typer(
    name="roleplay",
    help="Multi-party interaction simulator.",
    add_completion=False,
)

_DEFAULT_DB = "./roleplay.db"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _db_path(db: str) -> Path:
    return Path(db).expanduser()


def _eprint(msg: str) -> None:
    typer.echo(msg, err=True)


async def _open_layer(db: Path) -> SqlitePersistenceLayer:
    """Open and return a SqlitePersistenceLayer (caller must close)."""
    from roleplay.persistence import SqlitePersistenceLayer

    layer = SqlitePersistenceLayer(db_path=db)
    await layer.open()
    return layer


# ---------------------------------------------------------------------------
# Stream output
# ---------------------------------------------------------------------------


class StreamPrinter:
    """Prints episode/turn output to stdout in the spec format."""

    def print_episode_header(
        self, ep_index: int, simulated_time: str, *, total: int | None = None
    ) -> None:
        progress = f" / {total}" if total is not None else ""
        time_part = f" | {simulated_time}" if simulated_time else ""
        typer.echo(f"\n{'─' * 60}")
        typer.echo(f"  Episode {ep_index + 1}{progress}{time_part}")
        typer.echo(f"{'─' * 60}")

    def print_turn(self, party_name: str, output: str, state_changes: str = "") -> None:
        typer.echo(f"\n[{party_name}]")
        for line in output.strip().splitlines():
            typer.echo(f"  {line}")
        if state_changes:
            typer.echo(f"  STATE: {state_changes}")

    def print_episode_footer(
        self,
        ep_index: int,
        tokens: int,
        memories: int,
        simulated_time_end: str,
        wall_secs: float,
    ) -> None:
        time_part = f" → {simulated_time_end}" if simulated_time_end else ""
        typer.echo(
            f"\n[Ep {ep_index + 1}{time_part}] "
            f"Episode complete. "
            f"Tokens: {tokens:,}. "
            f"Memories written: {memories}. "
            f"⏱ {wall_secs:.1f}s"
        )


# ---------------------------------------------------------------------------
# ObserverHook implementation
# ---------------------------------------------------------------------------


async def _cli_check_goal(
    state: SimulationState, episode: object, provider: object
) -> tuple[str, bool]:
    """Ask the LLM whether the simulation goal has been met.

    Returns ``(status_text, met)`` where ``met`` is True when the response
    starts with "GOAL MET:".
    """
    from roleplay.providers.base import CompletionRequest, Provider

    # Use duck-typing so this works with real Episode objects and test mocks.
    if not hasattr(episode, "turns") or not episode.turns:  # type: ignore[union-attr]
        return ("(no turns to evaluate)", False)
    ep = episode

    dialog_text = "\n\n".join(f"{t.party_id.upper()}: {t.output}" for t in ep.turns)
    try:
        resp = await cast("Provider", provider).complete(
            CompletionRequest(
                prompt=(
                    f"Simulation goal: {state.config.goal}\n\n"
                    "Latest episode dialog:\n" + dialog_text + "\n\n"
                    "Has the goal been fully achieved based on the dialog above? "
                    "Reply with exactly one of:\n"
                    "GOAL MET: <one sentence explaining how it was achieved>\n"
                    "GOAL NOT MET: <one sentence on what still needs to happen>"
                ),
                max_output_tokens=120,
                temperature=0.1,
            )
        )
        text = resp.text.strip()
        met = text.upper().startswith("GOAL MET:")
        return (text, met)
    except Exception:
        return ("(goal check unavailable)", False)


class CliObserverHook:
    """ObserverHook that streams turn output and supports interactive pause."""

    def __init__(
        self,
        printer: StreamPrinter,
        *,
        interactive: bool = True,
        max_episodes: int | None = None,
        persistence: SqlitePersistenceLayer | None = None,
        session_id: str = "",
        provider: object = None,
    ) -> None:
        self._printer = printer
        self._interactive = interactive
        self._max_episodes = max_episodes
        self._persistence: SqlitePersistenceLayer | None = persistence
        self._session_id = session_id
        self._provider = provider
        self._pause_flag = threading.Event()
        self._ep_start: float = 0.0
        self.goal_achieved: bool = False
        self.goal_status: str = ""

        if interactive:
            self._start_input_thread()

    def _start_input_thread(self) -> None:
        def _poll() -> None:
            while True:
                try:
                    line = sys.stdin.readline()
                except (EOFError, OSError):
                    break
                if line.strip().lower() in ("p", ""):
                    self._pause_flag.set()

        threading.Thread(target=_poll, daemon=True).start()

    async def before_episode(self, state: SimulationState, episode_index: int) -> ObserverDirective:
        from roleplay.engine.observer import ObserverDirective

        self._ep_start = time.monotonic()
        sim_time = str(state.environment.state_snapshot().get("time.simulated", ""))
        self._printer.print_episode_header(episode_index, sim_time, total=self._max_episodes)

        if self._pause_flag.is_set():
            self._pause_flag.clear()
            inj_payload = await self._run_intervention(state)
            if inj_payload is None:
                return ObserverDirective.halt("User quit")
            return ObserverDirective.inject(inj_payload)

        return ObserverDirective.continue_()

    async def after_turn(self, state: SimulationState, turn: Turn) -> ObserverDirective:
        from roleplay.core.party import PartyKind
        from roleplay.engine.observer import ObserverDirective

        party_id = turn.party_id
        output = turn.output
        proposals = turn.state_update_proposals
        state_str = ", ".join(f"{k}={v}" for k, v in proposals.items()) if proposals else ""

        env = state.environment
        party = state.parties.get(party_id) or (env if env.id == party_id else None)
        if party is None:
            party_name = party_id
        elif party.kind is PartyKind.ENVIRONMENT:
            party_name = f"🌍 {party.id}"
        else:
            party_name = party.name

        self._printer.print_turn(party_name, output, state_str)
        return ObserverDirective.continue_()

    async def after_episode(self, state: SimulationState, episode: object) -> ObserverDirective:
        from roleplay.engine.observer import ObserverDirective

        # Use duck-typing so this works with real Episode objects and test mocks.
        ep = episode if hasattr(episode, "turns") else None
        turns = ep.turns if ep is not None else []  # type: ignore[union-attr]
        tokens = sum(t.prompt_tokens + t.completion_tokens for t in turns)
        sim_end = str(ep.simulated_time_end) if ep is not None else ""  # type: ignore[union-attr]
        ep_index = ep.index if ep is not None else 0  # type: ignore[union-attr]
        wall = time.monotonic() - self._ep_start

        self._printer.print_episode_footer(ep_index, tokens, 0, sim_end, wall)

        if self._persistence and self._session_id and ep is not None:
            with contextlib.suppress(Exception):
                await self._persistence.save_episode(self._session_id, ep)

        # Goal checking — only if a goal and provider are available.
        if state.config.goal and self._provider is not None and ep is not None and ep.turns:  # type: ignore[union-attr]
            goal_status, met = await _cli_check_goal(state, ep, self._provider)
            if met:
                self.goal_achieved = True
                self.goal_status = goal_status
                typer.echo(f"\n🎯 Goal achieved: {goal_status.removeprefix('GOAL MET:').strip()}")
                return ObserverDirective.halt(f"Goal achieved: {goal_status}")

        return ObserverDirective.continue_()

    async def _run_intervention(self, state: SimulationState) -> InjectionPayload | None:
        """Interactive pause. Returns InjectionPayload or None on quit."""
        from roleplay.engine.observer import InjectionPayload
        from roleplay.memory.store import MemoryEntry, MemoryKind

        payload = InjectionPayload()
        typer.echo(
            "\n⏸  Paused. Commands: [c]ontinue  [i]nject <text>  "
            '[s]tate <party> k=v  [m]emory <party> "<text>"  '
            "[o]rder <p1> <p2>  [q]uit  [?] help"
        )

        while True:
            try:
                loop = asyncio.get_running_loop()
                raw = await loop.run_in_executor(None, lambda: input("> ").strip())
            except (EOFError, KeyboardInterrupt):
                return None

            if not raw:
                continue

            cmd, _, rest = raw.partition(" ")
            cmd = cmd.lower()

            if cmd in ("c", "continue"):
                return payload

            elif cmd in ("q", "quit"):
                if self._persistence:
                    with contextlib.suppress(Exception):
                        await self._persistence.checkpoint(state)
                return None

            elif cmd in ("i", "inject"):
                payload = InjectionPayload(
                    context_override=(payload.context_override or "") + rest,
                    state_updates=payload.state_updates,
                    persona_overrides=payload.persona_overrides,
                    memory_writes=payload.memory_writes,
                    force_scheduler=payload.force_scheduler,
                )
                typer.echo(f"  inject: {rest!r}")

            elif cmd in ("s", "state"):
                parts = rest.split(None, 1)
                if len(parts) == 2 and "=" in parts[1]:
                    pid, kv = parts
                    k, _, v = kv.partition("=")
                    updates = dict(payload.state_updates)
                    updates.setdefault(pid, {})[k.strip()] = v.strip()
                    payload = InjectionPayload(
                        context_override=payload.context_override,
                        state_updates=updates,
                        persona_overrides=payload.persona_overrides,
                        memory_writes=payload.memory_writes,
                        force_scheduler=payload.force_scheduler,
                    )
                    typer.echo(f"  state {pid}: {k.strip()}={v.strip()}")
                else:
                    typer.echo("  Usage: state <party_id> <key>=<value>")

            elif cmd in ("m", "memory"):
                parts = rest.split(None, 1)
                if len(parts) == 2:
                    pid, text = parts
                    text = text.strip().strip('"')
                    entry = MemoryEntry(
                        party_id=pid,
                        kind=MemoryKind.EPISODIC,
                        content=text,
                        episode_index=0,
                    )
                    payload = InjectionPayload(
                        context_override=payload.context_override,
                        state_updates=payload.state_updates,
                        persona_overrides=payload.persona_overrides,
                        memory_writes=[*payload.memory_writes, entry],
                        force_scheduler=payload.force_scheduler,
                    )
                    typer.echo(f"  memory {pid}: {text!r}")
                else:
                    typer.echo('  Usage: memory <party_id> "<text>"')

            elif cmd in ("o", "order"):
                order = rest.split()
                payload = InjectionPayload(
                    context_override=payload.context_override,
                    state_updates=payload.state_updates,
                    persona_overrides=payload.persona_overrides,
                    memory_writes=payload.memory_writes,
                    force_scheduler=order,
                )
                typer.echo(f"  order: {order}")

            elif cmd in ("?", "help"):
                typer.echo(
                    "  [c]ontinue             Resume simulation\n"
                    "  [i]nject <text>        Add context override\n"
                    "  [s]tate <party> k=v    Update party state\n"
                    '  [m]emory <party> "t"   Write memory entry\n'
                    "  [o]rder <p1> <p2>...   Force speaker order\n"
                    "  [q]uit                 Checkpoint and exit\n"
                )
            else:
                typer.echo(f"  Unknown command: {cmd!r}  (? for help)")


# ---------------------------------------------------------------------------
# Shared async runner
# ---------------------------------------------------------------------------


def _run(coro: Coroutine[object, object, None]) -> None:
    # asyncio.run is safe to call multiple times in Python 3.12+; each call
    # creates and tears down its own event loop.
    asyncio.run(coro)


# ---------------------------------------------------------------------------
# roleplay run
# ---------------------------------------------------------------------------


@app.command()
def run(
    scenario: Annotated[Path, typer.Argument(help="Path to scenario YAML file")],
    max_episodes: Annotated[int | None, typer.Option("--max-episodes", "-n")] = None,
    provider: Annotated[str | None, typer.Option("--provider")] = None,
    output: Annotated[str, typer.Option("--output")] = "stream",
    interactive: Annotated[bool, typer.Option("--interactive/--no-interactive")] = True,
    db: Annotated[str, typer.Option("--db")] = _DEFAULT_DB,
    env_file: Annotated[str, typer.Option("--env-file")] = ".env",
) -> None:
    """Start a new simulation from a YAML scenario file."""
    _run(_run_cmd(scenario, max_episodes, provider, output, interactive, db, env_file))


def _make_registry() -> ProviderRegistry:
    """Build a ProviderRegistry pre-populated with all built-in providers."""
    from roleplay.providers.claude_provider import ClaudeProvider
    from roleplay.providers.gemini import GeminiProvider
    from roleplay.providers.mock import MockProvider
    from roleplay.providers.registry import ProviderRegistry

    registry = ProviderRegistry()
    registry.register("gemini", GeminiProvider())
    registry.register("claude", ClaudeProvider())
    registry.register("mock", MockProvider())
    return registry


async def _run_cmd(
    scenario: Path,
    max_episodes: int | None,
    provider_override: str | None,
    output: str,
    interactive: bool,
    db: str,
    env_file: str,
) -> None:
    from roleplay.engine.engine import SimulationEngine
    from roleplay.memory.store import InMemoryStore
    from roleplay.scenario_yaml import ValidationError, load_yaml_scenario

    load_env_file(Path(env_file))

    if not scenario.exists():
        typer.echo(f"Error: scenario file not found: {scenario}", err=True)
        raise typer.Exit(1)

    try:
        result = load_yaml_scenario(scenario)
    except ValidationError as exc:
        typer.echo(f"Validation error: {exc}", err=True)
        raise typer.Exit(1) from None
    except Exception as exc:
        typer.echo(f"Error loading scenario: {exc}", err=True)
        raise typer.Exit(1) from None

    state = result.state
    provider_name = provider_override or result.provider_name
    episodes = max_episodes if max_episodes is not None else result.max_episodes

    registry = _make_registry()
    try:
        provider_obj = registry.get(provider_name)
    except Exception as exc:
        typer.echo(f"Error: provider {provider_name!r}: {exc}", err=True)
        raise typer.Exit(1) from None

    layer = await _open_layer(_db_path(db))
    try:
        memory_store = InMemoryStore()
        printer = StreamPrinter()
        observer = CliObserverHook(
            printer,
            interactive=interactive,
            max_episodes=episodes,
            persistence=layer,
            session_id=state.config.session_id,
            provider=provider_obj,
        )

        await layer.create_session(state)
        engine = SimulationEngine(
            state=state,
            provider=provider_obj,
            memory_store=memory_store,
            observer=observer,
        )

        try:
            await engine.run(max_episodes=episodes)
        except KeyboardInterrupt:
            _eprint("\nInterrupted — checkpointing…")
            with contextlib.suppress(Exception):
                await layer.checkpoint(state)
            raise typer.Exit(3) from None
        except Exception as exc:
            _eprint(f"\nRuntime error: {exc}")
            with contextlib.suppress(Exception):
                await layer.checkpoint(state)
            raise typer.Exit(2) from None

        await layer.save_state(state)
        typer.echo("\n✅ Simulation complete.")
    finally:
        await layer.close()


# ---------------------------------------------------------------------------
# roleplay resume
# ---------------------------------------------------------------------------


@app.command()
def resume(
    session_id: Annotated[str, typer.Argument(help="Session ID to resume")],
    max_episodes: Annotated[int | None, typer.Option("--max-episodes", "-n")] = None,
    interactive: Annotated[bool, typer.Option("--interactive/--no-interactive")] = True,
    db: Annotated[str, typer.Option("--db")] = _DEFAULT_DB,
    env_file: Annotated[str, typer.Option("--env-file")] = ".env",
) -> None:
    """Resume a paused or interrupted simulation session."""
    _run(_resume_cmd(session_id, max_episodes, interactive, db, env_file))


async def _resume_cmd(
    session_id: str,
    max_episodes: int | None,
    interactive: bool,
    db: str,
    env_file: str,
) -> None:
    from roleplay.engine.engine import SimulationEngine
    from roleplay.memory.store import InMemoryStore
    from roleplay.persistence import SessionNotFoundError

    load_env_file(Path(env_file))
    layer = await _open_layer(_db_path(db))
    try:
        try:
            state = await layer.load_session(session_id)
        except SessionNotFoundError:
            typer.echo(f"Error: session {session_id!r} not found in {db}", err=True)
            raise typer.Exit(1) from None

        provider_name = state.config.default_provider
        provider_obj = _make_registry().get(provider_name)
        memory_store = InMemoryStore()

        ep_count = len(state.history.completed_episodes())
        typer.echo(f"Resuming session {session_id!r} from episode {ep_count + 1}…")

        printer = StreamPrinter()
        observer = CliObserverHook(
            printer,
            interactive=interactive,
            max_episodes=max_episodes,
            persistence=layer,
            session_id=session_id,
            provider=provider_obj,
        )
        engine = SimulationEngine(
            state=state,
            provider=provider_obj,
            memory_store=memory_store,
            observer=observer,
        )

        try:
            await engine.run(max_episodes=max_episodes)
        except KeyboardInterrupt:
            _eprint("\nInterrupted — checkpointing…")
            with contextlib.suppress(Exception):
                await layer.checkpoint(state)
            raise typer.Exit(3) from None
        except Exception as exc:
            _eprint(f"\nRuntime error: {exc}")
            with contextlib.suppress(Exception):
                await layer.checkpoint(state)
            raise typer.Exit(2) from None

        await layer.save_state(state)
        typer.echo("\n✅ Session complete.")
    finally:
        await layer.close()


# ---------------------------------------------------------------------------
# roleplay inspect
# ---------------------------------------------------------------------------


@app.command()
def inspect(
    session_id: Annotated[str, typer.Argument()],
    party: Annotated[str | None, typer.Option("--party")] = None,
    memories: Annotated[bool, typer.Option("--memories")] = False,
    episodes: Annotated[int, typer.Option("--episodes")] = 5,
    fmt: Annotated[str, typer.Option("--format")] = "text",
    db: Annotated[str, typer.Option("--db")] = _DEFAULT_DB,
) -> None:
    """Dump session state to stdout."""
    _run(_inspect_cmd(session_id, party, memories, episodes, fmt, db))


async def _inspect_cmd(
    session_id: str,
    party_filter: str | None,
    show_memories: bool,
    last_n_episodes: int,
    fmt: str,
    db: str,
) -> None:
    from typing import Any

    from roleplay.persistence import SessionNotFoundError

    layer = await _open_layer(_db_path(db))
    try:
        raw = await layer.export_json(session_id)
    except SessionNotFoundError:
        typer.echo(f"Error: session {session_id!r} not found", err=True)
        await layer.close()
        raise typer.Exit(1) from None
    finally:
        await layer.close()

    data: dict[str, Any] = dict(raw.items())

    if fmt == "json":
        typer.echo(json.dumps(data, indent=2))
        return

    sess: dict[str, Any] = data.get("session") or {}
    all_eps: list[dict[str, Any]] = data.get("episodes") or []
    parties: list[dict[str, Any]] = data.get("parties") or []
    mems: list[dict[str, Any]] = data.get("memories") or []

    last_saved = sess.get("last_saved_at", "unknown")
    typer.echo(f"\nSession: {session_id}  Episodes: {len(all_eps)}  Last saved: {last_saved}")

    typer.echo("\nParties:")
    for p in parties:
        pid = str(p.get("party_id", ""))
        if party_filter and pid != party_filter:
            continue
        kind = str(p.get("kind", "?")).upper()
        config_json = str(p.get("config_json", "{}"))
        try:
            cfg: dict[str, Any] = json.loads(config_json)
            pname = str(cfg.get("persona", {}).get("name", pid))
        except Exception:
            pname = pid

        state_json = str(p.get("state_json", "{}"))
        try:
            state_dict: dict[str, Any] = json.loads(state_json)
            state_str = ", ".join(f"{k}={v}" for k, v in list(state_dict.items())[:5])
        except Exception:
            state_str = state_json

        party_mems = [m for m in mems if m.get("party_id") == pid]
        typer.echo(f"  {pid} ({pname}) [{kind}]")
        typer.echo(f"    State: {state_str or '(empty)'}")
        typer.echo(f"    Memories: {len(party_mems)} entries")
        if show_memories:
            for m in party_mems:
                importance = float(m.get("importance") or 0.0)
                content = str(m.get("content", ""))[:80]
                typer.echo(f"      [{m.get('kind', '?')} imp={importance:.2f}] {content}")

    recent = all_eps[-last_n_episodes:] if last_n_episodes else all_eps
    typer.echo(f"\nRecent episodes (last {len(recent)}):")
    for ep in recent:
        idx = ep.get("episode_index", "?")
        t_start = str(ep.get("simulated_time_start") or "")
        t_end = str(ep.get("simulated_time_end") or "")
        turns: list[dict[str, Any]] = ep.get("turns") or []
        tokens = sum(
            int(t.get("prompt_tokens") or 0) + int(t.get("completion_tokens") or 0) for t in turns
        )
        time_range = f"{t_start} → {t_end}" if t_start or t_end else "—"
        typer.echo(f"  Ep {idx}  {time_range}  Turns: {len(turns)}  Tokens: {tokens:,}")


# ---------------------------------------------------------------------------
# roleplay list
# ---------------------------------------------------------------------------


@app.command(name="list")
def list_sessions(
    db: Annotated[str, typer.Option("--db")] = _DEFAULT_DB,
    fmt: Annotated[str, typer.Option("--format")] = "text",
) -> None:
    """List all sessions in the database."""
    _run(_list_cmd(db, fmt))


async def _list_cmd(db: str, fmt: str) -> None:
    layer = await _open_layer(_db_path(db))
    summaries = await layer.list_sessions()
    await layer.close()

    if fmt == "json":
        out = [
            {
                "session_id": s.session_id,
                "episode_count": s.episode_count,
                "party_count": s.party_count,
                "last_saved_at": s.last_saved_at.isoformat() if s.last_saved_at else None,
                "parent_session_id": s.parent_session_id,
            }
            for s in summaries
        ]
        typer.echo(json.dumps(out, indent=2))
        return

    if not summaries:
        typer.echo("No sessions found.")
        return

    typer.echo(f"\n{'SESSION ID':<36}  {'EPS':>4}  {'PARTIES':>7}  LAST SAVED")
    typer.echo("─" * 70)
    for s in summaries:
        last = s.last_saved_at.strftime("%Y-%m-%d %H:%M") if s.last_saved_at else "—"
        fork = f"  (fork of {s.parent_session_id})" if s.parent_session_id else ""
        typer.echo(f"{s.session_id:<36}  {s.episode_count:>4}  {s.party_count:>7}  {last}{fork}")


# ---------------------------------------------------------------------------
# roleplay fork
# ---------------------------------------------------------------------------


@app.command()
def fork(
    session_id: Annotated[str, typer.Argument()],
    new_id: Annotated[str | None, typer.Option("--new-id")] = None,
    db: Annotated[str, typer.Option("--db")] = _DEFAULT_DB,
) -> None:
    """Create a branched copy of a session at its current state."""
    _run(_fork_cmd(session_id, new_id, db))


async def _fork_cmd(session_id: str, new_id: str | None, db: str) -> None:
    import uuid

    from roleplay.persistence import SessionNotFoundError

    layer = await _open_layer(_db_path(db))
    new_session_id = new_id or f"{session_id}-fork-{str(uuid.uuid4())[:8]}"
    try:
        await layer.fork(session_id, new_session_id)
    except SessionNotFoundError:
        typer.echo(f"Error: session {session_id!r} not found", err=True)
        await layer.close()
        raise typer.Exit(1) from None
    finally:
        await layer.close()

    typer.echo(f"Forked: {session_id} → {new_session_id}")
    typer.echo(f"Run the fork with: roleplay resume {new_session_id} --db {db}")


# ---------------------------------------------------------------------------
# roleplay forget
# ---------------------------------------------------------------------------


@app.command()
def forget(
    session_id: Annotated[str, typer.Argument()],
    party_id: Annotated[str, typer.Argument()],
    entry_id: Annotated[str, typer.Argument()],
    db: Annotated[str, typer.Option("--db")] = _DEFAULT_DB,
) -> None:
    """Hard-delete a specific memory entry."""
    _run(_forget_cmd(session_id, party_id, entry_id, db))


async def _forget_cmd(session_id: str, party_id: str, entry_id: str, db: str) -> None:
    layer = await _open_layer(_db_path(db))
    try:
        await layer.delete_memory(session_id, entry_id)
    finally:
        await layer.close()
    typer.echo(f"Deleted memory entry {entry_id!r} in session {session_id!r}.")


# ---------------------------------------------------------------------------
# roleplay delete
# ---------------------------------------------------------------------------


@app.command()
def delete(
    session_id: Annotated[str, typer.Argument()],
    confirm: Annotated[bool, typer.Option("--confirm")] = False,
    db: Annotated[str, typer.Option("--db")] = _DEFAULT_DB,
) -> None:
    """Delete a session and all its data (requires --confirm)."""
    if not confirm:
        typer.echo("Safety: pass --confirm to delete. This is irreversible.", err=True)
        raise typer.Exit(1)
    _run(_delete_cmd(session_id, db))


async def _delete_cmd(session_id: str, db: str) -> None:
    from roleplay.persistence import SessionNotFoundError

    layer = await _open_layer(_db_path(db))
    try:
        await layer.delete_session(session_id)
    except SessionNotFoundError:
        typer.echo(f"Error: session {session_id!r} not found", err=True)
        await layer.close()
        raise typer.Exit(1) from None
    finally:
        await layer.close()
    typer.echo(f"Deleted session {session_id!r}.")


# ---------------------------------------------------------------------------
# roleplay export
# ---------------------------------------------------------------------------


@app.command()
def export(
    session_id: Annotated[str, typer.Argument(help="Session ID to export")],
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Write to FILE instead of stdout"),
    ] = None,
    db: Annotated[str, typer.Option(help="Path to the SQLite database file")] = "roleplay.db",
) -> None:
    """Export a session\'s full history to a portable JSON document.

    The export includes scenario config, party personas, all episodes with
    per-turn output, and episode summaries — suitable for downstream analysis.

    \b
    Examples:
      roleplay export abc123
      roleplay export abc123 --output session_abc123.json
      roleplay export abc123 -o - | jq .episodes[].summary
    """
    _run(_export_cmd(session_id, output, db))


async def _export_cmd(session_id: str, output: Path | None, db: str) -> None:
    from datetime import UTC, datetime

    from roleplay.persistence import SessionNotFoundError

    layer = await _open_layer(_db_path(db))
    try:
        state = await layer.load_session(session_id)
        history = await layer.load_history(session_id)
    except SessionNotFoundError:
        typer.echo(f"Error: session {session_id!r} not found", err=True)
        await layer.close()
        raise typer.Exit(1) from None
    finally:
        await layer.close()

    cfg = state.config

    parties_out = []
    for p in state.parties.values():
        pd: dict[str, object] = {"id": p.id, "name": p.name, "kind": p.kind.value}
        persona_d = p.persona.to_export_dict()
        if persona_d:
            pd["persona"] = persona_d
        snap = dict(p.state_snapshot())
        if snap:
            pd["initial_state"] = snap
        parties_out.append(pd)

    env = state.environment
    env_out: dict[str, object] = {"id": env.id, "name": env.name}
    env_persona = env.persona.to_export_dict()
    if env_persona:
        env_out["persona"] = env_persona
    env_snap = dict(env.state_snapshot())
    if env_snap:
        env_out["initial_state"] = env_snap

    envs_out = []
    for env_id in state.environments.ids():
        named = state.environments.get(env_id)
        if named is not None:
            ed: dict[str, object] = {
                "id": named.id,
                "name": named.name,
                "description": named.description,
            }
            if named.state:
                ed["state"] = dict(named.state)
            envs_out.append(ed)

    episodes_out = [
        {
            "episode": ep.index,
            "summary": ep.summary,
            "turns": [
                {
                    "party_id": t.party_id,
                    "output": t.output,
                    "state_update_proposals": t.state_update_proposals,
                }
                for t in ep.turns
            ],
        }
        for ep in history.completed_episodes()
    ]

    doc: dict[str, object] = {
        "export_version": "1",
        "exported_at": datetime.now(tz=UTC).isoformat(),
        "session": {
            "id": session_id,
            "episode_count": len(episodes_out),
        },
        "config": {
            "goal": cfg.goal or None,
            "default_provider": cfg.default_provider,
            "context_window_episodes": cfg.context_window_episodes,
            "memory_max_entries": cfg.memory_max_entries,
            "environment_reactive": cfg.environment_reactive,
        },
        "environment": env_out,
        "parties": parties_out,
        "episodes": episodes_out,
    }
    if envs_out:
        doc["environments"] = envs_out

    payload = json.dumps(doc, indent=2, ensure_ascii=False)

    if output is None or str(output) == "-":
        typer.echo(payload)
    else:
        output.write_text(payload, encoding="utf-8")
        typer.echo(f"Exported {len(episodes_out)} episode(s) to {output}")


# ---------------------------------------------------------------------------
# Generate command
# ---------------------------------------------------------------------------


@app.command()
def generate(
    prompt: Annotated[str, typer.Argument(help="Natural-language description of the scenario")],
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Write YAML to this file instead of stdout"),
    ] = None,
    provider: Annotated[
        str,
        typer.Option("--provider", "-p", help="Provider to use: gemini | claude | mock"),
    ] = "gemini",
    fix_cycles: Annotated[
        int,
        typer.Option(
            "--fix-cycles",
            "-f",
            help="Validation-correction cycles after generation (0-5, default 0)",
            min=0,
            max=5,
        ),
    ] = 0,
) -> None:
    """Generate a YAML scenario file from a natural-language prompt.

    Use --fix-cycles to automatically validate the output and ask the LLM to
    correct any errors, up to the specified number of times.
    """
    import asyncio

    from roleplay.api.runner import _build_registry
    from roleplay.generate import generate_yaml_scenario
    from roleplay.providers.base import ProviderError

    registry = _build_registry()
    if provider not in registry:
        available = registry.names()
        typer.echo(
            f"Error: provider {provider!r} not available. Available: {available}",
            err=True,
        )
        raise typer.Exit(1)

    prov = registry.get(provider)

    try:
        yaml_text = asyncio.run(generate_yaml_scenario(prompt, prov, fix_cycles=fix_cycles))
    except ProviderError as exc:
        typer.echo(f"✗ Provider error: {exc}", err=True)
        raise typer.Exit(1) from None
    except Exception as exc:
        typer.echo(f"✗ Generation failed: {exc}", err=True)
        raise typer.Exit(1) from None

    if output is not None:
        output.write_text(yaml_text, encoding="utf-8")
        typer.echo(f"✓ Scenario written to {output}")
    else:
        typer.echo(yaml_text)


# ---------------------------------------------------------------------------
# roleplay validate
# ---------------------------------------------------------------------------


@app.command()
def validate(
    scenario: Annotated[Path, typer.Argument(help="Path to YAML scenario file")],
) -> None:
    """Validate a YAML scenario file without creating a session."""
    from roleplay.scenario_yaml import ValidationError, load_yaml_scenario

    if not scenario.exists():
        typer.echo(f"Error: file not found: {scenario}", err=True)
        raise typer.Exit(1)

    content = scenario.read_text(encoding="utf-8")
    if not content.strip():
        typer.echo(f"Error: {scenario} is empty", err=True)
        raise typer.Exit(1)

    try:
        load_yaml_scenario(scenario)
        typer.echo(f"✓ {scenario} is valid.")
    except ValidationError as exc:
        typer.echo(f"✗ {scenario} has {len(exc.errors)} error(s):", err=True)
        for err in exc.errors:
            typer.echo(f"  • {err}", err=True)
        raise typer.Exit(1) from None
    except Exception as exc:
        typer.echo(f"✗ Failed to parse {scenario}: {exc}", err=True)
        raise typer.Exit(1) from None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Launch the roleplay CLI."""
    app()
