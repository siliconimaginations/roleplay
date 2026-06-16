# syntax=docker/dockerfile:1
# ── Stage 1: build — install deps with uv ────────────────────────────────────
FROM python:3.12-slim AS builder

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# Copy dependency manifests first (layer-cache friendly)
COPY pyproject.toml uv.lock ./

# Install production deps only (no dev extras)
RUN uv sync --frozen --no-dev --no-install-project

# Copy source and install the project itself
COPY src/ ./src/
RUN uv sync --frozen --no-dev

# ── Stage 2: runtime ─────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Non-root user for security
RUN useradd --create-home --shell /bin/bash roleplay
WORKDIR /home/roleplay/app

# Copy the virtual environment from builder
COPY --from=builder /app/.venv /app/.venv

# Copy the installed package source
COPY --from=builder /app/src ./src

# Persistent data directory for SQLite
RUN mkdir -p /data && chown roleplay:roleplay /data

USER roleplay

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    ROLEPLAY_DB_PATH=/data/roleplay.db

EXPOSE 8000

# uvicorn with websocket support (websockets extra in uvicorn[standard])
CMD ["uvicorn", "roleplay.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
