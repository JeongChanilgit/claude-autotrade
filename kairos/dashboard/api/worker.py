"""Multiprocessing worker pool.

On Windows (the runtime target) we must use the ``spawn`` start method: the
FastAPI parent holds open sockets and a DuckDB connection which fork cannot
safely inherit. ``spawn`` is also the default on Windows regardless of what
we ask for; we pin it explicitly for test reproducibility on any platform.

The pool is created lazily on the first ``submit()`` call so that importing
this module (e.g. from the API routes) is free.
"""
from __future__ import annotations

import multiprocessing
import os
import threading
from multiprocessing.pool import Pool
from typing import Any, Callable

# ---------------------------------------------------------------------------
# Module state
# ---------------------------------------------------------------------------

_pool_lock = threading.Lock()
_pool: Pool | None = None
_ctx: multiprocessing.context.BaseContext | None = None

# Tests may override the job target to avoid spawning real backtests.
_job_target: Callable[..., Any] | None = None


def _get_ctx() -> multiprocessing.context.BaseContext:
    global _ctx
    if _ctx is None:
        _ctx = multiprocessing.get_context("spawn")
    return _ctx


def _get_pool() -> Pool:
    global _pool
    with _pool_lock:
        if _pool is None:
            ctx = _get_ctx()
            size = max(1, (os.cpu_count() or 2) - 1)
            _pool = ctx.Pool(processes=size)
    return _pool


def _get_target() -> Callable[..., Any]:
    if _job_target is not None:
        return _job_target
    # Lazy import — avoids pulling heavy backtest deps at module load time.
    from kairos.dashboard.api.jobs import run_backtest_job

    return run_backtest_job


def set_job_target_for_tests(target: Callable[..., Any] | None) -> None:
    """Monkey-patch hook used by the worker test to inject a fake job."""
    global _job_target
    _job_target = target


def submit(run_id: str) -> Any:
    """Enqueue a backtest job. Returns the pool ``AsyncResult``."""
    from kairos.dashboard.api.progress import get_queue

    pool = _get_pool()
    target = _get_target()
    queue = get_queue()
    return pool.apply_async(target, args=(run_id, queue))


def shutdown() -> None:
    """Clean pool teardown — primarily for test fixtures."""
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.close()
            _pool.join()
            _pool = None


__all__ = ["set_job_target_for_tests", "shutdown", "submit"]
