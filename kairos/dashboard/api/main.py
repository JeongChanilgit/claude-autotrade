"""FastAPI entrypoint for the Kairos backtest dashboard.

Wires the REST router, WebSocket router, progress bridge, CORS for local
Vite dev, and (optionally) the built SPA at the mount root.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from kairos import __version__
# Eager import: each strategy module self-registers on load, so importing the
# package here guarantees /api/strategies reflects the full registry at startup
# regardless of which route happens to be hit first.
from kairos import strategies as _strategies  # noqa: F401
from kairos.dashboard.api import progress
from kairos.dashboard.api.errors import register_exception_handlers
from kairos.dashboard.api.reports import reports_router
from kairos.dashboard.api.routes import api_router
from kairos.dashboard.api.ws import ws_router

ROOT = Path(__file__).resolve().parents[3]
WEB_DIST = ROOT / "kairos" / "dashboard" / "web" / "dist"

app = FastAPI(title="Kairos Backtest Dashboard", version=__version__)

# Vite dev server runs on 5173; allow cross-origin only for local dev.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

register_exception_handlers(app)


@app.get("/api/health")
def health() -> JSONResponse:
    return JSONResponse({"status": "ok", "version": __version__})


app.include_router(api_router)
app.include_router(reports_router)
app.include_router(ws_router)


@app.on_event("startup")
async def _bind_progress_loop() -> None:
    # Must run after uvicorn creates the event loop — registers the loop so
    # the progress drainer can fan messages out to WebSocket subscribers.
    progress.set_loop(asyncio.get_running_loop())


if WEB_DIST.exists():
    app.mount("/", StaticFiles(directory=str(WEB_DIST), html=True), name="web")
