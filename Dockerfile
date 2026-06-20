# syntax=docker/dockerfile:1
# ── Stage 1: frontend build ───────────────────────────────────────────────────
FROM node:22-slim AS frontend-builder

WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci --cache /tmp/npm-cache

COPY frontend/ ./
RUN npm run build

# ── Stage 2: Python build — install deps with uv ─────────────────────────────
FROM python:3.12-slim AS builder

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# Copy dependency manifests first (layer-cache friendly)
COPY pyproject.toml uv.lock ./

# Install production deps only (no dev extras)
RUN uv sync --frozen --no-dev --no-install-project

# Copy source and install the project itself
COPY LICENSE README.md ./
COPY src/ ./src/
RUN uv sync --frozen --no-dev

# ── Stage 3: runtime ─────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Non-root user for security
RUN useradd --create-home --shell /bin/bash roleplay
WORKDIR /home/roleplay/app

# Copy the virtual environment from builder
COPY --from=builder /app/.venv /app/.venv

# Copy the installed package source
COPY --from=builder /app/src /app/src

# Copy the compiled frontend assets
COPY --from=frontend-builder /app/frontend/dist ./frontend/dist

# Persistent data directory for SQLite
RUN mkdir -p /data && chown roleplay:roleplay /data

USER roleplay

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    ROLEPLAY_DB_PATH=/data/roleplay.db

EXPOSE 8000

CMD ["uvicorn", "roleplay.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
