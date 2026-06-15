# Provider Abstraction

## Purpose

The Provider Abstraction layer isolates all LLM API calls behind a typed
protocol. The simulation engine never calls Gemini or Claude directly — it
calls a `Provider`. This keeps the engine testable with mocked responses,
enables runtime model switching on rate limits, and makes it straightforward
to add new LLM backends without touching engine code.

---

## Scope

**In scope:**
- `Provider` protocol — the interface the engine uses
- `CompletionRequest` and `CompletionResponse` typed structs
- `ToolDefinition` and tool call handling within a turn
- `GeminiProvider` and `ClaudeProvider` adapter implementations
- Model fallback chain and exponential backoff on rate limits
- `ProviderRegistry` — config-driven provider selection
- Cost tracking (token counts threaded back to `Turn`)

**Out of scope:**
- Prompt assembly (see `05-simulation-engine`)
- Which tool implementations are registered (scenario-specific; the provider
  calls the tool function but does not implement it)
- Persistence of provider configuration (see `07-persistence`)
- Rate-limit fallback ordering at the simulation level (the engine simply
  catches `ProviderExhaustedError` — recovery policy lives here)

---

## Key Concepts / Domain Model

### CompletionRequest

```python
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters_schema: dict[str, object]   # JSON Schema object


@dataclass(frozen=True)
class CompletionRequest:
    prompt: str                            # Fully assembled prompt from engine
    tools: tuple[ToolDefinition, ...] = ()
    max_output_tokens: int = 2_048
    temperature: float = 0.9
    stop_sequences: tuple[str, ...] = ()
```

### CompletionResponse

```python
@dataclass(frozen=True)
class ToolCallResult:
    tool_name: str
    arguments: dict[str, object]
    result: str          # Stringified result injected back into the conversation
    error: str | None = None


@dataclass(frozen=True)
class CompletionResponse:
    text: str                              # Final text output after tool calls resolved
    tool_calls: tuple[ToolCallResult, ...] = ()
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model_used: str = ""                   # Actual model that produced the response
```

`text` is never empty if the call succeeded. The provider raises on empty
responses rather than returning them.

### Provider protocol

```python
from typing import Protocol


class Provider(Protocol):
    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        """Call the LLM and return a response.

        Handles retries, model fallback, and tool execution internally.
        Raises ProviderExhaustedError if all models are rate-limited.
        Raises ProviderError for non-retryable failures.
        """
        ...

    @property
    def default_model(self) -> str:
        """The preferred model for this provider (used in logging)."""
        ...
```

### Exceptions

```python
class ProviderError(RuntimeError):
    """Non-retryable provider failure (auth error, malformed request, etc.)."""

class ProviderRateLimitError(ProviderError):
    """Single-model rate limit hit. Raised internally; callers see ProviderExhaustedError."""
    retry_after_seconds: float | None

class ProviderExhaustedError(ProviderError):
    """All models in the fallback chain are rate-limited or failed."""
    attempted_models: list[str]
```

---

## Adapter Implementations

### GeminiProvider

Uses the `google-genai` SDK. Model fallback chain (in order):

```python
GEMINI_MODELS = [
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-1.5-flash",
]
```

On a 429 or quota error from the current model, the provider moves to the next
model in the chain with exponential backoff (see below). If all models are
exhausted, raises `ProviderExhaustedError`.

### ClaudeProvider

Uses the `anthropic` SDK. Model fallback chain:

```python
CLAUDE_MODELS = [
    "claude-haiku-4-5-20251001",
    "claude-sonnet-4-6",
    "claude-opus-4-8",
]
```

Falls back from smaller to larger models on rate limits (larger models typically
have higher quota). The reverse of the Gemini chain (which goes fast-lite → full
because Gemini tiers quota differently).

### MockProvider

Used in unit tests. Returns a fixed `CompletionResponse` or pops from a
pre-loaded response queue. Raises `ProviderExhaustedError` on demand for testing
engine recovery paths.

---

## Rate Limit and Retry Strategy

### Per-model exponential backoff

When a model returns a rate-limit error:

```
wait = min(base_delay × 2^attempt, max_delay) + jitter
```

Defaults:
- `base_delay = 1.0` s
- `max_delay = 60.0` s
- `jitter = random.uniform(0, 0.5 × wait)`
- `max_attempts_per_model = 3`

After `max_attempts_per_model` retries on one model, the provider moves to
the next model in the chain (resetting the retry counter).

### Model fallback sequence

```
Model 0 → [retry up to 3×] → Model 1 → [retry up to 3×] → … → ProviderExhaustedError
```

The fallback chain is exhausted as a whole before raising. The engine does not
implement its own retry loop — it relies entirely on the provider.

### Soft notice on exhaustion

When `ProviderExhaustedError` is raised, the provider logs a structured warning
including all attempted models and their last error codes. The engine surfaces
this to the observer (via `after_episode`) and persists the open episode for
resumption.

---

## Tool Calling

Tools allow parties to ground their responses in real information (web search,
database lookups, environment queries, etc.).

### Registration

Tools are defined as `ToolDefinition` objects and registered in a
`ToolRegistry`. The registry is passed to the provider adapter at construction
time. Each tool has a Python function associated with it:

```python
@dataclass
class RegisteredTool:
    definition: ToolDefinition
    fn: Callable[..., Awaitable[str]]   # async; returns stringified result
```

### Execution within a turn

The provider handles the tool call loop internally (the engine sees only the
final `CompletionResponse.text`):

```
1. Send prompt to LLM with tool definitions
2. If LLM returns a tool call:
   a. Look up tool in registry
   b. Validate arguments against parameters_schema (JSON Schema)
   c. Call fn(**arguments) with a timeout (default: 10 s)
   d. Append result to conversation; record in ToolCallResult
   e. Send updated conversation back to LLM
3. Repeat until LLM returns text with no tool call, or max_tool_rounds reached
```

