"""Unit tests for kairos.dashboard.api.repo — v2 portfolio SQLite + parquet."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from kairos.dashboard.api import repo
from kairos.dashboard.api.dtos import (
    CreatePortfolioRunRequest,
    EquityPayloadPerSymbol,
    EquityPayloadTotal,
)


@pytest.fixture()
def isolated_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point the repo at a tmp data root + reset the connection cache."""
    monkeypatch.setenv("KAIROS_DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(repo, "_conn_cache", {})
    yield repo
    # Close anything opened during the test so tmp_path cleanup is safe on Windows.
    for c in list(repo._conn_cache.values()):
        try:
            c.close()
        except Exception:
            pass
    repo._conn_cache.clear()


def _make_request(**overrides: object) -> CreatePortfolioRunRequest:
    base: dict[str, object] = {
        "strategies": {
            "BTCUSDT": {"name": "orb", "params": {"atr_period": 14}},
            "ETHUSDT": {"name": "orb", "params": {"atr_period": 21}},
        },
        "weights": {"BTCUSDT": 0.6, "ETHUSDT": 0.4},
        "interval": "15m",
        "start": "2024-01-01T00:00:00Z",
        "end": "2024-02-01T00:00:00Z",
        "engine": "vectorbt",
        "initial_cash": 10_000.0,
        "apply_risk_simulator": True,
    }
    base.update(overrides)
    return CreatePortfolioRunRequest.model_validate(base)


def _artifacts_path(rid: str) -> Path:
    root = Path(os.environ["KAIROS_DATA_ROOT"])
    p = root / "run_artifacts" / rid
    p.mkdir(parents=True, exist_ok=True)
    return p


# ---------------------------------------------------------------------------
# schema / migration
# ---------------------------------------------------------------------------


def test_init_creates_v2_table(isolated_repo):
    conn = isolated_repo._get_conn()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(backtest_runs)").fetchall()}
    # v2-only columns — their presence is the canonical signal of the new schema.
    assert "strategies_json" in cols
    assert "weights_json" in cols
    assert "symbols_json" in cols
    assert "per_symbol_metrics_json" in cols
    assert "artifacts_dir" in cols


def test_legacy_v1_table_is_renamed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A pre-existing Phase 4 table should be renamed to backtest_runs_v1 on init."""
    monkeypatch.setenv("KAIROS_DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(repo, "_conn_cache", {})

    # Seed a legacy-shaped table (no strategies_json column).
    (tmp_path).mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "dashboard.db"
    seed = sqlite3.connect(str(db_path))
    seed.executescript(
        "CREATE TABLE backtest_runs ("
        "id TEXT PRIMARY KEY, strategy TEXT, symbol TEXT, status TEXT);"
    )
    seed.execute("INSERT INTO backtest_runs VALUES ('r1','orb','BTCUSDT','done')")
    seed.commit()
    seed.close()

    conn = repo._get_conn()
    try:
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "backtest_runs_v1" in tables  # legacy preserved under v1 name
        # New v2 table is the active one.
        cols = {
            r[1]
            for r in conn.execute("PRAGMA table_info(backtest_runs)").fetchall()
        }
        assert "strategies_json" in cols
        # The legacy row lives on under the v1 name.
        legacy = conn.execute(
            "SELECT id FROM backtest_runs_v1 WHERE id = 'r1'"
        ).fetchone()
        assert legacy is not None
    finally:
        for c in list(repo._conn_cache.values()):
            c.close()
        repo._conn_cache.clear()


# ---------------------------------------------------------------------------
# create / get / list
# ---------------------------------------------------------------------------


def test_create_returns_run_id_with_prefix(isolated_repo):
    rid = isolated_repo.create_run(_make_request())
    assert rid.startswith("run_")
    assert len(rid) > len("run_")


def test_get_run_round_trip_preserves_portfolio_shape(isolated_repo):
    rid = isolated_repo.create_run(_make_request())
    detail = isolated_repo.get_run(rid)
    assert detail is not None
    assert detail.id == rid
    assert detail.status == "pending"
    assert detail.symbols == ["BTCUSDT", "ETHUSDT"]  # sorted
    assert detail.strategies == {"BTCUSDT": "orb", "ETHUSDT": "orb"}
    assert detail.weights == {"BTCUSDT": 0.6, "ETHUSDT": 0.4}
    assert detail.strategy_params == {
        "BTCUSDT": {"atr_period": 14},
        "ETHUSDT": {"atr_period": 21},
    }
    assert detail.initial_cash == 10_000.0
    assert detail.apply_risk_simulator is True
    assert detail.metrics is None
    assert detail.per_symbol_metrics is None


def test_get_run_missing_returns_none(isolated_repo):
    assert isolated_repo.get_run("run_does_not_exist") is None


def test_list_runs_filters_by_status_and_limit(isolated_repo):
    ids: list[str] = []
    for _ in range(3):
        ids.append(isolated_repo.create_run(_make_request()))
    isolated_repo.mark_running(ids[0])

    all_runs = isolated_repo.list_runs(limit=10)
    assert len(all_runs) == 3
    assert all(isinstance(r.strategies, dict) for r in all_runs)

    running = isolated_repo.list_runs(limit=10, status="running")
    assert len(running) == 1
    assert running[0].id == ids[0]

    limited = isolated_repo.list_runs(limit=1)
    assert len(limited) == 1


# ---------------------------------------------------------------------------
# status transitions
# ---------------------------------------------------------------------------


def test_mark_running_then_done_writes_both_metric_dicts(isolated_repo):
    rid = isolated_repo.create_run(_make_request())
    isolated_repo.mark_running(rid)
    mid = isolated_repo.get_run(rid)
    assert mid is not None and mid.status == "running"

    metrics = {"cagr": 0.35, "mdd": -0.12, "sharpe": 1.8, "total_trades": 42}
    per_symbol = {
        "BTCUSDT": {"cagr": 0.40, "mdd": -0.10},
        "ETHUSDT": {"cagr": 0.30, "mdd": -0.15},
    }
    isolated_repo.mark_done(
        rid,
        metrics=metrics,
        per_symbol_metrics=per_symbol,
        artifacts_dir="data/run_artifacts/x",
        trades_count=42,
    )
    detail = isolated_repo.get_run(rid)
    assert detail is not None
    assert detail.status == "done"
    assert detail.metrics == metrics
    assert detail.per_symbol_metrics == per_symbol
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
# load_equity — portfolio total + per_symbol + LTTB
# ---------------------------------------------------------------------------


def test_load_equity_portfolio_total_round_trip(isolated_repo):
    rid = isolated_repo.create_run(_make_request())
    n = 10
    idx = pd.date_range("2024-01-01", periods=n, freq="min", tz="UTC")
    ts_strs = [t.strftime("%Y-%m-%dT%H:%M:%SZ") for t in idx]
    df = pd.DataFrame({"timestamp": ts_strs, "equity": [float(i) for i in range(n)]})
    df.to_parquet(_artifacts_path(rid) / "equity.parquet", index=False)

    payload = isolated_repo.load_equity(rid)
    assert isinstance(payload, EquityPayloadTotal)
    assert payload.timestamps == ts_strs
    assert payload.equity == [float(i) for i in range(n)]


def test_load_equity_per_symbol_round_trip(isolated_repo):
    rid = isolated_repo.create_run(_make_request())
    n = 5
    idx = pd.date_range("2024-01-01", periods=n, freq="min", tz="UTC")
    ts_strs = [t.strftime("%Y-%m-%dT%H:%M:%SZ") for t in idx]
    df = pd.DataFrame(
        {
            "timestamp": ts_strs,
            "BTCUSDT": [100.0 + i for i in range(n)],
            "ETHUSDT": [200.0 + i for i in range(n)],
        }
    )
    df.to_parquet(_artifacts_path(rid) / "equity_per_symbol.parquet", index=False)

    payload = isolated_repo.load_equity(rid, per_symbol=True)
    assert isinstance(payload, EquityPayloadPerSymbol)
    assert payload.timestamps == ts_strs
    assert set(payload.per_symbol.keys()) == {"BTCUSDT", "ETHUSDT"}
    assert payload.per_symbol["BTCUSDT"] == [100.0 + i for i in range(n)]
    assert payload.per_symbol["ETHUSDT"] == [200.0 + i for i in range(n)]


def test_load_equity_downsamples_to_at_most_5000(isolated_repo):
    rid = isolated_repo.create_run(_make_request())
    n = 6000
    idx = pd.date_range("2024-01-01", periods=n, freq="min", tz="UTC")
    equity = [10_000.0 + float(i) for i in range(n)]
    ts_strs = [t.strftime("%Y-%m-%dT%H:%M:%SZ") for t in idx]
    df = pd.DataFrame({"timestamp": ts_strs, "equity": equity})
    df.to_parquet(_artifacts_path(rid) / "equity.parquet", index=False)

    payload = isolated_repo.load_equity(rid)
    assert isinstance(payload, EquityPayloadTotal)
    assert len(payload.timestamps) == len(payload.equity)
    assert len(payload.timestamps) <= 5000
    # LTTB must preserve first + last endpoints.
    assert payload.timestamps[0] == ts_strs[0]
    assert payload.timestamps[-1] == ts_strs[-1]
    assert payload.equity[0] == pytest.approx(equity[0])
    assert payload.equity[-1] == pytest.approx(equity[-1])


def test_load_equity_missing_returns_empty_payload(isolated_repo):
    rid = isolated_repo.create_run(_make_request())
    payload = isolated_repo.load_equity(rid)
    assert isinstance(payload, EquityPayloadTotal)
    assert payload.timestamps == []
    assert payload.equity == []

    ps = isolated_repo.load_equity(rid, per_symbol=True)
    assert isinstance(ps, EquityPayloadPerSymbol)
    assert ps.timestamps == []
    assert ps.per_symbol == {}


# ---------------------------------------------------------------------------
# load_trades — pagination + symbol column
# ---------------------------------------------------------------------------


def _make_trades_df(n: int) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame(
        {
            # Alternate symbols so the symbol column is non-trivial.
            "symbol": ["BTCUSDT" if i % 2 == 0 else "ETHUSDT" for i in range(n)],
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
        }
    )


def test_load_trades_pagination_and_total(isolated_repo):
    rid = isolated_repo.create_run(_make_request())
    df = _make_trades_df(25)
    df.to_parquet(_artifacts_path(rid) / "trades.parquet", index=False)

    page, total = isolated_repo.load_trades(rid, offset=0, limit=10)
    assert total == 25
    assert len(page) == 10
    assert page[0]["entry_price"] == pytest.approx(100.0)

    page2, total2 = isolated_repo.load_trades(rid, offset=20, limit=10)
    assert total2 == 25
    assert len(page2) == 5
    assert page2[0]["entry_price"] == pytest.approx(120.0)


def test_load_trades_preserve_symbol_column(isolated_repo):
    rid = isolated_repo.create_run(_make_request())
    df = _make_trades_df(4)
    df.to_parquet(_artifacts_path(rid) / "trades.parquet", index=False)

    page, total = isolated_repo.load_trades(rid, offset=0, limit=10)
    assert total == 4
    assert [p["symbol"] for p in page] == ["BTCUSDT", "ETHUSDT", "BTCUSDT", "ETHUSDT"]


def test_load_trades_missing_returns_empty(isolated_repo):
    rid = isolated_repo.create_run(_make_request())
    trades, total = isolated_repo.load_trades(rid, offset=0, limit=10)
    assert trades == []
    assert total == 0
