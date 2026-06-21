"""Background simulation runner for the REST API.

Each active session gets one :class:`SessionRunner` which manages an
``asyncio.Task`` driving :class:`~roleplay.engine.engine.SimulationEngine`.
Turn events are broadcast to WebSocket subscribers via per-subscriber queues.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Literal

from roleplay.engine.observer import ObserverDirective

if TYPE_CHECKING:
    from roleplay.core.episode import Episode, Turn
    from roleplay.core.simulation_state import SimulationState
    from roleplay.persistence.sqlite import SqlitePersistenceLayer
    from roleplay.providers.base import Provider

logger = logging.getLogger(__name__)

RunStatusLiteral = Literal["idle", "running", "paused", "done", "error"]


def _build_registry() -> ProviderRegistry:  # type: ignore[name-defined]  # noqa: F821
    """Build and return a ProviderRegistry populated with all available providers."""
    from roleplay.providers.claude_provider import ClaudeProvider
    from roleplay.providers.gemini import GeminiProvider
    from roleplay.providers.mock import MockProvider
    from roleplay.providers.registry import ProviderRegistry

    registry = ProviderRegistry()
    registry.register("mock", MockProvider())
    try:
        registry.register("gemini", GeminiProvider())
    except Exception:
        logger.debug("GeminiProvider not available (missing API key?)")
    try:
        registry.register("claude", ClaudeProvider())
    except Exception:
        logger.debug("ClaudeProvider not available (missing API key?)")
    return registry


_QUEUE_MAXSIZE = 512


class ApiObserverHook:
    """Bridges engine lifecycle callbacks into the runner's event queue."""

    def __init__(
        self,
        runner: SessionRunner,
        provider: Provider,
        layer: SqlitePersistenceLayer,
    ) -> None:
        self._runner = runner
        self._provider = provider
        self._layer = layer

    async def before_episode(
        self,
        state: SimulationState,
        episode_index: int,
    ) -> ObserverDirective:
        # Check for pause request
        if self._runner._pause_requested:
            self._runner._pause_requested = False
            self._runner.status = "paused"
            return ObserverDirective.halt("Paused via API request")

        # Broadcast episode_start
        await self._runner._broadcast({"type": "episode_start", "episode": episode_index})

        # Consume any queued injection and apply it to this episode's context.
        injection_text = self._runner._pending_injection
        if injection_text:
            self._runner._pending_injection = None
            from roleplay.engine.observer import InjectionPayload

            return ObserverDirective.inject(InjectionPayload(context_override=injection_text))

        return ObserverDirective.continue_()

    async def after_turn(
        self,
        state: SimulationState,
        turn: Turn,
    ) -> ObserverDirective:
        ep_index = len(state.history.completed_episodes())
        await self._runner._broadcast(
            {
                "type": "turn",
                "episode": ep_index,
                "party_id": turn.party_id,
                "output": turn.output,
                "state_update_proposals": dict(turn.state_update_proposals),
            }
        )
        return ObserverDirective.continue_()

    async def after_episode(
        self,
        state: SimulationState,
        episode: object,
    ) -> ObserverDirective:
        ep: Episode = episode  # type: ignore[assignment]
        ep_index = len(state.history.completed_episodes()) - 1

        # Generate a 1-2 sentence summary via LLM; fall back to empty string on error.
        summary = ""
        if ep.turns:
            dialog_text = "\n\n".join(f"{t.party_id.upper()}: {t.output}" for t in ep.turns)
            try:
                from roleplay.providers.base import CompletionRequest

                resp = await self._provider.complete(
                    CompletionRequest(
                        prompt=(
                            "You are summarizing a roleplay scene. "
                            "Write 1-2 complete sentences describing what happened, "
                            "any decisions reached, and key dynamics. "
                            "Be specific. Always end with a full stop. "
                            "Output only the summary — no bullet points, no headers,"
                            " no preamble, no incomplete sentences.\n\n"
                            "Dialog:\n" + dialog_text + "\n\nSummary:"
                        ),
                        max_output_tokens=400,
                    )
                )
                summary = resp.text.strip()
            except Exception:
                logger.warning(
                    "Summary generation failed for episode %d of session %s",
                    ep.index,
                    self._runner.session_id,
                )
        ep.summary = summary

        # Persist immediately so GET /history reflects this episode during a live run.
        try:
            await self._layer.save_episode(state.config.session_id, ep)
        except Exception:
            logger.warning(
                "Incremental episode persist failed for session %s episode %d",
                self._runner.session_id,
                ep.index,
            )

        await self._runner._broadcast(
            {"type": "episode_end", "episode": max(ep_index, 0), "summary": summary}
        )
        self._runner.episodes_completed += 1
        return ObserverDirective.continue_()


