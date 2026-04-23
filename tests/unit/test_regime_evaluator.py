"""Smoke tests for the per-regime evaluation harness."""
from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd
import pytest

from kairos.backtest.engine_vbt import VectorbtEngine
from kairos.backtest.protocols import (
    BarsData,
    CostModel,
    SignalFrame,
    SymbolSpec,
)
from kairos.backtest.regime_evaluator import (
    all_families_break_even,
    evaluate_regimes,
    group_by_family,
)
from kairos.backtest.regimes import REGIMES, RegimeWindow, slice_bars


def _synthetic_bars(start: str, end: str, trend: float = 0.0, seed: int = 0) -> BarsData:
    """Daily-cadence synthetic bars spanning [start, end)."""
    idx = pd.date_range(start, end, freq="1h", tz="UTC", inclusive="left")
    n = len(idx)
    rng = np.random.default_rng(seed)
    noise = rng.normal(0, 0.001, n)
    drift = np.full(n, trend)
    rets = drift + noise
    close = 100.0 * np.exp(np.cumsum(rets))
    ohlcv = pd.DataFrame(
        {
            "open": close,
            "high": close * 1.001,
            "low": close * 0.999,
            "close": close,
            "volume": np.full(n, 1000.0),
            "quote_volume": close * 1000.0,
            "taker_buy_volume": np.full(n, 500.0),
            "taker_buy_quote_volume": close * 500.0,
            "trade_count": np.full(n, 100, dtype=np.int64),
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
    """Emits side=0 everywhere: zero trades, equity constant."""
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


def test_slice_bars_respects_half_open_interval() -> None:
    data = _synthetic_bars("2024-01-01", "2024-02-01")
    w = RegimeWindow(
        name="uptrend",
        label="x",
        start=pd.Timestamp("2024-01-10", tz="UTC"),
        end=pd.Timestamp("2024-01-20", tz="UTC"),
    )
    sliced = slice_bars(data, w)
    assert sliced.ohlcv.index[0] >= w.start
    assert sliced.ohlcv.index[-1] < w.end


def test_slice_bars_passes_through_auxiliary_frames() -> None:
    data = _synthetic_bars("2024-01-01", "2024-02-01")
    # Attach a funding frame covering the same window.
    funding_idx = pd.date_range("2024-01-01", "2024-02-01", freq="8h", tz="UTC", inclusive="left")
    funding = pd.DataFrame(
        {"funding_rate": 0.0001, "funding_interval_hours": 8.0},
        index=funding_idx,
    )
    bars = BarsData(
        ohlcv=data.ohlcv,
        symbol=data.symbol,
        interval=data.interval,
        funding=funding,
    )
    w = RegimeWindow(
        name="range",
        label="y",
        start=pd.Timestamp("2024-01-05", tz="UTC"),
        end=pd.Timestamp("2024-01-15", tz="UTC"),
    )
    sliced = slice_bars(bars, w)
    assert sliced.funding is not None
    assert (sliced.funding.index >= w.start).all()
    assert (sliced.funding.index < w.end).all()


def test_evaluate_regimes_flat_strategy_is_break_even_everywhere() -> None:
    # Bars spanning all regime windows
    data = _synthetic_bars("2020-01-01", "2025-01-01")
    evals = evaluate_regimes(
        strategy=_FlatStrategy(),
        data=data,
        engine=VectorbtEngine(),
        costs=CostModel(),
        symbol_spec=_spec(),
        initial_cash=Decimal("10000"),
    )
    # Every registered regime should yield one evaluation.
    assert len(evals) == len(REGIMES)
    # Flat strategy does zero trades → total_return ~ 0 → break-even everywhere.
    for e in evals:
        assert e.total_return == pytest.approx(0.0, abs=1e-9)
        assert e.break_even


def test_group_by_family_covers_all_four_names() -> None:
    data = _synthetic_bars("2020-01-01", "2025-01-01")
    evals = evaluate_regimes(
        strategy=_FlatStrategy(),
        data=data,
        engine=VectorbtEngine(),
        costs=CostModel(),
        symbol_spec=_spec(),
        initial_cash=Decimal("10000"),
    )
    families = group_by_family(evals)
    assert set(families) == {"uptrend", "range", "crash", "lowvol"}


def test_all_families_break_even_gate_passes_for_flat_strategy() -> None:
    data = _synthetic_bars("2020-01-01", "2025-01-01")
    evals = evaluate_regimes(
        strategy=_FlatStrategy(),
        data=data,
        engine=VectorbtEngine(),
        costs=CostModel(),
        symbol_spec=_spec(),
        initial_cash=Decimal("10000"),
    )
    assert all_families_break_even(evals) is True


def test_skip_windows_shorter_than_min_bars() -> None:
    # Data that only covers one of the regime windows
    data = _synthetic_bars("2022-05-05", "2022-05-17")  # covers luna_collapse only
    evals = evaluate_regimes(
        strategy=_FlatStrategy(),
        data=data,
        engine=VectorbtEngine(),
        costs=CostModel(),
        symbol_spec=_spec(),
        initial_cash=Decimal("10000"),
        min_bars=60,
    )
    labels = {e.window.label for e in evals}
    assert labels == {"luna_collapse"}
