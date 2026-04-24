"""WebSocket progress endpoint for Kairos backtest runs.

Phase 4C — owns `ws_router` only. The sibling modules `repo` and `progress`
are owned by Phase 4B; we import them lazily inside the handler so that
tests can monkeypatch (or substitute) them without forcing an import-time
dependency.

Contract: see ``docs/phase4/API.md`` §WebSocket ``/ws/backtest/{id}``.
"""
from __future__ import annotations

import asyncio
import importlib
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

ws_router = APIRouter()

# Application-level close code used when a run id is unknown.
_CLOSE_RUN_NOT_FOUND = 4404


def _load(module_name: str) -> Any:
    """Import a sibling module lazily so tests may stub it out."""
    return importlib.import_module(f"kairos.dashboard.api.{module_name}")


def _terminal_message_from_run(run: Any) -> dict[str, Any]:
    """Build the single terminal frame to replay for a finished run."""
    status = getattr(run, "status", None)
    if status == "done":
        metrics = getattr(run, "metrics_json", None) or {}
        return {"type": "done", "metrics": metrics}
    # failed (or any non-done terminal state)
    message = getattr(run, "error", None) or "run failed"
    return {"type": "error", "message": message}


@ws_router.websocket("/ws/backtest/{run_id}")
async def backtest_progress(websocket: WebSocket, run_id: str) -> None:
    await websocket.accept()

    repo = _load("repo")
    run = repo.get_run(run_id)
    if run is None:
        await websocket.send_json({"type": "error", "message": "run not found"})
        await websocket.close(code=_CLOSE_RUN_NOT_FOUND)
        return

    status = getattr(run, "status", None)
    if status in ("done", "failed"):
        await websocket.send_json(_terminal_message_from_run(run))
        await websocket.close()
        return

    progress = _load("progress")
    queue: asyncio.Queue = progress.subscribe(run_id)

    try:
        while True:
            msg = await queue.get()
            await websocket.send_json(msg)
            msg_type = msg.get("type") if isinstance(msg, dict) else None
            if msg_type in ("done", "error"):
                await websocket.close()
                return
    except WebSocketDisconnect:
        progress.unsubscribe(run_id, queue)
        return
    except Exception as exc:  # pragma: no cover - defensive path
        try:
            await websocket.send_json({"type": "error", "message": str(exc)})
        finally:
            await websocket.close()
        progress.unsubscribe(run_id, queue)
        return