class SessionRunner:
    """Manages the lifecycle of one simulation session's background task."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.status: RunStatusLiteral = "idle"
        self.episodes_completed: int = 0
        self.episodes_requested: int = 0
        self.error: str | None = None
        self._task: asyncio.Task[None] | None = None
        self._pause_requested: bool = False
        self._pending_injection: str | None = None
        self._subscribers: list[asyncio.Queue[dict[str, Any] | None]] = []

    # ── Public control API ────────────────────────────────────────────────

    def start(
        self,
        state: SimulationState,
        layer: SqlitePersistenceLayer,
        n_episodes: int,
    ) -> None:
        """Spawn the background asyncio task."""
        if self.status == "running":
            raise RuntimeError("Session is already running")
        self.episodes_requested = n_episodes
        self.status = "running"
        self._pause_requested = False
        self._task = asyncio.create_task(
            self._run(state, layer, n_episodes),
            name=f"runner-{self.session_id}",
        )

    def request_pause(self) -> None:
        """Signal the runner to pause after the current episode."""
        self._pause_requested = True

    async def inject(self, text: str) -> None:
        """Queue a narrative event to be applied at the start of the next episode.

        Works whether the session is running (picked up immediately) or paused
        (picked up when the session is next resumed).  Broadcasts an
        ``injection`` WS event so connected clients can show the pending event
        in the timeline before it is consumed.
        """
        self._pending_injection = text
        await self._broadcast({"type": "injection", "text": text})

    def subscribe(self) -> asyncio.Queue[dict[str, Any] | None]:
        """Return a per-subscriber queue receiving broadcast events.

        ``None`` is sent as sentinel when the simulation ends.
        """
        q: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any] | None]) -> None:
        import contextlib

        with contextlib.suppress(ValueError):
            self._subscribers.remove(q)

    # ── Internal ─────────────────────────────────────────────────────────

    async def _broadcast(self, event: dict[str, Any]) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning(
                    "WebSocket subscriber queue full for session %s — dropping event",
                    self.session_id,
                )

    async def _run(
        self,
        state: SimulationState,
        layer: SqlitePersistenceLayer,
        n_episodes: int,
    ) -> None:
        from roleplay.engine.engine import SimulationEngine
        from roleplay.memory.store import InMemoryStore

        memory_store = InMemoryStore()
        try:
            provider = _build_registry().get(state.config.default_provider)
            # If the scenario specified a preferred model, prepend it to the fallback chain.
            if state.config.default_model and hasattr(provider, "models"):
                from roleplay.providers.gemini import _DEFAULT_MODELS, GeminiProvider

                other = tuple(m for m in _DEFAULT_MODELS if m != state.config.default_model)
                provider = GeminiProvider(models=(state.config.default_model, *other))
            hook = ApiObserverHook(self, provider, layer)
            engine = SimulationEngine(
                state=state,
                provider=provider,
                memory_store=memory_store,
                observer=hook,  # type: ignore[arg-type]
            )
            await engine.run(max_episodes=n_episodes)

            # Persist completed episodes to the database
            for episode in state.history.completed_episodes():
                await layer.save_episode(state.config.session_id, episode)

            if self.status == "running":
                self.status = "done"
            await layer.save_state(state)
        except Exception as exc:
            logger.exception("Simulation error for session %s", self.session_id)
            self.status = "error"
            self.error = str(exc)
            await self._broadcast({"type": "error", "message": str(exc)})
        finally:
            complete_event = {
                "type": "simulation_complete",
                "episodes_completed": self.episodes_completed,
            }
            await self._broadcast(complete_event)
            for q in list(self._subscribers):
                import contextlib

                with contextlib.suppress(asyncio.QueueFull):
                    q.put_nowait(None)  # sentinel
            await layer.close()
