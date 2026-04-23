"""Smoke tests for the crash-window stress runner."""
from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd

from kairos.backtest.engine_vbt import VectorbtEngine
from kairos.backtest.protocols import (
    BarsData,
    CostModel,
    RiskRules,
    SignalFrame,
    SymbolSpec,
)
from kairos.backtest.regimes import get_regime
from kairos.backtest.stress import all_daily_limits_respected, run_stress_suite


def _bars_covering_crashes() -> BarsData:
    """Hourly bars spanning all three crash windows (May 2022 → Aug 2024)."""
    idx = pd.date_range("2022-05-01", "2024-08-15", freq="1h", tz="UTC", inclusive="left")
    rng = np.random.default_rng(0)
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
    return BarsData(ohlcv=ohlcv, symbol="TESTUSDT", interval="1m")


def _spec() -> SymbolSpec:
    return SymbolSpec(
        symbol="TESTUSDT",
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


def test_stress_suite_covers_all_three_crash_windows() -> None:
    data = _bars_covering_crashes()
    results = run_stress_suite(
        strategy=_FlatStrategy(),
        data=data,
        engine=VectorbtEngine(),
        rules=RiskRules(),
        costs=CostModel(),
        symbol_spec=_spec(),
        initial_cash=Decimal("10000"),
    )
    crash_windows = get_regime("crash")
    assert len(results) == len(crash_windows)
    assert {r.window.label for r in results} == {w.label for w in crash_windows}


def test_flat_strategy_triggers_no_halts_and_passes_daily_limit() -> None:
    data = _bars_covering_crashes()
    results = run_stress_suite(
        strategy=_FlatStrategy(),
        data=data,
        engine=VectorbtEngine(),
        rules=RiskRules(),
        costs=CostModel(),
        symbol_spec=_spec(),
        initial_cash=Decimal("10000"),
    )
    for r in results:
        assert len(r.raw_halts) == 0
        assert r.worst_daily_return == 0.0
        assert r.daily_limit_respected is True


def test_all_daily_limits_respected_gate() -> None:
    data = _bars_covering_crashes()
    results = run_stress_suite(
        strategy=_FlatStrategy(),
        data=data,
        engine=VectorbtEngine(),
        rules=RiskRules(),
        costs=CostModel(),
        symbol_spec=_spec(),
        initial_cash=Decimal("10000"),
    )
    assert all_daily_limits_respected(results) is True


def test_empty_results_fail_gate() -> None:
    assert all_daily_limits_respected(()) is False
