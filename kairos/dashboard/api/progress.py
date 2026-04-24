"""Cross-process progress bridge.

Worker processes push JSON-serialisable messages into a shared
``multiprocessing.Manager`` queue. A single background thread in the API
process drains that queue and fans the messages out to any per-run
``asyncio.Queue`` subscribers via ``asyncio.run_coroutine_threadsafe``.

The asyncio event loop isn't known at import time (uvicorn creates it at
startup), so ``main.py`` must call ``set_loop(loop)`` during its lifespan
startup hook before any subscribers are created.
"""
from __future__ import annotations

import asyncio
import multiprocessing
import threading
from typing import Any

# ---------------------------------------------------------------------------
# Module state
# ---------------------------------------------------------------------------

_state_lock = threading.RLock()
_manager: multiprocessing.managers.SyncManager | None = None
_queue: Any | None = None  # proxy to a multiprocessing.Queue; no precise stub
_loop: asyncio.AbstractEventLoop | None = None
_drainer: threading.Thread | None = None
_subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = {}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_queue() -> Any:
    """Return the singleton cross-process queue, creating it on first use."""
    global _manager, _queue
    with _state_lock:
        if _queue is None:
            _manager = multiprocessing.Manager()
            _queue = _manager.Queue()
    return _queue


def set_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Register the asyncio loop used for fan-out to subscribers."""
    global _loop
    with _state_lock:
        _loop = loop


def subscribe(run_id: str) -> asyncio.Queue[dict[str, Any]]:
    """Create a per-connection asyncio queue for messages tagged ``run_id``."""
    q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    with _state_lock:
        _subscribers.setdefault(run_id, set()).add(q)
        _ensure_drainer_locked()
    return q


def unsubscribe(run_id: str, q: asyncio.Queue[dict[str, Any]]) -> None:
    with _state_lock:
        subs = _subscribers.get(run_id)
        if subs is None:
            return
        subs.discard(q)
        if not subs:
            _subscribers.pop(run_id, None)


def publish(run_id: str, message: dict[str, Any]) -> None:
    """Push a message into the shared queue (tagged with ``run_id``).

    Workers usually call this indirectly by writing into the queue returned
    by ``get_queue()``; this helper exists mostly for in-process tests.
    """
    queue = get_queue()
    queue.put({"run_id": run_id, "message": message})


# ---------------------------------------------------------------------------
# Drainer internals
# ---------------------------------------------------------------------------


def _ensure_drainer_locked() -> None:
    """Start the background drain thread if it isn't already running.

    Caller must hold ``_state_lock``.
    """
    global _drainer
    if _drainer is not None and _drainer.is_alive():
        return
    # Ensure queue exists before thread starts reading it.
    if _queue is None:
        get_queue()
    t = threading.Thread(
        target=_drain_loop, name="progress-drainer", daemon=True
    )
    _drainer = t
    t.start()


def _drain_loop() -> None:
    """Blocking drain loop — runs forever until the process exits."""
    assert _queue is not None
    while True:
        try:
            item = _queue.get()
        except (EOFError, OSError):
            # Manager went away (e.g. during interpreter shutdown).
            return
        if item is None:
            # Sentinel used by tests to unblock a shutdown path.
            return
        try:
            run_id = item.get("run_id")
            message = item.get("message")
        except AttributeError:
            continue
        if run_id is None or message is None:
            continue
        _fan_out(run_id, message)


def _fan_out(run_id: str, message: dict[str, Any]) -> None:
    """Copy ``message`` onto every asyncio.Queue subscribed to ``run_id``."""
    with _state_lock:
        loop = _loop
        targets = list(_subscribers.get(run_id, ()))
    if loop is None or not targets:
        return
    for q in targets:
        # run_coroutine_threadsafe hops the message onto the loop's thread.
        try:
            asyncio.run_coroutine_threadsafe(q.put(message), loop)
        except RuntimeError:
            # Loop stopped; skip this target rather than crash the drainer.
            continue


def reset_for_tests() -> None:
    """Clear subscribers + loop binding without tearing down the shared queue.

    The Manager process is expensive to start on Windows (~1-2s per call), so
    we keep it alive across tests. The drainer thread also survives — it just
    finds no subscribers to fan out to until a new test calls ``subscribe``.
    """
    global _loop
    with _state_lock:
        _subscribers.clear()
        _loop = None
        # Drain any leftover items so they don't leak into the next test.
        if _queue is not None:
            while True:
                try:
                    _queue.get_nowait()
                except Exception:
                    break


__all__ = [
    "get_queue",
    "publish",
    "reset_for_tests",
    "set_loop",
    "subscribe",
    "unsubscribe",
]
