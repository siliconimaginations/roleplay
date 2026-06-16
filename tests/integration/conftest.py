"""Integration test fixtures — uses MockProvider, no real LLM calls."""

from __future__ import annotations

import tempfile
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

import pytest


@pytest.fixture
def scenario_yaml_path(tmp_path: Path) -> Path:
    """Write a minimal scenario YAML to a temp file."""
    content = """\
session_id: "integration-test-001"
config:
  default_provider: mock
  context_window_episodes: 3
  goal: "Reach consensus between Alice and Bob."
parties:
  - id: alice
    kind: person
    name: Alice
    system_prompt: "You are Alice, always agreeable."
  - id: bob
    kind: person
    name: Bob
    system_prompt: "You are Bob, always cooperative."
  - id: room
    kind: environment
    name: Meeting Room
    system_prompt: "A small meeting room."
"""
    p = tmp_path / "scenario.yaml"
    p.write_text(content)
    return p


@pytest.fixture
def db_path() -> str:
    d = tempfile.mkdtemp(prefix="roleplay_integ_", dir="/tmp")
    return d + "/test.db"
