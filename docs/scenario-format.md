# Scenario YAML Format

This document is the authoritative reference for Roleplay scenario files.
It is intentionally written to be shared with an AI assistant so it can
generate correct YAML — see [Using AI to generate scenarios](#using-ai-to-generate-scenarios).

Validate any scenario file before running it:

```bash
uv run python -m roleplay.validate scenarios/my-scenario.yaml
```

> **Note on TOML:** `.toml` scenario files are still accepted by the validator
> but are deprecated. See [TOML (deprecated)](#toml-deprecated) at the bottom of this
> document if you need to migrate an existing file.

---

## Minimal working example

```yaml
parties:
  - id: alice
    name: Alice

  - id: bob
    name: Bob

  - id: town
    kind: environment
    name: Riverside Town
    persona:
      description: "A quiet market town"
      knowledge: []
```

---

## Full annotated example

See `scenarios/example.yaml` for a complete, runnable example. The schema below
documents every supported field.

```yaml
# Optional human-readable description (not used by the engine)
description: "Labour dispute between a union and a factory"

# Unique session ID — omit to auto-generate a UUID
session_id: "negotiation-001"

# ── Simulation settings ────────────────────────────────────────────────────
config:
  default_provider: gemini          # gemini | claude | mock
  max_episodes: 5                   # integer >= 1; omit for unlimited
  goal: "Alice and Acme Corp sign a formal supplier agreement"
                                    # optional — LLM checks after each episode;
                                    # simulation halts early when met.
                                    # Be specific: "verbal commitment on price" works
                                    # better than "reach a deal".
  context_window_episodes: 5        # how many past episodes the LLM sees (default: 10)
  memory_max_entries: 20            # max memory entries per party (default: 20)
  environment_reactive: true        # env party gets its own LLM turn per episode (default: true)
  forgetting_enabled: false         # enable memory forgetting (default: false)
  auto_checkpoint: true             # save checkpoint after each episode (default: true)

# ── Turn scheduler (optional — defaults to round_robin) ───────────────────
# scheduler:
#   kind: round_robin               # round_robin | random_order | fixed

# Fixed order:
# scheduler:
#   kind: fixed
#   order: [alice, bob]

# Random order with reproducible seed:
# scheduler:
#   kind: random_order
#   seed: 42

# ── Simulated clock (optional — defaults to noop) ─────────────────────────
# clock:
#   kind: formatted_increment
#   unit: hours                     # seconds | minutes | hours | days | weeks
#   amount: 2
#   format: "%Y-%m-%d %H:%M"

# ── Parties ────────────────────────────────────────────────────────────────
parties:
  - id: alice                       # snake_case, unique across all parties
    kind: person                    # person (default) | organization | environment
    name: Alice
    persona:
      description: "A pragmatic negotiator who values clear outcomes"
      goals:
        - "Reach a fair agreement"
        - "Preserve the long-term relationship"
      traits:
        - calm
        - strategic
        - direct
      knowledge:
        - "Experienced in conflict resolution"
        - "Familiar with local market rates"
      constraints:
        - "Must stay within the approved budget"
        - "Cannot make binding commitments without sign-off"

  - id: acme
    kind: organization
    name: Acme Corp
    persona:
      description: "A mid-size supplier focused on volume and reliability"
      goals:
        - "Secure a long-term supply contract"
      traits:
        - professional
        - risk-averse
      knowledge:
        - "Has capacity for 50k units/month"
      constraints:
        - "Cannot offer below $11.50/unit without board approval"

  - id: town
    kind: environment               # exactly one environment party required
    name: Riverside Town
    persona:
      description: "A small riverside town in early autumn"
      knowledge:
        - "The annual trade fair opens in two weeks"
        - "Economic sentiment is cautiously optimistic"
    # State values: string | int | float | bool | null
    # Keys must follow the dot-prefix schema (see below)
    state:
      "time.simulated": "Day 1, Morning"
      "weather.condition": "clear"
      "event.mood": "cautious optimism"
```

---

## Environment state key schema

Keys in the environment `state:` block must follow one of these dot-prefix families:

| Family | Example keys | Meaning |
|--------|-------------|---------|
| `time.*` | `time.simulated`, `time.episode` | Simulated clock values |
| `weather.*` | `weather.condition`, `weather.temp_c` | Atmospheric conditions |
| `event.*` | `event.mood`, `event.current` | Active events or flags |
| `loc.<id>.place` | `loc.alice.place` | Where a party or object is located |
| `loc.<id>.visible_to` | `loc.alice.visible_to` | Visibility of a location |
| `obj.<id>.place` | `obj.briefcase.place` | Where an object is |
| `obj.<id>.visible_to` | `obj.briefcase.visible_to` | Visibility of an object |

The validator warns on keys that do not match any of these families.

State values must be strings, integers, floats, booleans, or `null`. Lists and
nested mappings are **not** allowed as state values.

---

## Using AI to generate scenarios

### Workflow

1. Copy the prompt template below into your AI assistant (Claude, ChatGPT, etc.).
2. Describe your scenario in plain language.
3. Paste the generated YAML into a file, e.g. `scenarios/my-scenario.yaml`.
4. Run the validator:
   ```bash
   uv run python -m roleplay.validate scenarios/my-scenario.yaml
   ```
5. If there are errors, paste the validator output back to the AI and ask it to fix them.
6. Repeat until the validator reports **✓ Valid**.
7. Run the simulation:
   ```bash
   uv run roleplay run scenarios/my-scenario.yaml
   ```

### Prompt template

Copy this block verbatim and append your scenario description at the end:

```
You are generating a Roleplay scenario configuration file in YAML format.

Follow these rules exactly:

1. The top-level keys are: description, session_id, config, scheduler, clock, parties.
2. "parties" is a list. Each party requires "id" (snake_case) and "name" (string).
3. Valid "kind" values: "person" (default), "organization", "environment".
4. Exactly one party must have kind: environment.
5. Each party may have a "persona" block with: description, goals, traits,
   knowledge, constraints (all strings or lists of strings).
6. The environment party may have a "state" block. Keys follow this schema:
   time.*, weather.*, event.*, loc.<id>.place, loc.<id>.visible_to,
   obj.<id>.place, obj.<id>.visible_to
7. State values must be strings, integers, floats, booleans, or null.
   Lists and nested mappings are NOT allowed as state values.
8. Valid config.default_provider values: "gemini", "claude", "mock".
9. All fields in "config" are optional; omit any you don't need.
10. config.goal is an optional string describing the end condition for the
    simulation. The LLM checks it after every episode and halts early when
    met. Be specific: "Alice verbally commits to the price" is better than
    "reach an agreement". Omit if you want the simulation to always run for
    the full episode count.

Output ONLY the YAML — no explanation, no code fences, no markdown.

Scenario: [describe your scenario here]
```

### Tips for better results

- **Be specific about goals and constraints** — vague goals produce vague
  simulations. "Wants a fair deal" is weaker than "Wants to reduce unit cost
  to under $12 while maintaining 30-day payment terms."

- **Set the scene in `description` and `knowledge`** — the environment party's
  `persona.knowledge` list is injected into every episode prompt. Concrete facts
  (deadlines, stakes, prior history) produce richer interactions.

- **Use `state` to track dynamic variables** — things that might change
  episode-to-episode (mood, time of day, a revealed piece of information) work
  well as state keys.

- **Write a specific `goal`** — the goal string is evaluated by the LLM
  after every episode. Vague goals like "reach a deal" are hard for the LLM
  to evaluate unambiguously. Prefer concrete, observable outcomes:
  - ✓ `"Alice verbally commits to the proposed price and delivery timeline"`
  - ✓ `"Both parties sign off on the contract terms without further review needed"`
  - ✗ `"Alice and Bob agree on a partnership deal"` — too abstract

- **Start with `default_provider: mock` while iterating** — the mock provider
  runs instantly and costs nothing. Switch to `gemini` or `claude` once the
  structure is right.

- **Paste validator errors back to the AI** — the validator output is written
  to be AI-readable. A single paste-and-ask loop usually resolves all issues.

### Example AI interaction

> **You:** [paste prompt template]  
> Scenario: A labour dispute between a factory workers' union and a
> manufacturing company. The union wants a 12% wage increase and better
> safety equipment. The company is worried about margin pressure and is
> willing to offer 5% plus a safety review committee.
>
> **AI:** [generates YAML]
>
> **You:** `uv run python -m roleplay.validate scenarios/labour-dispute.yaml`  
> Output: `✗ 1 error — state value for "event.tags" must be a scalar, not a list`
>
> **You:** [paste validator output to AI] Please fix these errors.
>
> **AI:** [corrects the YAML]
>
> **You:** `uv run python -m roleplay.validate scenarios/labour-dispute.yaml`  
> Output: `✓ Valid — 2 parties, provider=gemini, 5 episodes`

---

## TOML (deprecated)

TOML scenario files (`.toml`) are **deprecated** as of June 2026 and will be
removed in a future release. The validator still accepts them with a deprecation
warning. New scenarios should use YAML.

To migrate a TOML file to YAML:

1. Note the key structural differences:
   - TOML uses `[[parties]]` array-of-tables; YAML uses `parties:` with list items (`- id: ...`)
   - TOML uses `[simulation]` for settings; YAML uses `config:`
   - TOML uses `[environment]` as a separate section; YAML puts the environment as a party entry with `kind: environment`
   - TOML party fields (`description`, `goals`, etc.) are top-level on the party; YAML nests them under `persona:`
   - TOML uses `[environment.initial_state]`; YAML uses `state:` on the environment party

2. Use the full annotated YAML example above and `scenarios/example.yaml` as a reference.

3. Validate the migrated file:
   ```bash
   uv run python -m roleplay.validate scenarios/my-scenario.yaml
   ```
