# Scenario TOML Format

This document is the authoritative reference for Roleplay scenario files.
It is intentionally written to be shared with an AI assistant so it can
generate correct TOML — see [Using AI to generate scenarios](#using-ai-to-generate-scenarios).

Validate any scenario file before running it:

```bash
uv run python -m roleplay.validate scenarios/my-scenario.toml
```

---

## Minimal working example

```toml
[[parties]]
id   = "alice"
name = "Alice"

[[parties]]
id   = "bob"
name = "Bob"

[environment]
id   = "town"
name = "Riverside Town"
```

---

## Full annotated example

```toml
# ── Simulation settings ────────────────────────────────────────────────────
[simulation]
session_id              = "negotiation-001"   # string, any unique identifier
provider                = "gemini"            # "gemini" | "claude" | "mock"
episodes                = 5                   # integer >= 1
goal                    = "Alice and Acme Corp sign a formal supplier agreement"
                                              # optional — LLM checks after each episode;
                                              # simulation halts early when met.
                                              # Be specific: "verbal commitment on price" works
                                              # better than "reach a deal".
context_window_episodes = 5                   # how many past episodes the LLM sees
memory_max_entries      = 20                  # max memory entries per party
environment_reactive    = true                # env party gets its own LLM turn per episode
auto_checkpoint         = false               # reserved for persistence (not yet active)

# ── Parties ────────────────────────────────────────────────────────────────
# Use [[parties]] (double brackets) for an array of tables — one block per party.

[[parties]]
id          = "alice"                 # required — unique snake_case identifier
kind        = "person"               # "person" (default) | "organization"
name        = "Alice"                # required — display name
description = "A pragmatic negotiator who values clear outcomes"
goals       = [                      # list of strings
    "Reach a fair agreement",
    "Preserve the long-term relationship",
]
traits      = ["calm", "strategic", "direct"]
knowledge   = ["Experienced in conflict resolution"]
constraints = ["Must stay within the approved budget"]

[[parties]]
id          = "acme"
kind        = "organization"          # use for companies, teams, institutions
name        = "Acme Corp"
description = "A mid-sized technology company seeking a supplier partnership"
goals       = ["Secure a reliable supplier", "Reduce procurement costs by 15%"]
traits      = ["process-driven", "risk-averse"]
knowledge   = ["Has used three previous suppliers, all with quality issues"]
constraints = ["Board approval required for contracts over $500k"]

# ── Environment ────────────────────────────────────────────────────────────
[environment]
id      = "office"                    # required — unique snake_case identifier
name    = "The Negotiation Suite"     # required — display name
setting = "A quiet conference room on the 12th floor, late afternoon"
facts   = [
    "Both parties have met once before, informally",
    "A competing offer exists but has not been disclosed",
]

# Initial state values — see "Environment state keys" below for naming rules.
# IMPORTANT: keys containing dots MUST be quoted (see "TOML gotcha" below).
[environment.initial_state]
"time.simulated"   = "Day 1, 14:00"   # string
"weather.condition" = "overcast"       # string
"event.mood"       = "tense"          # string
```

---

## Field reference

### `[simulation]` — all fields optional

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `session_id` | string | `"session-001"` | Identifier for this run |
| `provider` | string | `"gemini"` | `"gemini"`, `"claude"`, or `"mock"` |
| `episodes` | integer | `3` | Number of episodes to simulate |
| `goal` | string | `""` | Natural-language end condition; checked after every episode by the LLM; simulation halts when met |
| `context_window_episodes` | integer | `5` | Past episodes visible in prompt |
| `memory_max_entries` | integer | `20` | Max memory entries per party |
| `environment_reactive` | boolean | `true` | Environment gets its own LLM turn |
| `auto_checkpoint` | boolean | `false` | Reserved — no effect yet |

### `[[parties]]` — one block per party; at least one required

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `id` | string | **yes** | Unique, snake_case (e.g. `"alice"`, `"acme_corp"`) |
| `name` | string | **yes** | Display name |
| `kind` | string | no | `"person"` (default) or `"organization"` |
| `description` | string | no | One-sentence character description |
| `goals` | list of strings | no | What this party wants to achieve |
| `traits` | list of strings | no | Personality or behavioural adjectives |
| `knowledge` | list of strings | no | What this party knows going in |
| `constraints` | list of strings | no | Hard limits on what they can do or agree to |

### `[environment]` — required

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `id` | string | **yes** | Unique, snake_case |
| `name` | string | **yes** | Display name |
| `setting` | string | no | Prose description of the physical setting |
| `facts` | list of strings | no | World facts all parties can observe |

### `[environment.initial_state]` — optional

Key-value pairs that initialise the world state. Values must be scalar — see "Valid state value types" below.

---

## Valid state value types

State values must be one of: `string`, `integer`, `float`, `boolean`, `null`.

```toml
[environment.initial_state]
"time.simulated"    = "Day 1, Morning"   # ✓ string
"event.intensity"   = 7                  # ✓ integer
"weather.temp_c"    = 18.5               # ✓ float
"event.raining"     = false              # ✓ boolean
"event.special"     = null               # ✓ null (absent / unknown)
```

TOML has no null literal — omit the key entirely if the value is unknown.

Lists and nested tables are **not** allowed:

```toml
[environment.initial_state]
"event.tags" = ["urgent", "public"]   # ✗ list — not allowed
"weather"    = { condition = "clear" } # ✗ table — not allowed
```

---

## Environment state key schema

Keys in `[environment.initial_state]` must follow one of these dot-prefix families:

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

---

## Critical TOML gotcha — dotted keys must be quoted

In TOML, an **unquoted** key containing dots creates a **nested table**, not a
flat string key. This is the most common AI-generation mistake.

```toml
# ✗ WRONG — creates {"weather": {"condition": "clear"}}, a nested dict
[environment.initial_state]
weather.condition = "clear"

# ✓ CORRECT — creates {"weather.condition": "clear"}, a flat string key
[environment.initial_state]
"weather.condition" = "clear"
```

**All keys that contain dots must be surrounded by double quotes.**
The validator catches this error and tells you exactly which key to fix.

---

## Using AI to generate scenarios

### Workflow

1. Copy the prompt template below into your AI assistant (Claude, ChatGPT, etc.).
2. Describe your scenario in plain language.
3. Paste the generated TOML into a file, e.g. `scenarios/my-scenario.toml`.
4. Run the validator:
   ```bash
   uv run python -m roleplay.validate scenarios/my-scenario.toml
   ```
5. If there are errors, paste the validator output back to the AI and ask it to fix them.
6. Repeat until the validator reports **✓ Valid**.

### Prompt template

Copy this block verbatim and append your scenario description at the end:

```
You are generating a Roleplay scenario configuration file in TOML format.

Follow these rules exactly:

1. Use [[parties]] (double brackets) for each party — one block per party.
2. Each party requires "id" (snake_case string) and "name" (string).
3. Valid "kind" values: "person" (default) or "organization".
4. Each party may have: description, goals, traits, knowledge, constraints
   (all strings or lists of strings).
5. [environment] requires "id" and "name".
6. [environment.initial_state] keys follow this schema:
   time.*, weather.*, event.*, loc.<id>.place, loc.<id>.visible_to,
   obj.<id>.place, obj.<id>.visible_to
7. CRITICAL — keys in [environment.initial_state] that contain dots MUST
   be surrounded by double quotes:
     CORRECT:   "weather.condition" = "clear"
     WRONG:     weather.condition = "clear"   ← creates a nested dict, will fail
8. State values must be strings, integers, floats, booleans, or null.
   Lists and nested tables are NOT allowed as state values.
9. Valid provider values: "gemini", "claude", "mock".
10. All fields in [simulation] are optional; omit any you don't need.
11. [simulation] goal is an optional string describing the end condition
    for the simulation. The LLM checks it after every episode and halts
    early when it is met. Be specific: "Alice verbally commits to the price"
    is better than "reach an agreement". Omit if you want the simulation to
    always run for the full episode count.

Output ONLY the TOML — no explanation, no code fences, no markdown.

Scenario: [describe your scenario here]
```

### Tips for better results

- **Be specific about goals and constraints** — vague goals produce vague
  simulations. "Wants a fair deal" is weaker than "Wants to reduce unit cost
  to under $12 while maintaining 30-day payment terms."

- **Set the scene in `setting` and `facts`** — these are injected into every
  episode prompt. Concrete facts (deadlines, stakes, prior history) produce
  richer interactions.

- **Use `environment.initial_state` to track dynamic variables** — things that
  might change episode-to-episode (mood, time of day, a revealed piece of
  information) work well as state keys.

- **Write a specific `goal`** — the goal string is evaluated by the LLM
  after every episode. Vague goals like "reach a deal" are hard for the LLM
  to evaluate unambiguously. Prefer concrete, observable outcomes:
  - ✓ `"Alice verbally commits to the proposed price and delivery timeline"`
  - ✓ `"Both parties sign off on the contract terms without further review needed"`
  - ✗ `"Alice and Bob agree on a partnership deal"` — too abstract; the parties
    may have conceptual alignment while the LLM still answers "not yet met"

- **Start with `provider = "mock"` while iterating** — the mock provider runs
  instantly and costs nothing. Switch to `"gemini"` or `"claude"` once the
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
> **AI:** [generates TOML]
>
> **You:** `uv run python -m roleplay.validate scenarios/labour-dispute.toml`  
> Output: `✗ 1 error — "wage" is not a valid state value type (got list)`
>
> **You:** [paste validator output to AI] Please fix these errors.
>
> **AI:** [corrects the TOML]
>
> **You:** `uv run python -m roleplay.validate scenarios/labour-dispute.toml`  
> Output: `✓ Valid — 2 parties, provider=gemini, 5 episodes`
