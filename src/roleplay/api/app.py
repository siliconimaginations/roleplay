"""FastAPI application factory for the Roleplay REST API."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from roleplay.api.routes.health import router as health_router
from roleplay.api.routes.sessions import router as sessions_router
from roleplay.api.routes.simulation import router as simulation_router

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

# Directory where the Vite production build is emitted (`npm run build`).
_FRONTEND_DIST = Path(__file__).parent.parent.parent.parent / "frontend" / "dist"


def _db_path() -> str:
    return os.environ.get("ROLEPLAY_DB_PATH", "roleplay.db")


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open the persistence layer and initialise runner registry on startup."""
    from roleplay.persistence.sqlite import SqlitePersistenceLayer

    layer = SqlitePersistenceLayer(_db_path())
    await layer.open()
    app.state.layer = layer
    app.state.runners = {}
    try:
        yield
    finally:
        await layer.close()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Roleplay Simulator API",
        description=("REST API for managing and running multi-party interaction simulations."),
        version="0.1.0",
        lifespan=_lifespan,
    )

    app.include_router(health_router)
    app.include_router(sessions_router)
    app.include_router(simulation_router)

    # Serve the React SPA when the frontend has been built.
    if _FRONTEND_DIST.is_dir():
        app.mount(
            "/assets",
            StaticFiles(directory=_FRONTEND_DIST / "assets"),
            name="assets",
        )

        @app.get("/", include_in_schema=False)
        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_fallback(full_path: str = "") -> FileResponse:
            """Return index.html for all non-API paths (SPA client-side routing)."""
            return FileResponse(_FRONTEND_DIST / "index.html")

    return app


# Module-level app instance for uvicorn / ASGI runners.
app = create_app()
