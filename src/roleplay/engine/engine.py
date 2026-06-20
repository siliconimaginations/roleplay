"""SimulationEngine — the async episode loop."""

from __future__ import annotations

import logging
import re
import warnings
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from roleplay.core.episode import Episode
from roleplay.core.episode import Turn as CoreTurn
from roleplay.engine.turn import Turn
from roleplay.memory.store import MemoryEntry, MemoryKind
from roleplay.providers.base import ProviderExhaustedError

if TYPE_CHECKING:
    from roleplay.core.party import StateValue
    from roleplay.core.simulation_state import SimulationState
    from roleplay.engine.observer import InjectionPayload, ObserverHook
    from roleplay.memory.store import MemoryStore

logger = logging.getLogger(__name__)


class _HaltSignalError(RuntimeError):
    """Internal signal: observer requested halt. Replaces StopIteration (banned in async)."""


_STATE_LINE_RE = re.compile(r"^STATE:\s*(\S+?)\s*=\s*(.+)$", re.MULTILINE)


def _parse_state_proposals(raw_output: str) -> tuple[str, dict[str, StateValue]]:
    """Split raw LLM output into (output_text, state_proposals).

    Lines matching ``STATE: key=value`` are extracted; remainder is output_text.
    Values are coerced: bool > int > float > str.
    """
    proposals: dict[str, StateValue] = {}
    state_lines: list[str] = []

    for m in _STATE_LINE_RE.finditer(raw_output):
        key = m.group(1)
        raw_val = m.group(2).strip()
        val: StateValue
        if raw_val.lower() in ("true", "yes"):
            val = True
        elif raw_val.lower() in ("false", "no"):
            val = False
        else:
            try:
                val = int(raw_val)
            except ValueError:
                try:
                    val = float(raw_val)
                except ValueError:
                    val = raw_val
        proposals[key] = val
        state_lines.append(m.group(0))

    output_text = raw_output
    for line in state_lines:
        output_text = output_text.replace(line, "").strip()

    return output_text, proposals


def _assemble_prompt(
    party_id: str,
    state: SimulationState,
    memories: list[MemoryEntry],
    current_turns: list[Turn] | None,
    context_override: str | None,
    prompt_char_budget: int = 20_000,
) -> str:
    """Assemble the full prompt for a party's turn (6-layer structure)."""
    if current_turns is None:
        current_turns = []

    party = state.get_party(party_id)
    env = state.environment

    # [1] Persona (never trimmed)
    persona_block = party.to_prompt_context(include_state=True)

    # [6] Instruction suffix (never trimmed)
    suffix = (
        f"\nYou are {party.name}. Respond in character. "
        "If you propose changes to world state, list them as: STATE: key=value. "
        "One response only."
    )

    fixed_len = len(persona_block) + len(suffix)
    remaining = max(0, prompt_char_budget - fixed_len)

    # [2] Environment block — global world context + named location description
    env_lines = [f"Environment: {env.name}"]
    for k, v in env.state_snapshot().items():
        env_lines.append(f"  {k}: {v}")

    # If this party has a location and the registry has a matching environment,
    # append the location description and its state to the environment block.
    if state.environments:
        party_location = str(party.state_snapshot().get("location", ""))
        if party_location:
            named_env = state.environments.get(party_location)
            if named_env is not None:
                env_lines.append(f"\nCurrent location: {named_env.name}")
                env_lines.append(f"  {named_env.description}")
                if named_env.state:
                    env_lines.append("  Location state:")
                    for k, v in named_env.state.items():
                        env_lines.append(f"    {k}: {v}")
            else:
                env_lines.append(f"\nCurrent location: {party_location} (unknown)")

    env_block = "\n".join(env_lines)

    # [3] Memory block
    memory_block = ""
    if memories:
        mem_lines = ["Retrieved memories (most relevant first):"]
        for m in memories:
            mem_lines.append(f"  - {getattr(m, 'content', str(m))}")
        memory_block = "\n".join(mem_lines)

    # [4] Episode history
    history_episodes = state.history.episodes_in_context_window(
        state.config.context_window_episodes
    )
    history_lines: list[str] = []
    for ep in history_episodes:
        history_lines.append(f"Episode {ep.index}:")
        for ht in ep.turns:
            try:
                p = state.get_party(ht.party_id)
                name = p.name
            except KeyError:
                name = ht.party_id
            history_lines.append(f"  {name}: {ht.output}")
    history_block = "\n".join(history_lines)

    # [5] Current episode context
    current_lines: list[str] = []
    if context_override:
        current_lines.append(f"[Event]: {context_override}")
    for t in current_turns:
        try:
            p_name = state.get_party(t.party_id).name
        except KeyError:
            p_name = t.party_id
        current_lines.append(f"{p_name}: {t.output}")
    current_block = "\n".join(current_lines)

    # Budget trimming: history → memory → current (low to high priority)
    total = len(env_block) + len(memory_block) + len(history_block) + len(current_block)

    if total > remaining:
        # Trim history first
        slack = remaining - len(env_block) - len(memory_block) - len(current_block)
        if slack < len(history_block):
            history_block = history_block[-max(0, slack) :] if slack > 0 else ""
        total = len(env_block) + len(memory_block) + len(history_block) + len(current_block)

    if total > remaining:
        # Trim memory next
        slack = remaining - len(env_block) - len(history_block) - len(current_block)
        if slack < len(memory_block):
            memory_block = memory_block[-max(0, slack) :] if slack > 0 else ""

    parts = [persona_block]
    for block in (env_block, memory_block, history_block, current_block):
        if block:
            parts.append(block)
    parts.append(suffix)
    return "\n\n".join(parts)


