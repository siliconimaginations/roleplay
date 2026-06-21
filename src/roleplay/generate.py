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
      environments:               # OPTIONAL — named LOCATIONS parties can move between
        # Use this list for physical places (rooms, buildings, towns).
        # Do NOT use kind=environment parties for locations — put them here instead.
        - id: string              # unique snake_case identifier (e.g. "town_hall")
          name: string            # display name shown in prompts (e.g. "Town Hall")
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
            "location": string    # person/org: current environment id (e.g. town_hall)

    RULES:
    - TWO SEPARATE CONCEPTS — do not confuse them:
        (A) kind=environment party: the ONE global world-narrator. It takes a turn each
            episode to react to events and update world state. There is exactly one, and
            it represents the SETTING as a whole, not any specific location.
        (B) environments: list: named LOCATIONS parties can physically occupy. Use this
            for rooms, buildings, or places. Parties move by proposing STATE: location=<id>.
    - You MAY omit the kind=environment party if you define an environments: list — the
      system will synthesise a minimal world-narrator automatically. But if you include
      it, include exactly ONE.
    - Every party needs id, kind, name.
    - kind values: person, organization, environment (lowercase).
    - When environments are defined, assign each person/org party a "location"
      state key matching one of the environment ids.
    - Parties in different locations cannot directly interact in the same turn.
    - Output ONLY the YAML — no markdown fences, no explanation, no preamble.
    """
)

_EXAMPLE = textwrap.dedent(
    """\
    EXAMPLE OUTPUT (use this as a structural template — do NOT copy the content):
    ============================================================================

    session_id: village_council_dispute
    description: >
      A small mountain village is divided over whether to sell communal forest
      land to a logging company. The mayor, a conservationist activist, and the
      company's regional manager negotiate over several days.

    config:
      default_provider: gemini
      default_model: gemini-3.5-flash
      max_episodes: 8
      context_window_episodes: 4
      memory_max_entries: 60
      environment_reactive: true
      goal: >
        The village reaches a binding decision on the land sale — either a
        signed agreement, a formal rejection, or a compromise deal.

    clock:
      kind: formatted_increment
      unit: hours
      amount: 4
      format: "%Y-%m-%d %H:%M"

    environments:
      - id: town_hall
        name: Town Hall
        description: >
          The village's meeting chamber — wooden benches, a raised dais for the
          mayor, maps of the forest pinned to the walls.
      - id: forest_edge
        name: Forest Edge
        description: >
          The boundary where the village meets the old-growth forest. Birdsong,
          the smell of pine, survey stakes recently hammered into the ground.
      - id: logging_camp
        name: Logging Company Field Office
        description: >
          A prefab cabin with blueprints, environmental impact reports, and a
          coffee machine that never stops running.

    parties:
      - id: mayor_chen
        kind: person
        name: Mayor Elena Chen
        persona:
          description: >
            Pragmatic mayor in her second term, caught between the village's
            economic need and her personal love of the forest.
          goals:
            - Reach a decision the majority of villagers can live with
            - Preserve her political standing for the next election
            - Avoid public conflict turning violent
          traits:
            - diplomatic
            - risk-averse
            - detail-oriented
          knowledge:
            - The village budget is running a deficit for the third year running
            - The logging company has offered a 15-year royalty deal
          constraints:
            - Cannot sign contracts without a village council vote
        state:
          location: town_hall

      - id: activist_reyes
        kind: person
        name: Marco Reyes
        persona:
          description: >
            A young conservationist who grew up in the village and returned
            after studying environmental law. Passionate but sometimes abrasive.
          goals:
            - Block the land sale entirely
            - Propose a sustainable eco-tourism alternative
            - Win over at least two council members
          traits:
            - passionate
            - confrontational
            - well-researched
          knowledge:
            - The forest contains a protected owl species under national law
            - Three neighbouring villages rejected similar deals and later regretted it
          constraints:
            - Has no formal authority — influence only through persuasion
        state:
          location: forest_edge

      - id: logging_manager
        kind: person
        name: Sandra Voss
        persona:
          description: >
            Regional manager for Norwood Timber, under pressure from head office
            to close the deal this quarter.
          goals:
            - Secure a signed land-use agreement
            - Minimise the royalty percentage offered
            - Counter negative press from the activist's campaign
          traits:
            - persuasive
            - results-driven
            - privately sympathetic to environmental concerns
          knowledge:
            - Head office will cancel the project if no deal is signed within 2 weeks
            - The company's environmental record in other regions is mixed
          constraints:
            - Cannot offer more than 18% royalty without head-office approval
        state:
          location: logging_camp

      - id: village_council
        kind: organization
        name: Village Council
        persona:
          description: >
            The seven-member elected body that holds final decision-making power
            over communal land. Members have mixed views.
          goals:
            - Represent the full range of villager opinions
            - Reach a quorum decision before the quarterly deadline
          traits:
            - cautious
            - divided
          knowledge:
            - Previous land decisions took six months and caused lasting rifts
          constraints:
            - Requires five of seven votes to pass any resolution
        state:
          location: town_hall
    """
)

_MINIMUM_REQUIREMENTS = textwrap.dedent(
    """\
    MINIMUM REQUIREMENTS — every generated scenario MUST include ALL of these:
    =========================================================================
    1. session_id          — a short, descriptive snake_case string
    2. description         — at least two sentences summarising the scenario
    3. config block        — at minimum: max_episodes (5-10) and goal (one clear sentence)
    4. At least 2 person or organization parties — each with a full persona block
       (description, goals list, traits list, knowledge list, constraints list)
    5. At least 1 named environment in the environments: list
    6. Every person/org party must have a state.location matching an environment id

    Scenarios that skip parties, persona sub-fields, environments, or config are WRONG.
    Produce a scenario that is rich enough to sustain 5-10 interesting episodes.
    """
)

_SYSTEM_PROMPT = (
    "You are a scenario designer for a multi-party roleplay simulator. "
    "Given a user's natural-language description, produce a complete, valid "
    "YAML scenario file following the schema below exactly.\n\n"
    + _SCHEMA_SPEC
    + "\n"
    + _MINIMUM_REQUIREMENTS
    + "\n"
    + _EXAMPLE
    + "\nOutput ONLY the raw YAML — no ```yaml fences, no explanation."
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def generate_yaml_scenario(
    prompt: str,
    provider: Provider,
    fix_cycles: int = 0,
) -> str:
    """Generate a roleplay scenario YAML from a natural-language *prompt*.

    Args:
        prompt: Natural-language description of the desired scenario.
        provider: An initialised :class:`~roleplay.providers.base.Provider`.
        fix_cycles: Number of automatic validation-correction cycles to attempt
            after initial generation (0 = no correction, max 5).

    Returns:
        A string containing valid YAML suitable for use with
        :func:`~roleplay.scenario_yaml.load_yaml_scenario`.

    Raises:
        :class:`~roleplay.providers.base.ProviderError`: If the LLM call fails.
    """
    import tempfile
    from pathlib import Path

    from roleplay.providers.base import CompletionRequest
    from roleplay.scenario_yaml import ValidationError, load_yaml_scenario
    from roleplay.validate import validate_scenario

    fix_cycles = max(0, min(fix_cycles, 5))

    full_prompt = _SYSTEM_PROMPT + "\n\nUser request:\n" + prompt.strip()
    response = await provider.complete(
        CompletionRequest(prompt=full_prompt, max_output_tokens=4_096, temperature=0.4)
    )
    yaml_text = _strip_fences(response.text.strip())

    for _ in range(fix_cycles):
        # Validate — if clean, stop early
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".yaml", delete=False, encoding="utf-8"
            ) as tmp:
                tmp.write(yaml_text)
                tmp_path = Path(tmp.name)
            load_yaml_scenario(tmp_path)
            # Also run semantic validation (catches deprecated/unknown models, etc.)
            sem = validate_scenario(tmp_path)
            tmp_path.unlink(missing_ok=True)
            if sem.errors:
                error_text = "; ".join(
                    f"{e.field}: {e.message}" for e in sem.errors
                )
                raise ValidationError(error_text)
            break  # valid — no more cycles needed
        except ValidationError as exc:
            tmp_path.unlink(missing_ok=True)
            correction_prompt = (
                _SYSTEM_PROMPT
                + "\n\nOriginal user request:\n"
                + prompt.strip()
                + "\n\nPrevious attempt (contains errors):\n"
                + yaml_text
                + "\n\n"
                + _CORRECTION_PROMPT.format(errors=str(exc))
            )
            response = await provider.complete(
                CompletionRequest(
                    prompt=correction_prompt,
                    max_output_tokens=4_096,
                    temperature=0.2,
                )
            )
            yaml_text = _strip_fences(response.text.strip())
        except Exception:
            tmp_path.unlink(missing_ok=True)
            break  # unexpected error — return as-is

    return yaml_text


def _strip_fences(text: str) -> str:
    """Remove Markdown code fences that some LLMs add despite instructions."""
    # Match ```yaml ... ``` or ``` ... ```
    match = re.fullmatch(r"```(?:yaml)?\s*\n(.*?)\n?```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text


# ---------------------------------------------------------------------------
# Correction prompt
# ---------------------------------------------------------------------------

_CORRECTION_PROMPT = textwrap.dedent(
    """\
    The YAML you produced failed validation with these errors:

    {errors}

    Fix the YAML so that ALL errors are resolved. Return the complete corrected
    YAML — not just the changed parts. Output ONLY the raw YAML, no fences.
    """
)
