"""FastAPI entrypoint for the Kairos backtest dashboard.

Phase 0 scaffold only — routes and WebSocket progress streams land in Phase 4.
Serves the built React SPA from kairos/dashboard/web/dist when present.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from kairos import __version__

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


@app.get("/api/health")
def health() -> JSONResponse:
    return JSONResponse({"status": "ok", "version": __version__})


if WEB_DIST.exists():
    app.mount("/", StaticFiles(directory=str(WEB_DIST), html=True), name="web")
