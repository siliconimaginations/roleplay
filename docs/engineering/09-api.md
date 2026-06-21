# Engineering Spec — Stage 8: REST API

## Purpose

Expose the Roleplay simulator as a stateless HTTP service so external clients
(frontends, notebooks, other services) can create scenarios, run simulations,
and stream live turn output without depending on the CLI.

## Guiding constraints

- **No new domain logic** — the API is a thin adapter over the existing
  `SimulationEngine`, `scenario_yaml`, and `persistence` layers.
- **Async-first** — all I/O uses `async`/`await`; no blocking calls on the
  event loop.
- **Single-process** — background simulations run as `asyncio.Task` objects
  within the same process. A future Stage 9 task can move them to workers.
- **Zero breaking changes** — the CLI and POC runner remain fully functional.

---

## Technology choices

| Component | Choice | Reason |
|-----------|--------|--------|
| Web framework | FastAPI 0.115+ | Native async, automatic OpenAPI docs, Pydantic v2 |
| ASGI server | Uvicorn | Standard FastAPI server; optional `uvicorn[standard]` for WebSocket support |
| Schema validation | Pydantic v2 (bundled with FastAPI) | Already used by FastAPI; no extra dep |
| WebSocket | FastAPI `WebSocket` (starlette) | Included; no extra dep |
| HTTP test client | `httpx.AsyncClient` + `pytest-asyncio` | Native async test support |

---

## Running the server

```bash
# Install extras
uv sync --group dev

# Start (default: http://localhost:8000)
uv run uvicorn roleplay.api.app:app --reload

# Set API key (required for all non-health endpoints)
ROLEPLAY_API_KEY=secret uv run uvicorn roleplay.api.app:app --reload
```

---

## Authentication

All endpoints except `GET /health` require the `X-API-Key` header.

```
X-API-Key: <value of ROLEPLAY_API_KEY env var>
```

If `ROLEPLAY_API_KEY` is not set, auth is disabled (development mode).
The server logs a warning at startup when auth is disabled.

Responses:
- `401 Unauthorized` — header missing
- `403 Forbidden` — header present but wrong value

---

## Data model

### `SessionSummary` (list view)

```json
{
  "session_id": "example-001",
  "created_at": "2026-06-16T10:00:00Z",
  "episode_count": 3,
  "status": "idle"
}
```

### `SessionDetail` (single-session view)

```json
{
  "session_id": "example-001",
  "created_at": "2026-06-16T10:00:00Z",
  "episode_count": 3,
  "status": "idle",
  "config": { "default_provider": "gemini", "max_episodes": 5, "goal": "..." },
  "parties": [
    { "id": "alice", "kind": "person", "name": "Alice", "state": {} }
  ],
  "environment": { "id": "town", "name": "Riverside Town", "state": { "time.simulated": "Day 1" } }
}
```

### `RunStatus`

```json
{
  "session_id": "example-001",
  "status": "running",
  "episodes_completed": 2,
  "episodes_requested": 3
}
```

### `TurnEvent` (WebSocket message)

```json
{
  "type": "turn",
  "episode": 1,
  "party_id": "alice",
  "output": "Alice opened the negotiation with a handshake.",
  "state_update_proposals": {}
}
```

Other event types: `"episode_start"`, `"episode_end"`, `"simulation_complete"`, `"error"`.

---

## Endpoints

### Health

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/health` | None | Liveness check — always returns `{"status": "ok"}` |

---

### Sessions

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/sessions` | ✓ | Create a session from a YAML scenario (body: `text/plain` or `application/x-yaml`) |
| `GET` | `/sessions` | ✓ | List all sessions (returns `list[SessionSummary]`) |
| `GET` | `/sessions/{session_id}` | ✓ | Get session detail |
| `DELETE` | `/sessions/{session_id}` | ✓ | Delete session and all associated data |
| `POST` | `/sessions/{session_id}/fork` | ✓ | Fork session; returns new `SessionSummary` |
| `GET` | `/sessions/{session_id}/yaml` | ✓ | Return current session state as a YAML scenario document |
| `GET` | `/sessions/{session_id}/export` | ✓ | Export full session state as JSON (parties, history, named environments) |
| `POST` | `/sessions/validate` | ✓ | Validate a YAML scenario without creating a session |
| `POST` | `/sessions/generate` | ✓ | Generate a YAML scenario from a natural-language prompt |

