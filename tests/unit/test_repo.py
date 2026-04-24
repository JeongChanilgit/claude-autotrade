"""Unit tests for kairos.dashboard.api.repo — SQLite + parquet persistence."""
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import pytest

from kairos.dashboard.api import repo
from kairos.dashboard.api.dtos import CreateRunRequest


@pytest.fixture()
def isolated_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point the repo at a tmp data root + reset the connection cache."""
    monkeypatch.setenv("KAIROS_DATA_ROOT", str(tmp_path))
    repo.reset_cache_for_tests()
    yield repo
    repo.reset_cache_for_tests()


def _make_request(**overrides: object) -> CreateRunRequest:
    base = {
        "strategy": "orb",
        "symbol": "BTCUSDT",
        "interval": "1m",
        "start": "2024-01-01T00:00:00Z",
        "end": "2024-01-02T00:00:00Z",
        "engine": "vectorbt",
        "params": {"lookback": 30},
        "initial_cash": 10_000.0,
        "apply_risk_simulator": True,
    }
    base.update(overrides)
    return CreateRunRequest.model_validate(base)


# ---------------------------------------------------------------------------
# create / get / list
# ---------------------------------------------------------------------------


def test_create_returns_run_id_with_prefix(isolated_repo):
    rid = isolated_repo.create_run(_make_request())
    assert rid.startswith("run_")
    assert len(rid) > len("run_")


def test_get_run_returns_pending_detail_with_params(isolated_repo):
    rid = isolated_repo.create_run(_make_request(params={"alpha": 1.5}))
    detail = isolated_repo.get_run(rid)
    assert detail is not None
    assert detail.id == rid
    assert detail.status == "pending"
    assert detail.params == {"alpha": 1.5}
    assert detail.initial_cash == 10_000.0
    assert detail.apply_risk_simulator is True
    assert detail.metrics is None


def test_get_run_missing_returns_none(isolated_repo):
    assert isolated_repo.get_run("run_does_not_exist") is None


def test_list_runs_filters_by_status_and_limit(isolated_repo):
    ids: list[str] = []
    for i in range(3):
        ids.append(isolated_repo.create_run(_make_request(symbol=f"SYM{i}USDT")))
    isolated_repo.mark_running(ids[0])

    all_runs = isolated_repo.list_runs(limit=10)
    assert len(all_runs) == 3

    running = isolated_repo.list_runs(limit=10, status="running")
    assert len(running) == 1
    assert running[0].id == ids[0]

    limited = isolated_repo.list_runs(limit=1)
    assert len(limited) == 1


# ---------------------------------------------------------------------------
# status transitions
# ---------------------------------------------------------------------------


def test_mark_running_then_done_transitions(isolated_repo):
    rid = isolated_repo.create_run(_make_request())
    isolated_repo.mark_running(rid)
    assert isolated_repo.get_run(rid).status == "running"  # type: ignore[union-attr]

    metrics = {"cagr": 0.35, "mdd": -0.12, "sharpe": 1.8, "total_trades": 42}
    isolated_repo.mark_done(rid, metrics, equity_path="/artifacts/x", trades_count=42)
    detail = isolated_repo.get_run(rid)
    assert detail is not None
    assert detail.status == "done"
    assert detail.metrics == metrics
    assert detail.cagr == pytest.approx(0.35)
    assert detail.total_trades == 42


def test_mark_failed_records_error(isolated_repo):
    rid = isolated_repo.create_run(_make_request())
    isolated_repo.mark_failed(rid, "boom")
    detail = isolated_repo.get_run(rid)
    assert detail is not None
    assert detail.status == "failed"
    assert detail.error == "boom"


# ---------------------------------------------------------------------------
# load_equity — LTTB downsampling
# ---------------------------------------------------------------------------


def test_load_equity_returns_empty_when_artifact_missing(isolated_repo):
    rid = isolated_repo.create_run(_make_request())
    ts, eq = isolated_repo.load_equity(rid)
    assert ts == []
    assert eq == []


def test_load_equity_downsamples_to_at_most_5000(isolated_repo, tmp_path: Path):
    rid = isolated_repo.create_run(_make_request())
    # Build 6000 synthetic points.
    n = 6000
    idx = pd.date_range("2024-01-01", periods=n, freq="min", tz="UTC")
    equity = [10_000.0 + float(i) for i in range(n)]
    ts_strs = [t.strftime("%Y-%m-%dT%H:%M:%SZ") for t in idx]
    df = pd.DataFrame({"timestamp": ts_strs, "equity": equity})

    root = Path(os.environ["KAIROS_DATA_ROOT"])
    artifacts = root / "run_artifacts" / rid
    artifacts.mkdir(parents=True, exist_ok=True)
    df.to_parquet(artifacts / "equity.parquet", index=False)

    out_ts, out_eq = isolated_repo.load_equity(rid)
    assert len(out_ts) == len(out_eq)
    assert len(out_ts) <= 5000
    # LTTB must preserve first + last point.
    assert out_ts[0] == ts_strs[0]
    assert out_ts[-1] == ts_strs[-1]
    assert out_eq[0] == pytest.approx(equity[0])
    assert out_eq[-1] == pytest.approx(equity[-1])


def test_load_equity_passthrough_small_dataset(isolated_repo):
    rid = isolated_repo.create_run(_make_request())
    n = 10
    idx = pd.date_range("2024-01-01", periods=n, freq="min", tz="UTC")
    ts_strs = [t.strftime("%Y-%m-%dT%H:%M:%SZ") for t in idx]
    df = pd.DataFrame({"timestamp": ts_strs, "equity": [float(i) for i in range(n)]})
    root = Path(os.environ["KAIROS_DATA_ROOT"])
    artifacts = root / "run_artifacts" / rid
    artifacts.mkdir(parents=True, exist_ok=True)
    df.to_parquet(artifacts / "equity.parquet", index=False)

    out_ts, out_eq = isolated_repo.load_equity(rid)
    assert out_ts == ts_strs
    assert out_eq == [float(i) for i in range(n)]


# ---------------------------------------------------------------------------
# load_trades — pagination
# ---------------------------------------------------------------------------


def _make_trades_df(n: int) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame({
        "entry_time": idx,
        "exit_time": idx + pd.Timedelta(minutes=30),
        "side": [1] * n,
        "qty": [0.1] * n,
        "entry_price": [100.0 + i for i in range(n)],
        "exit_price": [101.0 + i for i in range(n)],
        "gross_pnl": [1.0] * n,
        "fees": [0.1] * n,
        "slippage": [0.05] * n,
        "funding_cost": [0.0] * n,
        "net_pnl": [0.85] * n,
        "bars_held": [30] * n,
        "exit_reason": ["signal"] * n,
    })


def test_load_trades_pagination(isolated_repo):
    rid = isolated_repo.create_run(_make_request())
    df = _make_trades_df(25)
    root = Path(os.environ["KAIROS_DATA_ROOT"])
    artifacts = root / "run_artifacts" / rid
    artifacts.mkdir(parents=True, exist_ok=True)
    df.to_parquet(artifacts / "trades.parquet", index=False)

    page, total = isolated_repo.load_trades(rid, offset=0, limit=10)
    assert total == 25
    assert len(page) == 10
    assert page[0].entry_price == pytest.approx(100.0)

    page2, total2 = isolated_repo.load_trades(rid, offset=20, limit=10)
    assert total2 == 25
    assert len(page2) == 5
    assert page2[0].entry_price == pytest.approx(120.0)


def test_load_trades_missing_returns_empty(isolated_repo):
    rid = isolated_repo.create_run(_make_request())
    trades, total = isolated_repo.load_trades(rid, offset=0, limit=10)
    assert trades == []
    assert total == 0
