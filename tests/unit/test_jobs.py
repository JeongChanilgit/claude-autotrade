"""Unit tests for kairos.dashboard.api.jobs.run_backtest_job (portfolio form).

These tests exercise the job entrypoint in-process with a fake engine and a
mocked repo, avoiding the real DB, real data partitions, and real engines.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from kairos.backtest.protocols import (
    BarsData,
    PortfolioBars,
    PortfolioResult,
    SymbolSpec,
)
from kairos.dashboard.api import jobs as jobs_module


# ---------------------------------------------------------------------------
# Fixtures: fake RunDetail, fake engine, in-memory queue
# ---------------------------------------------------------------------------


@dataclass
class _FakeRunDetail:
    """Stand-in for the repo's RunDetail DTO. Only fields jobs.py reads."""

    id: str
    strategies: dict[str, str]
    strategy_params: dict[str, dict[str, Any]]
    weights: dict[str, float]
    interval: str
    start: str
    end: str
    engine: str
    initial_cash: float = 10_000.0
    apply_risk_simulator: bool = False


@dataclass
class _RepoCalls:
    """Accumulator for repo method calls made during a job run."""

    mark_running: list[str] = field(default_factory=list)
    mark_done: list[dict[str, Any]] = field(default_factory=list)
    mark_failed: list[dict[str, Any]] = field(default_factory=list)


class _FakePortfolioEngine:
    """Deterministic PortfolioEngine that synthesises a simple flat curve."""

    name = "vectorbt"

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def run(
        self,
        strategies: dict[str, Any],
        portfolio: PortfolioBars,
        params: dict[str, Any],
        costs: Any,
        symbol_specs: dict[str, SymbolSpec],
        initial_cash: Decimal,
    ) -> PortfolioResult:
        self.calls.append(
            {
                "strategies": dict(strategies),
                "symbols": sorted(portfolio.bars.keys()),
                "params": dict(params),
                "initial_cash": initial_cash,
            }
        )
        # Build a 3-bar equity curve that sums symbol contributions to the
        # reported portfolio total. Per-symbol equity = weight * initial_cash
        # held flat, so portfolio total equals initial_cash.
        idx = pd.date_range("2023-01-01", periods=3, freq="15min", tz="UTC")
        cash_f = float(initial_cash)
        equity = pd.Series([cash_f] * 3, index=idx, name="equity")
        per_symbol: dict[str, pd.Series] = {}
        for sym, w in portfolio.weights.items():
            per_symbol[sym] = pd.Series([cash_f * w] * 3, index=idx, name=sym)
        trades = pd.DataFrame(
            {
                "symbol": pd.Series([], dtype="string"),
                "entry_time": pd.Series([], dtype="datetime64[ns, UTC]"),
                "exit_time": pd.Series([], dtype="datetime64[ns, UTC]"),
                "side": pd.Series([], dtype="int8"),
                "qty": pd.Series([], dtype="float64"),
                "entry_price": pd.Series([], dtype="float64"),
                "exit_price": pd.Series([], dtype="float64"),
                "gross_pnl": pd.Series([], dtype="float64"),
                "fees": pd.Series([], dtype="float64"),
                "slippage": pd.Series([], dtype="float64"),
                "funding_cost": pd.Series([], dtype="float64"),
                "net_pnl": pd.Series([], dtype="float64"),
                "bars_held": pd.Series([], dtype="int64"),
                "exit_reason": pd.Series([], dtype="string"),
            }
        )
        metrics = {"cagr": 0.0, "mdd": 0.0, "sharpe": 0.0, "total_trades": 0.0}
        per_symbol_metrics = {
            sym: {"cagr": 0.0, "mdd": 0.0, "sharpe": 0.0, "total_trades": 0.0}
            for sym in portfolio.weights.keys()
        }
        return PortfolioResult(
            equity_curve=equity,
            per_symbol_equity=per_symbol,
            trades=trades,
            metrics=metrics,
            per_symbol_metrics=per_symbol_metrics,
            engine_name="vectorbt",
            params={"per_symbol_params": dict(params)},
        )


class _ListQueue:
    """In-memory substitute for a multiprocessing queue. Stores all puts."""

    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def put(self, item: dict[str, Any]) -> None:
        self.items.append(item)


def _fake_bars(symbol: str, n: int = 5) -> BarsData:
    idx = pd.date_range("2023-01-01", periods=n, freq="15min", tz="UTC")
    ohlcv = pd.DataFrame(
        {
            "open": np.full(n, 100.0),
            "high": np.full(n, 101.0),
            "low": np.full(n, 99.0),
            "close": np.full(n, 100.0),
            "volume": np.full(n, 1000.0),
            "quote_volume": np.full(n, 100_000.0),
            "taker_buy_volume": np.full(n, 500.0),
            "taker_buy_quote_volume": np.full(n, 50_000.0),
            "trade_count": np.full(n, 10, dtype=np.int64),
        },
        index=idx,
    )
    return BarsData(ohlcv=ohlcv, symbol=symbol, interval="15m")