`POST /sessions` body: raw YAML text (same format as `scenarios/example.yaml`).
Response: `SessionSummary` with `201 Created`.

`GET /sessions/{id}/yaml`: Returns `{"yaml": "<yaml text>"}`. The YAML reproduces
the session's current state as a loadable scenario file, including party personas,
environment, config, and named environments.

`GET /sessions/{id}/export`: Returns a JSON object with `session_id`, `parties`
(with `persona`, `initial_state`), `environment`, `environments` (named), and
`episodes` (with turn history and AI summaries).

`POST /sessions/validate` body: raw YAML. Returns
`{"valid": true}` or `{"valid": false, "errors": ["..."]}`. Does not persist
anything.

`POST /sessions/generate` body: plain-text natural-language description. Returns
`{"yaml": "<generated YAML string>"}`. Optional query param `fix_cycles` (int,
0-5, default 0): number of automatic validation-correction cycles — after each
generation the YAML is validated, and if errors are found the LLM is re-prompted
with the error list and asked to correct the output.

---

### Simulation control

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/sessions/{session_id}/run` | ✓ | Start or continue running; optional `?episodes=N` |
| `GET` | `/sessions/{session_id}/status` | ✓ | Current run status |
| `POST` | `/sessions/{session_id}/pause` | ✓ | Request pause after current turn |
| `POST` | `/sessions/{session_id}/inject` | ✓ | Inject a narrative event (body: `{"text": "..."}`) — accepted in `running`, `paused`, `idle`, and `done` states |

`POST /sessions/{id}/run`:
- If session is already running: `409 Conflict`.
- Starts an `asyncio.Task` that drives `SimulationEngine.run()`.
- Returns `202 Accepted` with `RunStatus`.

`POST /sessions/{id}/inject` body:
```json
{ "text": "A fire alarm interrupts the negotiation." }
```

---

### WebSocket

| Path | Auth | Description |
|------|------|-------------|
| `WS /sessions/{session_id}/stream` | ✓ (first message) | Live turn stream |

Connection flow:
1. Client connects.
2. Client sends auth message: `{"api_key": "<key>"}`.
3. Server acknowledges: `{"type": "connected"}`.
4. Server broadcasts `TurnEvent` JSON objects as the simulation runs.
5. On simulation complete or error, server sends `{"type": "simulation_complete"}` or `{"type": "error", "message": "..."}` and closes.

If no simulation is currently running, the WebSocket stays open and events
arrive when `POST /sessions/{id}/run` is called (within the same process).

---

## Internal architecture

```
src/roleplay/api/
├── __init__.py
├── app.py          # FastAPI app, router mounting, lifespan
├── auth.py         # X-API-Key dependency
├── schemas.py      # Pydantic models (request/response)
├── runner.py       # SessionRunner — asyncio.Task wrapper around SimulationEngine
└── routes/
    ├── __init__.py
    ├── health.py
    ├── sessions.py
    └── simulation.py   # run, status, pause, inject, websocket
```

### `SessionRunner`

Manages one `asyncio.Task` per session:

```python
class SessionRunner:
    status: Literal["idle", "running", "paused", "done", "error"]
    task: asyncio.Task | None
    event_queue: asyncio.Queue[dict]   # feeds WebSocket subscribers

    async def run(self, n_episodes: int) -> None: ...
    def pause(self) -> None: ...
    async def inject(self, text: str) -> None: ...
```

An `ApiObserverHook` (implements `ObserverHook`) bridges the engine to the
`SessionRunner`: it puts `TurnEvent` dicts onto `event_queue` and checks a
`_pause_requested` flag after each turn.

### State storage

`SessionRunner` instances are held in a module-level `dict[str, SessionRunner]`
on the `app.state` object (set during lifespan). Sessions persist to SQLite via
`SqlitePersistenceLayer` (existing Stage 6 layer).

---

## Error handling

| Condition | HTTP code |
|-----------|-----------|
| Session not found | `404 Not Found` |
| Session already running | `409 Conflict` |
| Invalid YAML body | `422 Unprocessable Entity` |
| Provider error during run | Captured in runner; `GET /status` shows `"error"` |

---

## OpenAPI docs

Auto-generated at `/docs` (Swagger UI) and `/redoc` (ReDoc). No auth required
to view docs.

---

## Out of scope (Stage 9)

- Worker processes / task queues (Celery, ARQ)
- Multi-tenant isolation
- Rate limiting
- Persistent WebSocket reconnect (client must re-subscribe)