class SimulationEngine:
    """Async episode loop that drives a simulation forward."""

    def __init__(
        self,
        state: SimulationState,
        provider: object,
        memory_store: MemoryStore,
        observer: ObserverHook | None = None,
    ) -> None:
        self._state = state
        self._provider = provider
        self._memory_store = memory_store
        self._observer = observer

    def _current_simulated_time(self) -> str:
        """Read time.simulated from environment state, or default to ''."""
        return str(self._state.environment.state_snapshot().get("time.simulated", ""))

    async def run_episode(self) -> Episode:
        """Execute one complete episode and return it (closed)."""
        state = self._state
        ep_index = len(state.history.completed_episodes())
        simulated_start = self._current_simulated_time()

        context_override: str | None = None

        # before_episode observer
        if self._observer is not None:
            directive = await self._observer.before_episode(state, ep_index)
            if directive.is_halt:
                raise _HaltSignalError(
                    f"Observer halted before episode {ep_index}: {directive.reason}"
                )
            if directive.is_inject and directive.payload:
                await self._apply_injection(directive.payload)
                if directive.payload.context_override:
                    context_override = directive.payload.context_override

        episode = Episode(index=ep_index, turns=[], simulated_time_start=simulated_start)
        current_turns: list[Turn] = []

        # Turn order from scheduler
        party_ids = list(state.parties.keys())

        # Co-location filter: when environments are defined, restrict each
        # party's prompt audience to parties sharing the same location.
        # Parties with no location set are never filtered out (backward compat).
        def _colocated_ids(speaker_id: str) -> list[str]:
            """Return party ids that can interact with *speaker_id* this turn."""
            if not state.environments:
                return party_ids
            speaker_loc = str(state.get_party(speaker_id).state_snapshot().get("location", ""))
            if not speaker_loc:
                return party_ids
            return [
                pid
                for pid in party_ids
                if not str(state.get_party(pid).state_snapshot().get("location", ""))
                or str(state.get_party(pid).state_snapshot().get("location", "")) == speaker_loc
            ]

        scheduled = state.scheduler.schedule(party_ids, ep_index, state.history)

        for party_id in scheduled:
            # Retrieve memories
            query = " ".join(t.output for t in current_turns) or "begin"
            try:
                memories = await self._memory_store.retrieve(
                    party_id,
                    query,
                    max_entries=state.config.memory_max_entries,
                    episode_index=ep_index,
                )
            except Exception:
                if state.config.memory_retrieve_fail_mode == "raise":
                    raise
                warnings.warn(
                    f"Memory retrieval failed for '{party_id}', proceeding empty.",
                    stacklevel=2,
                )
                memories = []

            # Only show turns from parties co-located with this speaker.
            visible_ids = set(_colocated_ids(party_id))
            visible_turns = [t for t in current_turns if t.party_id in visible_ids]

            prompt = _assemble_prompt(
                party_id,
                state,
                memories,
                visible_turns,
                context_override,
                state.config.prompt_char_budget,
            )

            from roleplay.providers.base import CompletionRequest

            response = await self._provider.complete(  # type: ignore[attr-defined]
                CompletionRequest(prompt=prompt)
            )

            output_text, proposals = _parse_state_proposals(response.text)

            # Apply valid state proposals
            valid: dict[str, StateValue] = {k: v for k, v in proposals.items() if k}
            if valid:
                try:
                    state.get_party(party_id).apply_state_update(valid, ep_index)
                except Exception as exc:
                    logger.warning("State update failed for '%s': %s", party_id, exc)

            turn = Turn(
                party_id=party_id,
                output=output_text,
                state_update_proposals=proposals,
                prompt_tokens=response.prompt_tokens,
                completion_tokens=response.completion_tokens,
                model_used=response.model_used,
            )
            current_turns.append(turn)
            episode.add_turn(
                CoreTurn(
                    party_id=party_id,
                    index=len(episode.turns),
                    output=output_text,
                    state_update_proposals=proposals,
                )
            )

            # after_turn observer
            if self._observer is not None:
                directive = await self._observer.after_turn(state, turn)
                if directive.is_halt:
                    break
                if directive.is_inject and directive.payload:
                    if directive.payload.context_override:
                        context_override = directive.payload.context_override
                    await self._apply_injection(directive.payload)

        # Environment update
        if state.config.environment_reactive:
            env_id = state.environment.id
            env_prompt = _assemble_prompt(
                env_id,
                state,
                [],
                current_turns,
                None,
                state.config.prompt_char_budget,
            )
            from roleplay.providers.base import CompletionRequest

            env_resp = await self._provider.complete(  # type: ignore[attr-defined]
                CompletionRequest(prompt=env_prompt)
            )
            env_text, env_proposals = _parse_state_proposals(env_resp.text)

            if env_proposals:
                try:
                    state.environment.apply_state_update(env_proposals, ep_index)
                except Exception as exc:
                    logger.warning("Env state update failed: %s", exc)

            episode.add_turn(
                CoreTurn(
                    party_id=env_id,
                    index=len(episode.turns),
                    output=env_text,
                    state_update_proposals=env_proposals,
                )
            )

        # Advance simulated time and close episode
        simulated_end = state.clock.advance(simulated_start, ep_index)
        episode.close(simulated_end, timestamp=datetime.now(UTC))
        state.history.episodes.append(episode)

        # Write episodic memories
        await self._write_memories(current_turns, ep_index)

        # after_episode observer
        if self._observer is not None:
            directive = await self._observer.after_episode(state, episode)
            if directive.is_halt:
                raise _HaltSignalError(
                    f"Observer halted after episode {ep_index}: {directive.reason}"
                )

        return episode

    async def run(self, max_episodes: int | None = None) -> None:
        """Run episodes until max_episodes reached, observer halts, or providers exhaust.

        Both :class:`~roleplay.engine.engine._HaltSignalError` (observer stop)
        and :class:`~roleplay.providers.base.ProviderExhaustedError` (all models
        exhausted) break the loop without propagating, so callers can always
        execute post-run logic such as printing a session summary.
        """
        count = 0
        while max_episodes is None or count < max_episodes:
            try:
                await self.run_episode()
            except _HaltSignalError:
                break
            except ProviderExhaustedError as exc:
                logger.warning("All providers exhausted after %d episode(s): %s", count, exc)
                break
            count += 1

    async def _apply_injection(self, payload: InjectionPayload) -> None:
        state = self._state
        ep_index = len(state.history.completed_episodes())
        for party_id, updates in payload.state_updates.items():
            try:
                state.get_party(party_id).apply_state_update(updates, ep_index)
            except KeyError:
                logger.warning("InjectionPayload: unknown party_id '%s'", party_id)
        for party_id, overrides in payload.persona_overrides.items():
            try:
                state.get_party(party_id).replace_persona(**overrides)
            except KeyError:
                logger.warning("InjectionPayload: unknown party_id '%s'", party_id)
        for entry in payload.memory_writes:
            await self._memory_store.write(entry)

    async def _write_memories(self, engine_turns: list[Turn], ep_index: int) -> None:
        state = self._state
        for turn in engine_turns:
            party = state.get_party(turn.party_id)
            importance = 0.6 if turn.state_update_proposals else 0.4
            entry = MemoryEntry(
                party_id=turn.party_id,
                kind=MemoryKind.EPISODIC,
                content=f"{party.name} [episode {ep_index}]: {turn.output[:300]}",
                episode_index=ep_index,
                importance=importance,
            )
            await self._memory_store.write(entry)

        # Passive observation
        active_ids = {t.party_id for t in engine_turns}
        summary = "; ".join(t.output[:100] for t in engine_turns[:3])
        for party_id in state.config.passive_observation_parties:
            if party_id not in active_ids:
                entry = MemoryEntry(
                    party_id=party_id,
                    kind=MemoryKind.EPISODIC,
                    content=f"[Overheard episode {ep_index}]: {summary}",
                    episode_index=ep_index,
                    importance=0.3,
                )
                await self._memory_store.write(entry)
