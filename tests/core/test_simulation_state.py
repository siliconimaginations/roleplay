"""Tests for SimulationConfig and SimulationState."""

from __future__ import annotations

import pytest

from roleplay.core.episode import (
    NoopClock,
    RoundRobinScheduler,
    SimulationHistory,
)
from roleplay.core.party import make_environment, make_organization, make_person
from roleplay.core.simulation_state import SimulationConfig, SimulationState

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_state(
    *,
    extra_parties: dict | None = None,
    env_reactive: bool = True,
) -> SimulationState:
    alice = make_person("alice", "Alice", "Test person")
    env = make_environment("town", "Town", "A small town", [], {})
    parties = {"alice": alice}
    if extra_parties:
        parties.update(extra_parties)
    config = SimulationConfig(session_id="test-session")
    return SimulationState(
        config=config,
        parties=parties,
        environment=env,
        history=SimulationHistory(),
        scheduler=RoundRobinScheduler(),
        clock=NoopClock(),
    )


# ---------------------------------------------------------------------------
# SimulationConfig
# ---------------------------------------------------------------------------


class TestSimulationConfig:
    def test_required_session_id(self) -> None:
        cfg = SimulationConfig(session_id="my-session")
        assert cfg.session_id == "my-session"

    def test_defaults(self) -> None:
        cfg = SimulationConfig(session_id="s")
        assert cfg.context_window_episodes == 10
        assert cfg.memory_max_entries == 20
        assert cfg.memory_char_budget == 4_000
        assert cfg.memory_write_mode == "template"
        assert cfg.compaction_threshold == 200
        assert cfg.compaction_batch_size == 50
        assert cfg.compaction_importance_floor == 0.7
        assert cfg.compaction_char_limit == 80_000
        assert cfg.forgetting_enabled is False
        assert cfg.forgetting_idle_episodes == 100
        assert cfg.memory_retrieve_fail_mode == "raise"
        assert cfg.default_provider == "gemini"
        assert cfg.environment_reactive is True
        assert cfg.auto_checkpoint is True
        assert cfg.passive_observation_parties == []
        assert cfg.prompt_char_budget == 20_000

    def test_retrieval_weights_default(self) -> None:
        cfg = SimulationConfig(session_id="s")
        w = cfg.retrieval_weights
        assert set(w.keys()) == {"alpha", "beta", "gamma", "delta"}
        assert abs(sum(w.values()) - 1.0) < 1e-9

    def test_retrieval_weights_independent(self) -> None:
        """Each instance gets its own dict."""
        cfg1 = SimulationConfig(session_id="s1")
        cfg2 = SimulationConfig(session_id="s2")
        cfg1.retrieval_weights["alpha"] = 0.99
        assert cfg2.retrieval_weights["alpha"] == 0.5

    def test_passive_observation_parties_independent(self) -> None:
        cfg1 = SimulationConfig(session_id="s1")
        cfg2 = SimulationConfig(session_id="s2")
        cfg1.passive_observation_parties.append("bob")
        assert cfg2.passive_observation_parties == []

    def test_custom_values(self) -> None:
        cfg = SimulationConfig(
            session_id="x",
            context_window_episodes=5,
            memory_write_mode="llm",
            forgetting_enabled=True,
            default_provider="claude",
        )
        assert cfg.context_window_episodes == 5
        assert cfg.memory_write_mode == "llm"
        assert cfg.forgetting_enabled is True
        assert cfg.default_provider == "claude"


# ---------------------------------------------------------------------------
# SimulationState — construction
# ---------------------------------------------------------------------------