# ---------------------------------------------------------------------------
# Common monkeypatch plumbing
# ---------------------------------------------------------------------------


@pytest.fixture
def _patched_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point the repo's data root at tmp_path so artifacts land under it."""
    monkeypatch.setenv("KAIROS_DATA_ROOT", str(tmp_path))
    # The repo caches sqlite connections per-pid. Clear so a future reuse
    # doesn't trip on a file path that no longer exists after tmp_path cleanup.
    from kairos.dashboard.api import repo as repo_module

    repo_module.reset_cache_for_tests()
    return tmp_path


def _install_repo_mocks(
    monkeypatch: pytest.MonkeyPatch, detail: _FakeRunDetail
) -> _RepoCalls:
    calls = _RepoCalls()
    from kairos.dashboard.api import repo as repo_module

    monkeypatch.setattr(
        jobs_module.repo, "get_run", lambda run_id: detail if run_id == detail.id else None
    )
    monkeypatch.setattr(
        jobs_module.repo,
        "mark_running",
        lambda run_id: calls.mark_running.append(run_id),
    )
    monkeypatch.setattr(
        jobs_module.repo,
        "mark_done",
        lambda run_id, **kwargs: calls.mark_done.append({"run_id": run_id, **kwargs}),
    )
    monkeypatch.setattr(
        jobs_module.repo,
        "mark_failed",
        lambda run_id, error: calls.mark_failed.append(
            {"run_id": run_id, "error": error}
        ),
    )
    # Keep _data_root pointing at the env-var override.
    monkeypatch.setattr(jobs_module.repo, "_data_root", repo_module._data_root)
    return calls


def _install_data_and_engine_mocks(
    monkeypatch: pytest.MonkeyPatch,
    weights: dict[str, float],
    engine: _FakePortfolioEngine,
) -> None:
    """Stub load_portfolio + engine factory + registry.build."""

    def _fake_load_portfolio(
        symbols: list[str],
        interval: str,
        start: pd.Timestamp,
        end: pd.Timestamp,
        weights: dict[str, float],
        *,
        data_root: Path | None = None,
    ) -> PortfolioBars:
        bars = {sym: _fake_bars(sym) for sym in symbols}
        return PortfolioBars(bars=bars, interval=interval, weights=dict(weights))

    # load_portfolio is looked up through the imported module inside
    # jobs.run_backtest_job; patch at its source.
    from kairos.data import load as load_module

    monkeypatch.setattr(load_module, "load_portfolio", _fake_load_portfolio)

    monkeypatch.setattr(jobs_module, "_build_engine", lambda name: engine)

    class _DummyStrategy:
        default_params = {}
        name = "dummy"

        def generate_signals(self, data: Any, params: Any) -> Any:  # pragma: no cover
            raise RuntimeError("not called — fake engine bypasses strategies")

    from kairos.strategies import registry as registry_module

    monkeypatch.setattr(registry_module, "build", lambda name: _DummyStrategy())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_run_backtest_job_success_single_symbol(
    monkeypatch: pytest.MonkeyPatch, _patched_env: Path
) -> None:
    detail = _FakeRunDetail(
        id="run_abc",
        strategies={"BTCUSDT": "orb"},
        strategy_params={"BTCUSDT": {"atr_period": 14}},
        weights={"BTCUSDT": 1.0},
        interval="15m",
        start="2023-01-01T00:00:00Z",
        end="2023-02-01T00:00:00Z",
        engine="vectorbt",
        initial_cash=10_000.0,
        apply_risk_simulator=False,
    )
    calls = _install_repo_mocks(monkeypatch, detail)
    engine = _FakePortfolioEngine()
    _install_data_and_engine_mocks(monkeypatch, detail.weights, engine)

    queue = _ListQueue()
    jobs_module.run_backtest_job(detail.id, queue)

    # Repo lifecycle: running then done, never failed.
    assert calls.mark_running == [detail.id]
    assert len(calls.mark_done) == 1
    assert calls.mark_failed == []

    done = calls.mark_done[0]
    assert done["run_id"] == detail.id
    assert "cagr" in done["metrics"]
    assert set(done["per_symbol_metrics"].keys()) == {"BTCUSDT"}
    assert done["trades_count"] == 0

    # Artifacts present on disk.
    artifacts_dir = Path(done["artifacts_dir"])
    assert artifacts_dir.exists()
    assert (artifacts_dir / "equity.parquet").exists()
    assert (artifacts_dir / "equity_per_symbol.parquet").exists()
    assert (artifacts_dir / "trades.parquet").exists()

    # Equity parquet schema.
    eq = pd.read_parquet(artifacts_dir / "equity.parquet")
    assert list(eq.columns) == ["timestamp", "equity"]
    assert len(eq) == 3

    # Per-symbol parquet has timestamp + each symbol column.
    eps = pd.read_parquet(artifacts_dir / "equity_per_symbol.parquet")
    assert "timestamp" in eps.columns
    assert "BTCUSDT" in eps.columns

    # Trades parquet has leading ``symbol`` column.
    tr = pd.read_parquet(artifacts_dir / "trades.parquet")
    assert tr.columns[0] == "symbol"

    # Engine got invoked exactly once with the right shape.
    assert len(engine.calls) == 1
    assert engine.calls[0]["symbols"] == ["BTCUSDT"]
    assert engine.calls[0]["initial_cash"] == Decimal("10000.0")

    # Progress: saw load + run stages and a terminal done.
    kinds = [item["message"].get("stage") or item["message"].get("type")
             for item in queue.items]
    assert "load" in kinds
    assert "run" in kinds
    assert "done" in kinds


