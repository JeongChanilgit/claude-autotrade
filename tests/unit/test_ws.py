"""Unit tests for the Phase 4C WebSocket progress endpoint."""
from __future__ import annotations

import asyncio
import sys
import types
from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kairos.dashboard.api.ws import ws_router


@dataclass
class FakeRun:
    id: str
    status: str = "pending"
    metrics_json: dict[str, Any] | None = None
    error: str | None = None


def _install_stub_modules(
    monkeypatch: pytest.MonkeyPatch,
    *,
    get_run: Any,
    subscribe: Any = None,
    unsubscribe: Any = None,
) -> tuple[types.ModuleType, types.ModuleType]:
    """Install fake `repo` and `progress` modules under kairos.dashboard.api.*.

    Phase 4B owns the real implementations; Phase 4C tests stub them so the
    WS handler has stable collaborators. Using ``monkeypatch.setitem`` on
    ``sys.modules`` ensures the fakes are removed after each test.
    """
    repo_mod = types.ModuleType("kairos.dashboard.api.repo")
    repo_mod.get_run = get_run  # type: ignore[attr-defined]

    progress_mod = types.ModuleType("kairos.dashboard.api.progress")
    progress_mod.subscribe = subscribe or (lambda _rid: asyncio.Queue())  # type: ignore[attr-defined]
    progress_mod.unsubscribe = unsubscribe or (lambda _rid, _q: None)  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "kairos.dashboard.api.repo", repo_mod)
    monkeypatch.setitem(sys.modules, "kairos.dashboard.api.progress", progress_mod)
    return repo_mod, progress_mod


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(ws_router)
    return app


def test_ws_streams_progress_then_done(monkeypatch: pytest.MonkeyPatch) -> None:
    messages = [
        {"type": "progress", "pct": 10.0, "stage": "load_data", "log": "reading"},
        {"type": "progress", "pct": 50.0, "stage": "engine_run", "log": "bar 500/1000"},
        {"type": "done", "metrics": {"cagr": 0.21, "sharpe": 1.4}},
    ]

    queue: asyncio.Queue = asyncio.Queue()
    for m in messages:
        queue.put_nowait(m)

    unsubscribe_calls: list[tuple[str, Any]] = []

    _install_stub_modules(
        monkeypatch,
        get_run=lambda rid: FakeRun(id=rid, status="pending"),
        subscribe=lambda _rid: queue,
        unsubscribe=lambda rid, q: unsubscribe_calls.append((rid, q)),
    )

    client = TestClient(_make_app())
    with client.websocket_connect("/ws/backtest/run_123") as ws:
        received = [ws.receive_json() for _ in range(3)]
        assert received == messages
        # Server should close after `done`; a subsequent receive raises.
        with pytest.raises(Exception):
            ws.receive_json()


def test_ws_closes_when_run_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_stub_modules(
        monkeypatch,
        get_run=lambda _rid: None,
    )

    client = TestClient(_make_app())
    with client.websocket_connect("/ws/backtest/missing") as ws:
        msg = ws.receive_json()
        assert msg == {"type": "error", "message": "run not found"}
        with pytest.raises(Exception):
            ws.receive_json()


def test_ws_terminal_state_replay(monkeypatch: pytest.MonkeyPatch) -> None:
    metrics = {"cagr": 0.33, "mdd": -0.12, "sharpe": 1.9, "total_trades": 42}

    subscribe_calls: list[str] = []

    def _subscribe(rid: str) -> asyncio.Queue:
        subscribe_calls.append(rid)
        return asyncio.Queue()

    _install_stub_modules(
        monkeypatch,
        get_run=lambda rid: FakeRun(id=rid, status="done", metrics_json=metrics),
        subscribe=_subscribe,
    )

    client = TestClient(_make_app())
    with client.websocket_connect("/ws/backtest/run_done") as ws:
        msg = ws.receive_json()
        assert msg == {"type": "done", "metrics": metrics}
        with pytest.raises(Exception):
            ws.receive_json()

    # Terminal replay must NOT subscribe to a live queue.
    assert subscribe_calls == []
