"""Unit tests for the worker submit/apply_async wiring + in-process job smoke."""
from __future__ import annotations

import queue as _queue
from pathlib import Path
from typing import Any, Callable

import pandas as pd
import pytest

from kairos.dashboard.api import progress, repo, worker
from kairos.dashboard.api.dtos import CreateRunRequest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point the repo at a fresh tmp data root per test."""
    monkeypatch.setenv("KAIROS_DATA_ROOT", str(tmp_path))
    repo.reset_cache_for_tests()
    progress.reset_for_tests()
    worker.set_job_target_for_tests(None)
    yield tmp_path
    worker.set_job_target_for_tests(None)
    progress.reset_for_tests()
    repo.reset_cache_for_tests()


def _make_request(**overrides: object) -> CreateRunRequest:
    base = {
        "strategy": "orb",
        "symbol": "BTCUSDT",
        "interval": "1m",
        "start": "2024-01-01T00:00:00Z",
        "end": "2024-01-02T00:00:00Z",
        "engine": "vectorbt",
        "params": {},
        "initial_cash": 10_000.0,
        "apply_risk_simulator": False,
    }
    base.update(overrides)
    return CreateRunRequest.model_validate(base)


# ---------------------------------------------------------------------------
# Fake job target — writes artifacts + pushes progress, mirrors the real shape
# ---------------------------------------------------------------------------


def _fake_run_backtest_job(run_id: str, progress_queue: Any) -> None:
    """Stand-in for jobs.run_backtest_job that skips the heavy engine path."""
    progress_queue.put({"run_id": run_id, "message": {
        "type": "progress", "pct": 0.0, "stage": "start", "log": "starting",
    }})
    repo.mark_running(run_id)
    progress_queue.put({"run_id": run_id, "message": {
        "type": "progress", "pct": 50.0, "stage": "engine", "log": "running engine",
    }})

    # Build minimal artifacts in the tmp data root.
    artifacts = repo._artifacts_dir(run_id)  # noqa: SLF001 — test helper
    artifacts.mkdir(parents=True, exist_ok=True)

    idx = pd.date_range("2024-01-01", periods=10, freq="min", tz="UTC")
    eq_df = pd.DataFrame({
        "timestamp": [t.strftime("%Y-%m-%dT%H:%M:%SZ") for t in idx],
        "equity": [10_000.0 + float(i) for i in range(10)],
    })
    eq_df.to_parquet(artifacts / "equity.parquet", index=False)

    trades_df = pd.DataFrame({
        "entry_time": idx[:2],
        "exit_time": idx[:2] + pd.Timedelta(minutes=1),
        "side": [1, -1],
        "qty": [0.1, 0.1],
        "entry_price": [100.0, 101.0],
        "exit_price": [100.5, 100.5],
        "gross_pnl": [0.05, 0.05],
        "fees": [0.01, 0.01],
        "slippage": [0.0, 0.0],
        "funding_cost": [0.0, 0.0],
        "net_pnl": [0.04, 0.04],
        "bars_held": [1, 1],
        "exit_reason": ["signal", "signal"],
    })
    trades_df.to_parquet(artifacts / "trades.parquet", index=False)

    metrics = {"cagr": 0.25, "mdd": -0.05, "sharpe": 1.2, "total_trades": 2}
    repo.mark_done(
        run_id,
        metrics=metrics,
        equity_path=str(artifacts),
        trades_count=2,
    )
    progress_queue.put({"run_id": run_id, "message": {
        "type": "done", "metrics": metrics,
    }})


# ---------------------------------------------------------------------------
# Fake pool — records apply_async calls and runs the target synchronously
# ---------------------------------------------------------------------------


class _FakeAsyncResult:
    def __init__(self, value: Any = None, exc: BaseException | None = None) -> None:
        self._value = value
        self._exc = exc

    def get(self, timeout: float | None = None) -> Any:
        if self._exc is not None:
            raise self._exc
        return self._value

    def wait(self, timeout: float | None = None) -> None:
        return None

    def ready(self) -> bool:
        return True

    def successful(self) -> bool:
        return self._exc is None


class _FakePool:
    def __init__(self) -> None:
        self.calls: list[tuple[Callable[..., Any], tuple[Any, ...]]] = []

    def apply_async(
        self, target: Callable[..., Any], args: tuple[Any, ...]
    ) -> _FakeAsyncResult:
        self.calls.append((target, args))
        try:
            return _FakeAsyncResult(value=target(*args))
        except BaseException as exc:  # noqa: BLE001 — mirror real pool
            return _FakeAsyncResult(exc=exc)

    def close(self) -> None:
        pass

    def join(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_submit_dispatches_to_pool_with_queue(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch
):
    # Capture the target + args without running a real child process.
    fake_pool = _FakePool()
    monkeypatch.setattr(worker, "_pool", fake_pool, raising=False)
    # Use an in-process queue substitute so we don't spin up a Manager.
    fake_q: _queue.Queue[dict[str, Any]] = _queue.Queue()
    monkeypatch.setattr(
        "kairos.dashboard.api.progress.get_queue", lambda: fake_q
    )

    calls: list[tuple[str, Any]] = []

    def recorder(run_id: str, progress_queue: Any) -> None:
        calls.append((run_id, progress_queue))

    worker.set_job_target_for_tests(recorder)

    rid = repo.create_run(_make_request())
    result = worker.submit(rid)
    result.get(timeout=2.0)

    assert len(fake_pool.calls) == 1
    target, args = fake_pool.calls[0]
    assert args == (rid, fake_q)
    assert calls == [(rid, fake_q)]


def test_fake_job_transitions_pending_to_done_and_writes_artifacts(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch
):
    fake_pool = _FakePool()
    monkeypatch.setattr(worker, "_pool", fake_pool, raising=False)
    fake_q: _queue.Queue[dict[str, Any]] = _queue.Queue()
    monkeypatch.setattr(
        "kairos.dashboard.api.progress.get_queue", lambda: fake_q
    )

    worker.set_job_target_for_tests(_fake_run_backtest_job)

    rid = repo.create_run(_make_request())
    # Sanity — row starts pending.
    assert repo.get_run(rid).status == "pending"  # type: ignore[union-attr]

    result = worker.submit(rid)
    result.get(timeout=5.0)

    # Row transitioned pending -> running -> done inside the fake job.
    final = repo.get_run(rid)
    assert final is not None
    assert final.status == "done"
    assert final.metrics is not None
    assert final.metrics["cagr"] == pytest.approx(0.25)
    assert final.total_trades == 2

    # Artifacts exist.
    artifacts = isolated_env / "run_artifacts" / rid
    assert (artifacts / "equity.parquet").exists()
    assert (artifacts / "trades.parquet").exists()

    # Progress messages were recorded in order.
    messages: list[dict[str, Any]] = []
    while not fake_q.empty():
        messages.append(fake_q.get_nowait())
    types = [m["message"]["type"] for m in messages]
    assert types[0] == "progress"
    assert types[-1] == "done"
    assert all(m["run_id"] == rid for m in messages)