Defaults: `max_tool_rounds = 5`. After 5 rounds without a final text response,
the provider returns the last text fragment (if any) or raises `ProviderError`.

### Built-in tools (registered by the engine, not user-defined)

| Tool | Description |
|------|-------------|
| `get_party_state` | Returns a party's current state dict as JSON |
| `get_environment_state` | Returns the environment state (visibility-filtered for caller) |
| `get_memory` | Retrieves top-N memories for the current party |

Scenario-specific tools (web search, external APIs) are registered by the
scenario loader at startup (see `08-cli`).

### Tool errors

If `fn()` raises or times out, the provider records `ToolCallResult.error`,
sends the error string back to the LLM as the tool result, and continues the
tool loop. The LLM can decide to retry with different arguments or produce a
final response acknowledging the failure.

---

## ProviderRegistry

The `ProviderRegistry` maps provider names to configured adapter instances.
It is populated from `SimulationConfig` and passed to the engine at startup.

```python
class ProviderRegistry:
    def get(self, name: str) -> Provider: ...
    def default(self) -> Provider: ...
    def register(self, name: str, provider: Provider) -> None: ...
```

The default provider is set in `SimulationConfig.default_provider` (default:
`"gemini"`). The engine always calls `registry.default()`. The only time a
named provider is called explicitly is in tests or when a scenario script
requests a specific provider for a specific party (future extension).

---

## Design Decisions & Rationale

1. **Tool call loop is inside the provider, not the engine.**
   The engine's contract is "send prompt, get response text." If the tool loop
   were in the engine, it would need to know about SDK-specific message formats
   (Gemini and Claude have different conversation structures for tool calls).
   Encapsulating the loop in the provider keeps the engine SDK-agnostic.

2. **Fallback goes fast-lite first for Gemini, small-first for Claude.**
   Gemini's quota structure means the lite models have separate (often higher)
   free-tier quota. Trying lite first minimises cost. Claude's quota is
   typically model-tier based — haiku has higher quota than sonnet, which has
   higher than opus — so small-first is also correct there.

3. **`text` is never empty on success.**
   The engine depends on `CompletionResponse.text` being a non-empty string to
   produce a turn. Making this an invariant of the provider response avoids
   null checks scattered across the engine.

4. **JSON Schema validation of tool arguments before calling `fn()`.**
   LLMs sometimes produce arguments that don't match the schema (wrong types,
   missing required fields). Validating before calling prevents surprising
   exceptions deep in tool implementations. The validation error is sent back
   to the LLM as a tool result so it can self-correct.

5. **`max_tool_rounds = 5` hard cap.**
   Without a cap, a buggy tool or an LLM in a bad state could loop indefinitely.
   Five rounds is generous for any reasonable grounding task (search → refine →
   answer typically takes 1–3 rounds).

6. **`ProviderRegistry` over dependency injection per party.**
   A registry lets the engine retrieve the configured provider by name without
   knowing in advance how many providers are registered or which one is default.
   It also makes testing easy: replace the default in the registry with a mock.

---

## Error Handling

| Situation | Behaviour |
|-----------|-----------|
| 429 / quota error (single model) | Exponential backoff up to `max_attempts_per_model`, then next model |
| All models exhausted | `ProviderExhaustedError` with `attempted_models` list |
| Auth / API key error | `ProviderError` (non-retryable); propagates immediately |
| Empty response from LLM | Provider retries once; if still empty, raises `ProviderError` |
| Tool `fn()` raises | Error string returned to LLM as tool result; turn continues |
| Tool `fn()` times out | Same as raise; `ToolCallResult.error` set to timeout message |
| Tool name not in registry | `ProviderError` (no retry); indicates prompt or LLM hallucination |
| `max_tool_rounds` reached | Last text fragment returned (may be empty); `ProviderError` if no text at all |
| JSON Schema validation failure | Validation error sent to LLM as tool result; LLM may self-correct |

---

## Testing Strategy

**Unit tests (no real API calls):**

- `MockProvider` returns queued responses in order
- `MockProvider` raises `ProviderExhaustedError` on demand
- Exponential backoff: verify wait times and attempt counts with mocked 429s
- Model fallback: model 0 exhausted → model 1 called; all exhausted → `ProviderExhaustedError`
- Tool call loop: one tool call → result injected → final text returned
- Tool call loop: `max_tool_rounds` exceeded → `ProviderError`
- Tool `fn()` raises → error sent to LLM → LLM produces final text
- JSON Schema validation: invalid arguments rejected before `fn()` called
- `CompletionResponse.text` always non-empty on success (raise if empty)
- `ProviderRegistry.default()` returns the configured default

**Integration tests (`@pytest.mark.integration`):**

- `GeminiProvider.complete()` with a simple prompt (real API)
- `ClaudeProvider.complete()` with a simple prompt (real API)
- Tool calling with `get_party_state` built-in tool (real API)
- Rate-limit simulation: mock a 429 on model 0, verify fallback to model 1

**Edge cases:**

- Empty `tools` tuple (no tool calling attempted)
- Tool registered but never called by LLM
- `max_output_tokens` exceeded by LLM (provider truncates gracefully)
- Two simultaneous `complete()` calls (async concurrency — no shared state issues)

**Coverage target:** ≥ 80% for `providers/`; fallback and retry logic ≥ 90%.

---

## Open Questions

None blocking.

Future: per-party provider assignment (party A uses Gemini, party B uses Claude).
The `ProviderRegistry` already supports named lookup; the engine would need to
map `party_id → provider_name` from `SimulationConfig`. Deferred to a future
stage.