def test_run_backtest_job_multi_symbol_routes_through_engine(
    monkeypatch: pytest.MonkeyPatch, _patched_env: Path
) -> None:
    detail = _FakeRunDetail(
        id="run_multi",
        strategies={"BTCUSDT": "orb", "ETHUSDT": "orb"},
        strategy_params={"BTCUSDT": {}, "ETHUSDT": {}},
        weights={"BTCUSDT": 0.6, "ETHUSDT": 0.4},
        interval="15m",
        start="2023-01-01T00:00:00Z",
        end="2023-02-01T00:00:00Z",
        engine="vectorbt",
        initial_cash=20_000.0,
        apply_risk_simulator=False,
    )
    calls = _install_repo_mocks(monkeypatch, detail)
    engine = _FakePortfolioEngine()
    _install_data_and_engine_mocks(monkeypatch, detail.weights, engine)

    queue = _ListQueue()
    jobs_module.run_backtest_job(detail.id, queue)

    assert calls.mark_failed == []
    assert len(calls.mark_done) == 1
    done = calls.mark_done[0]
    assert set(done["per_symbol_metrics"].keys()) == {"BTCUSDT", "ETHUSDT"}

    # Engine received both symbols.
    assert engine.calls[0]["symbols"] == ["BTCUSDT", "ETHUSDT"]

    eps = pd.read_parquet(Path(done["artifacts_dir"]) / "equity_per_symbol.parquet")
    assert "BTCUSDT" in eps.columns
    assert "ETHUSDT" in eps.columns


def test_run_backtest_job_missing_run_marks_failed(
    monkeypatch: pytest.MonkeyPatch, _patched_env: Path
) -> None:
    # get_run returns None -> RuntimeError path -> mark_failed + error message.
    detail = _FakeRunDetail(
        id="run_exists",
        strategies={"BTCUSDT": "orb"},
        strategy_params={"BTCUSDT": {}},
        weights={"BTCUSDT": 1.0},
        interval="15m",
        start="2023-01-01T00:00:00Z",
        end="2023-02-01T00:00:00Z",
        engine="vectorbt",
    )
    calls = _install_repo_mocks(monkeypatch, detail)

    queue = _ListQueue()
    jobs_module.run_backtest_job("run_missing", queue)

    # The job did NOT transition to running — get_run returned None first.
    assert calls.mark_running == []
    assert len(calls.mark_failed) == 1
    assert calls.mark_done == []

    kinds = [item["message"].get("type") for item in queue.items]
    assert "error" in kinds


def test_run_backtest_job_engine_failure_marks_failed(
    monkeypatch: pytest.MonkeyPatch, _patched_env: Path
) -> None:
    detail = _FakeRunDetail(
        id="run_err",
        strategies={"BTCUSDT": "orb"},
        strategy_params={"BTCUSDT": {}},
        weights={"BTCUSDT": 1.0},
        interval="15m",
        start="2023-01-01T00:00:00Z",
        end="2023-02-01T00:00:00Z",
        engine="vectorbt",
    )
    calls = _install_repo_mocks(monkeypatch, detail)

    class _BoomEngine:
        name = "vectorbt"

        def run(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("boom")

    _install_data_and_engine_mocks(monkeypatch, detail.weights, _BoomEngine())

    queue = _ListQueue()
    jobs_module.run_backtest_job(detail.id, queue)

    # Running fired before the crash, then failed; no done.
    assert calls.mark_running == [detail.id]
    assert calls.mark_done == []
    assert len(calls.mark_failed) == 1
    assert "boom" in calls.mark_failed[0]["error"]

    kinds = [item["message"].get("type") for item in queue.items]
    assert "error" in kinds
