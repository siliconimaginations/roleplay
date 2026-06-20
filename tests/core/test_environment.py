"""Tests for core/environment.py — Environment and EnvironmentRegistry."""

from __future__ import annotations

from roleplay.core.environment import Environment, EnvironmentRegistry


class TestEnvironment:
    def test_construction_defaults(self) -> None:
        env = Environment(id="a", name="Alpha", description="First place")
        assert env.id == "a"
        assert env.name == "Alpha"
        assert env.description == "First place"
        assert env.state == {}

    def test_construction_with_state(self) -> None:
        env = Environment(id="b", name="Beta", description="Second", state={"open": True})
        assert env.state == {"open": True}


class TestEnvironmentRegistry:
    def test_empty_registry_is_falsy(self) -> None:
        r = EnvironmentRegistry()
        assert not r
        assert len(r) == 0

    def test_non_empty_registry_is_truthy(self) -> None:
        r = EnvironmentRegistry([Environment("x", "X", "desc")])
        assert r
        assert len(r) == 1

    def test_get_existing(self) -> None:
        env = Environment("square", "Town Square", "Busy public space")
        r = EnvironmentRegistry([env])
        assert r.get("square") is env

    def test_get_missing_returns_none(self) -> None:
        r = EnvironmentRegistry([Environment("a", "A", "desc")])
        assert r.get("missing") is None

    def test_ids(self) -> None:
        envs = [
            Environment("a", "A", "first"),
            Environment("b", "B", "second"),
        ]
        r = EnvironmentRegistry(envs)
        assert set(r.ids()) == {"a", "b"}

    def test_none_argument_produces_empty(self) -> None:
        r = EnvironmentRegistry(None)
        assert not r
