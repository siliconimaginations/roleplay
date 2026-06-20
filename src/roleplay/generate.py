"""AI-powered YAML scenario generation.

Calls an LLM provider to produce a valid roleplay scenario YAML from a
natural-language prompt.  The generated text is stripped of any Markdown
code fences before being returned so it can be written directly to a file
or fed back into :func:`~roleplay.scenario_yaml.load_yaml_scenario`.
"""

from __future__ import annotations

import re
import textwrap
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from roleplay.providers.base import Provider

# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------

_SCHEMA_SPEC = textwrap.dedent(
    """\
    ROLEPLAY SCENARIO YAML SCHEMA
    ==============================

    Top-level keys (all optional except parties):

      session_id: string          # unique ID; omit to auto-generate
      description: string         # human-readable summary
      config:
        default_provider: string  # gemini | claude | mock  (default: gemini)
        default_model: string     # optional model override
        max_episodes: int         # default: 5
        context_window_episodes: int  # default: 5
        memory_max_entries: int   # default: 50
        environment_reactive: bool    # default: true
        goal: string              # optional simulation end-goal
      scheduler:                  # default: round_robin
        kind: round_robin | random_order | fixed
        order: [party_id, ...]    # required when kind=fixed
        seed: int                 # optional, for random_order
      clock:                      # default: noop (no time)
        kind: noop | formatted_increment
        unit: seconds | minutes | hours | days | weeks
        amount: int
        format: string            # strftime format, e.g. "%Y-%m-%d %H:%M"
      environments:               # OPTIONAL — named locations parties can occupy
        - id: string              # unique snake_case identifier
          name: string            # display name shown in prompts
          description: string     # narrative description injected into party prompts
          state:                  # optional static key/value metadata
            "key": value
      parties:                    # REQUIRED — list of party objects
        - id: string              # REQUIRED, unique snake_case identifier
          kind: person | organization | environment   # REQUIRED
          name: string            # REQUIRED, display name
          persona:
            description: string
            goals: [string, ...]
            traits: [string, ...]
            knowledge: [string, ...]
            constraints: [string, ...]
          state:                  # optional initial state key/value pairs
            "time.simulated": string   # environment party: current simulated time
            "weather.condition": string
            "location": string    # person/org: current environment id (e.g. town_square)

    RULES:
    - Exactly ONE party must have kind=environment (the global world context).
    - Every party needs id, kind, name.
    - kind values: person, organization, environment (lowercase).
    - When environments are defined, assign each person/org party a "location"
      state key matching one of the environment ids.
    - Parties in different locations cannot directly interact in the same turn;
      they must move (propose STATE: location=<id>) to share a space.
    - Output ONLY the YAML — no markdown fences, no explanation, no preamble.
    """
)

_SYSTEM_PROMPT = (
    "You are a scenario designer for a multi-party roleplay simulator. "
    "Given a user's natural-language description, produce a complete, valid "
    "YAML scenario file following the schema below exactly.\n\n"
    + _SCHEMA_SPEC
    + "\nOutput ONLY the raw YAML — no ```yaml fences, no explanation."
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def generate_yaml_scenario(prompt: str, provider: Provider) -> str:
    """Generate a roleplay scenario YAML from a natural-language *prompt*.

    Args:
        prompt: Natural-language description of the desired scenario.
        provider: An initialised :class:`~roleplay.providers.base.Provider`.

    Returns:
        A string containing valid YAML suitable for use with
        :func:`~roleplay.scenario_yaml.load_yaml_scenario`.

    Raises:
        :class:`~roleplay.providers.base.ProviderError`: If the LLM call fails.
    """
    from roleplay.providers.base import CompletionRequest

    full_prompt = _SYSTEM_PROMPT + "\n\nUser request:\n" + prompt.strip()
    response = await provider.complete(
        CompletionRequest(prompt=full_prompt, max_output_tokens=2_048, temperature=0.7)
    )
    return _strip_fences(response.text.strip())


def _strip_fences(text: str) -> str:
    """Remove Markdown code fences that some LLMs add despite instructions."""
    # Match ```yaml ... ``` or ``` ... ```
    match = re.fullmatch(r"```(?:yaml)?\s*\n(.*?)\n?```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text
