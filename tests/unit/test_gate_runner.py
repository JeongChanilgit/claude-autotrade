"""Unit test: ensemble ValueError degrades gracefully in run_gate."""
from __future__ import annotations

import math
from decimal import Decimal
from unittest.mock import MagicMock

import pandas as pd
import pytest

from kairos.backtest.protocols import BacktestResult, BarsData


def _make_stub_result() -> BacktestResult:
    idx = pd.date_range("2024-01-01", periods=10, freq="15min", tz="UTC")
    equity = pd.Series(10_000.0, index=idx)
    trades = pd.DataFrame(columns=[
        "entry_time", "exit_time", "side", "qty", "entry_price", "exit_price",
        "gross_pnl", "fees", "slippage", "funding_cost", "net_pnl",
        "bars_held", "exit_reason",
    ])
    return BacktestResult(
        equity_curve=equity,
        trades=trades,
        metrics={"sharpe": 1.5, "cagr": 2.0, "mdd": -0.1, "total_trades": 0},
        engine_name="vectorbt",
        params={},
    )


def _make_bars_data() -> BarsData:
    idx = pd.date_range("2024-01-01", periods=10, freq="15min", tz="UTC")
    ohlcv = pd.DataFrame(
        {
            "open": 50_000.0,
            "high": 50_100.0,
            "low": 49_900.0,
            "close": 50_050.0,
            "volume": 1.0,
            "quote_volume": 50_000.0,
            "taker_buy_volume": 0.5,
            "taker_buy_quote_volume": 25_000.0,
            "trade_count": 100,
        },
        index=idx,
    )
    funding_idx = pd.date_range("2024-01-01", periods=3, freq="8h", tz="UTC")
    funding = pd.DataFrame(
        {"funding_rate": 0.0001, "funding_interval_hours": 8},
        index=funding_idx,
    )
    metrics_idx = pd.date_range("2024-01-01", periods=10, freq="15min", tz="UTC")
    metrics = pd.DataFrame(
        {
            "sum_open_interest": 1e9,
            "sum_open_interest_value": 1e9,
            "count_toptrader_long_short_ratio": 1.0,
            "sum_toptrader_long_short_ratio": 1.0,
            "count_long_short_ratio": 1.0,
            "sum_taker_long_short_vol_ratio": 1.0,
        },
        index=metrics_idx,
    )
    return BarsData(
        ohlcv=ohlcv,
        symbol="BTCUSDT",
        interval="15m",
        funding=funding,
        metrics=metrics,
    )


def test_ensemble_value_error_degrades_gracefully(monkeypatch):
    stub_bars = _make_bars_data()
    stub_result = _make_stub_result()

    import kairos.backtest.gate_runner as gr
    from kairos.strategies.ensemble import EnsembleStrategy

    monkeypatch.setattr(gr, "load_bars", lambda **kwargs: stub_bars)

    original_run = gr.VectorbtEngine.run

    def patched_run(self, strategy, data, params, costs, symbol_spec, initial_cash):
        if isinstance(strategy, EnsembleStrategy):
            raise ValueError("synthetic")
        return stub_result

    monkeypatch.setattr(gr.VectorbtEngine, "run", patched_run)

    result = gr.run_gate(
        symbol="BTCUSDT",
        interval="15m",
        start=pd.Timestamp("2024-01-01", tz="UTC"),
        end=pd.Timestamp("2024-01-02", tz="UTC"),
        initial_cash=Decimal("10000"),
    )

    assert isinstance(result, dict)
    assert math.isnan(result["ensemble"]["cagr"])
    assert math.isnan(result["ensemble"]["mdd"])
    assert result["ensemble"]["total_trades"] == 0