class TestSimulationStateConstruction:
    def test_basic_construction(self) -> None:
        state = _make_state()
        assert "alice" in state.parties
        assert state.environment.id == "town"

    def test_started_at_set(self) -> None:
        state = _make_state()
        assert state.started_at is not None

    def test_environment_kind_validation(self) -> None:
        alice = make_person("alice", "Alice", "test")
        bob = make_person("bob", "Bob", "test")  # Not an environment
        config = SimulationConfig(session_id="s")
        with pytest.raises(ValueError, match="ENVIRONMENT"):
            SimulationState(
                config=config,
                parties={"alice": alice},
                environment=bob,  # type: ignore[arg-type]
                history=SimulationHistory(),
                scheduler=RoundRobinScheduler(),
                clock=NoopClock(),
            )

    def test_parties_must_not_contain_environment(self) -> None:
        env = make_environment("town", "Town", "desc", [], {})
        env2 = make_environment("city", "City", "desc", [], {})
        config = SimulationConfig(session_id="s")
        with pytest.raises(ValueError, match="must not contain ENVIRONMENT"):
            SimulationState(
                config=config,
                parties={"city": env2},  # ENVIRONMENT in non-env dict
                environment=env,
                history=SimulationHistory(),
                scheduler=RoundRobinScheduler(),
                clock=NoopClock(),
            )

    def test_empty_parties_allowed(self) -> None:
        env = make_environment("town", "Town", "desc", [], {})
        config = SimulationConfig(session_id="s")
        state = SimulationState(
            config=config,
            parties={},
            environment=env,
            history=SimulationHistory(),
            scheduler=RoundRobinScheduler(),
            clock=NoopClock(),
        )
        assert state.party_ids() == []


# ---------------------------------------------------------------------------
# SimulationState — methods
# ---------------------------------------------------------------------------


class TestSimulationStateMethods:
    def test_party_ids(self) -> None:
        bob = make_person("bob", "Bob", "test")
        state = _make_state(extra_parties={"bob": bob})
        ids = state.party_ids()
        assert "alice" in ids
        assert "bob" in ids
        assert "town" not in ids  # environment excluded

    def test_party_ids_order_preserved(self) -> None:
        bob = make_person("bob", "Bob", "test")
        carol = make_organization("carol", "Carol Corp", "desc")
        state = _make_state(extra_parties={"bob": bob, "carol": carol})
        ids = state.party_ids()
        # alice first (added in _make_state), then bob, then carol
        assert ids == ["alice", "bob", "carol"]

    def test_get_party_by_id(self) -> None:
        state = _make_state()
        alice = state.get_party("alice")
        assert alice.id == "alice"
        assert alice.name == "Alice"

    def test_get_party_environment(self) -> None:
        state = _make_state()
        env = state.get_party("town")
        assert env.id == "town"

    def test_get_party_missing(self) -> None:
        state = _make_state()
        with pytest.raises(KeyError):
            state.get_party("nobody")

    def test_all_parties_including_env(self) -> None:
        bob = make_person("bob", "Bob", "test")
        state = _make_state(extra_parties={"bob": bob})
        all_parties = state.all_parties_including_env()
        ids = [p.id for p in all_parties]
        assert "alice" in ids
        assert "bob" in ids
        assert "town" in ids

    def test_environment_is_last(self) -> None:
        state = _make_state()
        all_parties = state.all_parties_including_env()
        assert all_parties[-1].id == "town"

    def test_all_parties_no_duplicate_env(self) -> None:
        state = _make_state()
        all_parties = state.all_parties_including_env()
        env_parties = [p for p in all_parties if p.id == "town"]
        assert len(env_parties) == 1

    def test_party_ids_empty_state(self) -> None:
        env = make_environment("env", "Env", "desc", [], {})
        config = SimulationConfig(session_id="s")
        state = SimulationState(
            config=config,
            parties={},
            environment=env,
            history=SimulationHistory(),
            scheduler=RoundRobinScheduler(),
            clock=NoopClock(),
        )
        assert state.party_ids() == []
        assert len(state.all_parties_including_env()) == 1

    def test_config_reference(self) -> None:
        state = _make_state()
        assert state.config.session_id == "test-session"
