"""Smoke tests for the crash-window stress runner (portfolio form)."""
from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd

from kairos.backtest.engine_portfolio_vbt import PortfolioVectorbtEngine
from kairos.backtest.protocols import (
    BarsData,
    CostModel,
    PortfolioBars,
    RiskRules,
    SignalFrame,
    SymbolSpec,
)
from kairos.backtest.regimes import get_regime
from kairos.backtest.stress import all_daily_limits_respected, run_stress_suite


def _bars_covering_crashes(symbol: str = "TESTUSDT", seed: int = 0) -> BarsData:
    """Hourly bars spanning all three crash windows (May 2022 → Aug 2024)."""
    idx = pd.date_range("2022-05-01", "2024-08-15", freq="1h", tz="UTC", inclusive="left")
    rng = np.random.default_rng(seed)
    rets = rng.normal(0.0, 0.0005, len(idx))
    close = 100.0 * np.exp(np.cumsum(rets))
    ohlcv = pd.DataFrame(
        {
            "open": close,
            "high": close * 1.001,
            "low": close * 0.999,
            "close": close,
            "volume": np.full(len(idx), 1000.0),
            "quote_volume": close * 1000.0,
            "taker_buy_volume": np.full(len(idx), 500.0),
            "taker_buy_quote_volume": close * 500.0,
            "trade_count": np.full(len(idx), 100, dtype=np.int64),
        },
        index=idx,
    )
    return BarsData(ohlcv=ohlcv, symbol=symbol, interval="1m")


def _spec(symbol: str = "TESTUSDT") -> SymbolSpec:
    return SymbolSpec(
        symbol=symbol,
        tick_size=Decimal("0.01"),
        step_size=Decimal("0.0001"),
        min_qty=Decimal("0.0001"),
        min_notional=Decimal("1"),
    )


class _FlatStrategy:
    name = "flat"
    default_params: Mapping[str, Any] = {}

    def generate_signals(self, data: BarsData, params: Mapping[str, Any]) -> SignalFrame:
        idx = data.ohlcv.index
        df = pd.DataFrame(
            {
                "side": np.zeros(len(idx), dtype=np.int8),
                "size_hint": np.zeros(len(idx), dtype=float),
                "stop": np.full(len(idx), np.nan, dtype=float),
                "target": np.full(len(idx), np.nan, dtype=float),
            },
            index=idx,
        )
        return SignalFrame(df=df)


def _single_symbol_portfolio(symbol: str = "TESTUSDT") -> PortfolioBars:
    bars = _bars_covering_crashes(symbol=symbol)
    return PortfolioBars(
        bars={symbol: bars},
        interval="1m",
        weights={symbol: 1.0},
    )


def test_stress_suite_covers_all_three_crash_windows() -> None:
    portfolio = _single_symbol_portfolio()
    results = run_stress_suite(
        strategies={"TESTUSDT": _FlatStrategy()},
        portfolio=portfolio,
        engine=PortfolioVectorbtEngine(),
        rules=RiskRules(),
        costs=CostModel(),
        symbol_specs={"TESTUSDT": _spec()},
        initial_cash=Decimal("10000"),
    )
    crash_windows = get_regime("crash")
    assert len(results) == len(crash_windows)
    assert {r.window.label for r in results} == {w.label for w in crash_windows}


def test_flat_strategy_triggers_no_halts_and_passes_daily_limit() -> None:
    portfolio = _single_symbol_portfolio()
    results = run_stress_suite(
        strategies={"TESTUSDT": _FlatStrategy()},
        portfolio=portfolio,
        engine=PortfolioVectorbtEngine(),
        rules=RiskRules(),
        costs=CostModel(),
        symbol_specs={"TESTUSDT": _spec()},
        initial_cash=Decimal("10000"),
    )
    for r in results:
        assert len(r.raw_halts) == 0
        assert r.worst_daily_return == 0.0
        assert r.daily_limit_respected is True


def test_all_daily_limits_respected_gate() -> None:
    portfolio = _single_symbol_portfolio()
    results = run_stress_suite(
        strategies={"TESTUSDT": _FlatStrategy()},
        portfolio=portfolio,
        engine=PortfolioVectorbtEngine(),
        rules=RiskRules(),
        costs=CostModel(),
        symbol_specs={"TESTUSDT": _spec()},
        initial_cash=Decimal("10000"),
    )
    assert all_daily_limits_respected(results) is True


def test_empty_results_fail_gate() -> None:
    assert all_daily_limits_respected(()) is False


def test_two_symbol_portfolio_stress_suite() -> None:
    """Two-symbol portfolio runs through every crash window and respects the
    daily limit when both strategies emit zero signals."""
    bars_a = _bars_covering_crashes(symbol="AAAUSDT", seed=1)
    bars_b = _bars_covering_crashes(symbol="BBBUSDT", seed=2)
    portfolio = PortfolioBars(
        bars={"AAAUSDT": bars_a, "BBBUSDT": bars_b},
        interval="1m",
        weights={"AAAUSDT": 0.6, "BBBUSDT": 0.4},
    )
    strategies = {
        "AAAUSDT": _FlatStrategy(),
        "BBBUSDT": _FlatStrategy(),
    }
    symbol_specs = {
        "AAAUSDT": _spec("AAAUSDT"),
        "BBBUSDT": _spec("BBBUSDT"),
    }
    results = run_stress_suite(
        strategies=strategies,
        portfolio=portfolio,
        engine=PortfolioVectorbtEngine(),
        rules=RiskRules(),
        costs=CostModel(),
        symbol_specs=symbol_specs,
        initial_cash=Decimal("10000"),
    )
    crash_windows = get_regime("crash")
    assert len(results) == len(crash_windows)
    for r in results:
        # Flat strategies on both symbols: portfolio equity constant, daily
        # returns are exactly zero, limit trivially respected.
        assert r.daily_limit_respected is True
        # Per-symbol metrics present for both.
        assert set(r.protected_result.per_symbol_metrics.keys()) == {"AAAUSDT", "BBBUSDT"}
    assert all_daily_limits_respected(results) is True
