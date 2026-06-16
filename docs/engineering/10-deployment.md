# Deployment Guide

This document covers building and running the Roleplay API in Docker, configuring environment variables, and notes on cloud deployment.

---

## Prerequisites

- Docker 24+ and Docker Compose v2 (`docker compose`)
- An API key for your preferred LLM provider (Gemini or Anthropic)

---

## Quick Start (Docker Compose)

```bash
# Clone the repo
git clone https://github.com/siliconimaginations/roleplay.git
cd roleplay

# (Optional) set an API key so the server requires auth
export ROLEPLAY_API_KEY="your-secret-key"

# (Optional) set LLM provider keys
export GEMINI_API_KEY="AIza..."
# or
export ANTHROPIC_API_KEY="sk-ant-..."

# Start the API
docker compose up
```

The API will be available at `http://localhost:8000`.  
Check health: `curl http://localhost:8000/health`

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `ROLEPLAY_DB_PATH` | `/data/roleplay.db` | Path to the SQLite database file |
| `ROLEPLAY_API_KEY` | *(unset)* | When set, all requests must include `X-API-Key: <value>` |
| `GEMINI_API_KEY` | *(unset)* | Google Gemini API key (required for `gemini` provider) |
| `ANTHROPIC_API_KEY` | *(unset)* | Anthropic API key (required for `claude` provider) |

> **Auth note:** If `ROLEPLAY_API_KEY` is not set, the server accepts all requests without authentication. Always set it in production.

---

## Building the Docker Image

```bash
# Build the multi-stage image
docker build -t roleplay-api .

# Run it directly (SQLite stored in a named volume)
docker run -d \
  --name roleplay \
  -p 8000:8000 \
  -v roleplay_data:/data \
  -e ROLEPLAY_API_KEY="your-secret-key" \
  -e GEMINI_API_KEY="AIza..." \
  roleplay-api
```

### Image details

The `Dockerfile` uses a two-stage build:

1. **Builder** (`python:3.12-slim`) — installs production dependencies via `uv sync --no-dev` into `.venv/`.
2. **Runtime** (`python:3.12-slim`) — copies only `.venv/` and `src/`; runs as a non-root `roleplay` user.

Resulting image is ~200 MB (slim base + Python deps, no dev tools).

---

## Persistent Storage

SQLite data lives at `ROLEPLAY_DB_PATH` (default `/data/roleplay.db`). Mount a named volume or host directory to persist it across container restarts:

```yaml
# docker-compose.yml already does this:
volumes:
  - roleplay_data:/data
```

For production, consider replacing SQLite with a networked database (Postgres via asyncpg) if you need horizontal scaling or concurrent writers. The `PersistenceLayer` protocol makes this straightforward to swap.

---

## Health Check

```bash
curl http://localhost:8000/health
# → {"status": "ok"}
```

The Compose file includes an automatic health check every 30 seconds.

---

## Cloud Deployment Notes

### Railway

1. Push the repo to GitHub.
2. Create a new Railway project → **Deploy from GitHub repo**.
3. Set environment variables in the Railway dashboard (same as above).
4. Railway auto-detects the `Dockerfile` and builds it.
5. Attach a **Railway Volume** (or use Railway's Postgres plugin if you swap the persistence layer).

### Fly.io

```bash
# Install flyctl, then:
fly launch --dockerfile Dockerfile --no-deploy
fly secrets set ROLEPLAY_API_KEY="your-secret-key"
fly secrets set GEMINI_API_KEY="AIza..."
fly volumes create roleplay_data --size 1  # 1 GB
# Add volume mount to fly.toml: [mounts] source = "roleplay_data" destination = "/data"
fly deploy
```

### Generic Docker host (VPS / AWS EC2 / GCP VM)

```bash
# Pull and run on any host with Docker installed
docker pull ghcr.io/siliconimaginations/roleplay:latest  # if you publish to GHCR
# or build on the host:
git clone https://github.com/siliconimaginations/roleplay.git && cd roleplay
docker compose up -d
```

---

## Upgrading

```bash
git pull
docker compose build
docker compose up -d
```

SQLite migrations run automatically at startup via `SqlitePersistenceLayer.open()`.

---

## Scaling Considerations

The current SQLite-backed implementation is designed for **single-instance** deployment. For multi-instance or high-concurrency use:

- Replace `SqlitePersistenceLayer` with a `PersistenceLayer` implementation backed by Postgres (`asyncpg`) or another concurrent-safe store.
- Use a message broker (Redis Pub/Sub, NATS) instead of `asyncio.Queue` for cross-process WebSocket fan-out.
- Run multiple `uvicorn` workers behind a load balancer (nginx, Caddy, AWS ALB).

These changes are outside the current scope but the `PersistenceLayer` protocol and `ObserverHook` pattern make them straightforward to implement without touching core engine code.
