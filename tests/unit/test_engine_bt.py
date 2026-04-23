"""Unit tests for BacktraderEngine adapter.

Tests run fully offline with synthetic data (no network, no file I/O).
Each test completes well under 30 s.

Key backtrader execution note: a Market order placed at bar T fills at bar T+1 open.
Therefore "signal at bar T" means the entry fill appears at bar T+1.
To *close* a position, the zero-signal must appear at bar T such that bar T+1 fills
the close order.  The final bar of the series cannot produce a filled order (there
is no bar T+1), so tests must leave at least 1 trailing flat bar after any close signal.
"""
from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from kairos.backtest.engine_bt import BacktraderEngine
from kairos.backtest.protocols import (
    BacktestResult,
    BarsData,
    CostModel,
    SignalFrame,
    SymbolSpec,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_synthetic(
    n: int,
    trend: float = 0.0,
    start_price: float = 100.0,
    interval: str = "1m",
) -> BarsData:
    """Build a synthetic BarsData with *n* bars.

    trend: price change per bar (e.g. 0.2 → price rises by 0.2 each bar).
    """
    freq = "1min" if interval == "1m" else "15min"
    index = pd.date_range("2024-01-01", periods=n, freq=freq, tz="UTC")
    prices = start_price + trend * np.arange(n)
    prices = np.maximum(prices, 1.0)  # keep positive

    ohlcv = pd.DataFrame(
        {
            "open": prices,
            "high": prices + 0.5,
            "low": prices - 0.5,
            "close": prices,
            "volume": np.full(n, 1_000.0),
            "quote_volume": prices * 1_000.0,
            "taker_buy_volume": np.full(n, 500.0),
            "taker_buy_quote_volume": prices * 500.0,
            "trade_count": np.full(n, 100, dtype=np.int64),
        },
        index=index,
    )
    return BarsData(ohlcv=ohlcv, symbol="TESTUSDT", interval=interval)


def _make_spec(
    tick_size: str = "0.1",
    step_size: str = "0.001",
    min_qty: str = "0.001",
    min_notional: str = "5",
) -> SymbolSpec:
    return SymbolSpec(
        symbol="TESTUSDT",
        tick_size=Decimal(tick_size),
        step_size=Decimal(step_size),
        min_qty=Decimal(min_qty),
        min_notional=Decimal(min_notional),
    )


def _zero_costs() -> CostModel:
    return CostModel(taker_fee_bps=0.0, slippage_bps_base=0.0, slippage_vol_coef=0.0)


def _default_costs() -> CostModel:
    return CostModel(taker_fee_bps=4.5, slippage_bps_base=1.0, slippage_vol_coef=0.5)


class FakeStrategy:
    """Minimal Strategy implementation that returns a pre-defined side array."""

    name: str = "fake"
    default_params: Mapping[str, object] = {}

    def __init__(self, sides: list[int]) -> None:
        self._sides = sides

    def generate_signals(
        self,
        data: BarsData,
        params: Mapping[str, object],
    ) -> SignalFrame:
        n = len(data.ohlcv)
        sides = list(self._sides[:n])
        # Pad with 0 if too short
        if len(sides) < n:
            sides = sides + [0] * (n - len(sides))

        df = pd.DataFrame(
            {
                "side": np.array(sides, dtype=np.int8),
                "size_hint": np.ones(n, dtype=np.float64),
                "stop": np.full(n, float("nan")),
                "target": np.full(n, float("nan")),
            },
            index=data.ohlcv.index,
        )
        return SignalFrame(df=df)


# ---------------------------------------------------------------------------
# Tests (≥5 required by spec; 8 provided)
# ---------------------------------------------------------------------------


def test_flat_signal_zero_trades() -> None:
    """500 bars with side always 0 → no trades, equity constant."""
    n = 500
    data = _make_synthetic(n, trend=0.0)
    strategy = FakeStrategy(sides=[0] * n)
    engine = BacktraderEngine()
    initial = Decimal("10000")

    result: BacktestResult = engine.run(
        strategy=strategy,
        data=data,
        params={},
        costs=_zero_costs(),
        symbol_spec=_make_spec(),
        initial_cash=initial,
    )

    assert isinstance(result, BacktestResult)
    assert len(result.trades) == 0, f"Expected 0 trades, got {len(result.trades)}"
    assert len(result.equity_curve) == n
    # Equity should be flat (no trades, no costs)
    assert float(result.equity_curve.iloc[-1]) == pytest.approx(float(initial), rel=1e-6)
    assert result.engine_name == "backtrader"


def test_single_long_trade_profitable() -> None:
    """Price rises 100→120 over 120 bars; signal +1 at bar 10, 0 at bar 100.

    The close order at bar 100 fills at bar 101 (open).
    n=120 leaves plenty of trailing flat bars.
    Expect exactly 1 trade, net_pnl > 0.
    """
    n = 120
    data = _make_synthetic(n, trend=0.2, start_price=100.0)

    # Signal: 0 for bars 0–9, +1 for bars 10–99, 0 for bars 100–119
    sides = [0] * 10 + [1] * 90 + [0] * 20
    assert len(sides) == n

    strategy = FakeStrategy(sides=sides)
    engine = BacktraderEngine()
    initial = Decimal("10000")

    result = engine.run(
        strategy=strategy,
        data=data,
        params={},
        costs=_zero_costs(),
        symbol_spec=_make_spec(),
        initial_cash=initial,
    )

    assert len(result.trades) == 1, f"Expected 1 trade, got {len(result.trades)}"
    trade = result.trades.iloc[0]
    assert trade["side"] == 1, "Expected long trade"
    assert trade["net_pnl"] > 0, f"Expected profitable trade, got net_pnl={trade['net_pnl']}"
    assert trade["qty"] > 0
    assert trade["entry_price"] < trade["exit_price"]


def test_cost_reduces_pnl() -> None:
    """Same long trade: 100 bps fee vs 0 bps → lower net_pnl with fees.

    Delta should be approximately 2 × entry_notional × fee_fraction.
    """
    n = 120
    data = _make_synthetic(n, trend=0.2, start_price=100.0)
    sides = [0] * 10 + [1] * 90 + [0] * 20
    strategy_no_cost = FakeStrategy(sides=sides)
    strategy_with_cost = FakeStrategy(sides=sides)

    engine = BacktraderEngine()
    initial = Decimal("10000")
    spec = _make_spec()

    # Run with zero costs
    res_free = engine.run(
        strategy=strategy_no_cost,
        data=data,
        params={},
        costs=_zero_costs(),
        symbol_spec=spec,
        initial_cash=initial,
    )

    # Run with 100 bps taker fee, no slippage
    high_cost = CostModel(taker_fee_bps=100.0, slippage_bps_base=0.0, slippage_vol_coef=0.0)
    res_costly = engine.run(
        strategy=strategy_with_cost,
        data=data,
        params={},
        costs=high_cost,
        symbol_spec=spec,
        initial_cash=initial,
    )

    assert len(res_free.trades) == 1
    assert len(res_costly.trades) == 1

    pnl_free = float(res_free.trades.iloc[0]["net_pnl"])
    pnl_costly = float(res_costly.trades.iloc[0]["net_pnl"])

    assert pnl_costly < pnl_free, (
        f"Costs should reduce pnl: free={pnl_free:.4f} costly={pnl_costly:.4f}"
    )

    # Delta should be roughly 2 × entry_notional × fee (entry + exit rounds ≈ same price)
    trade = res_costly.trades.iloc[0]
    entry_notional = float(trade["qty"]) * float(trade["entry_price"])
    expected_delta = 2.0 * entry_notional * 100.0 * 1e-4
    actual_delta = pnl_free - pnl_costly

    # Allow 40% tolerance (exit notional differs from entry notional)
    assert actual_delta == pytest.approx(expected_delta, rel=0.40), (
        f"Fee delta {actual_delta:.4f} not close to expected {expected_delta:.4f}"
    )


def test_signal_shift_no_lookahead() -> None:
    """Signal appears at bar T (0-indexed); entry must be at bar T+1.

    We set side=+1 starting from bar index 10 (0-based).
    The entry_time in the trade should equal the timestamp of bar index 11.
    """
    n = 120
    data = _make_synthetic(n, trend=0.0, start_price=100.0)

    signal_bar = 10  # 0-based: first bar where side becomes +1
    sides = [0] * signal_bar + [1] * (n - signal_bar - 20) + [0] * 20
    assert len(sides) == n

    strategy = FakeStrategy(sides=sides)
    engine = BacktraderEngine()
    initial = Decimal("10000")

    result = engine.run(
        strategy=strategy,
        data=data,
        params={},
        costs=_zero_costs(),
        symbol_spec=_make_spec(),
        initial_cash=initial,
    )

    assert len(result.trades) >= 1, "Expected at least 1 trade"
    trade = result.trades.iloc[0]

    # Bar T = signal_bar=10 carries the signal; T+1 = bar 11 is the entry bar
    expected_entry_time = data.ohlcv.index[signal_bar + 1]
    actual_entry_time = pd.Timestamp(trade["entry_time"])

    assert actual_entry_time == expected_entry_time, (
        f"No-lookahead violated: entry_time={actual_entry_time} "
        f"expected={expected_entry_time}"
    )


def test_short_trade_on_downtrend() -> None:
    """Price falls 100→80 over 120 bars; side=-1 at bar 10 → profitable short.

    Expect 1 trade, side=-1, net_pnl > 0.
    """
    n = 120
    data = _make_synthetic(n, trend=-0.2, start_price=100.0)

    sides = [0] * 10 + [-1] * 90 + [0] * 20
    assert len(sides) == n

    strategy = FakeStrategy(sides=sides)
    engine = BacktraderEngine()
    initial = Decimal("10000")

    result = engine.run(
        strategy=strategy,
        data=data,
        params={},
        costs=_zero_costs(),
        symbol_spec=_make_spec(),
        initial_cash=initial,
    )

    assert len(result.trades) == 1, f"Expected 1 trade, got {len(result.trades)}"
    trade = result.trades.iloc[0]
    assert trade["side"] == -1, f"Expected short trade, got side={trade['side']}"
    assert trade["net_pnl"] > 0, f"Expected profitable short, got net_pnl={trade['net_pnl']}"
    assert trade["entry_price"] > trade["exit_price"], (
        "Short entry should be above exit for profit"
    )


def test_backtest_result_schema() -> None:
    """BacktestResult must have all required fields with correct dtypes."""
    n = 120
    data = _make_synthetic(n, trend=0.1)
    sides = [0] * 5 + [1] * 100 + [0] * 15
    assert len(sides) == n
    strategy = FakeStrategy(sides=sides)
    engine = BacktraderEngine()
    initial = Decimal("5000")

    result = engine.run(
        strategy=strategy,
        data=data,
        params={"foo": 42},
        costs=_default_costs(),
        symbol_spec=_make_spec(),
        initial_cash=initial,
    )

    assert result.engine_name == "backtrader"

    # equity_curve is UTC-aware DatetimeIndex
    assert isinstance(result.equity_curve, pd.Series)
    assert result.equity_curve.index.tz is not None
    assert len(result.equity_curve) == n

    # metrics has all required keys
    required_keys = {
        "cagr", "mdd", "sharpe", "sortino", "calmar",
        "profit_factor", "max_consec_loss", "var_95", "cvar_95",
        "total_trades", "win_rate", "avg_win", "avg_loss",
        "exposure", "turnover",
    }
    assert required_keys.issubset(result.metrics.keys())

    # params round-trip
    assert result.params["foo"] == 42

    # trades columns present when trades exist
    if len(result.trades) > 0:
        required_cols = {
            "entry_time", "exit_time", "side", "qty",
            "entry_price", "exit_price", "gross_pnl",
            "fees", "slippage", "funding_cost", "net_pnl",
            "bars_held", "exit_reason",
        }
        assert required_cols.issubset(result.trades.columns)
        assert result.trades["side"].dtype == np.dtype("int8")
        assert str(result.trades["entry_time"].dtype) == "datetime64[ns, UTC]"


def test_equity_curve_length_matches_ohlcv() -> None:
    """Equity curve must have the same length as the OHLCV input."""
    for n in [50, 200]:
        data = _make_synthetic(n, trend=0.05)
        sides = [0] * 5 + [1] * (n - 25) + [0] * 20
        strategy = FakeStrategy(sides=sides[:n])
        engine = BacktraderEngine()

        result = engine.run(
            strategy=strategy,
            data=data,
            params={},
            costs=_zero_costs(),
            symbol_spec=_make_spec(),
            initial_cash=Decimal("10000"),
        )

        assert len(result.equity_curve) == n, (
            f"n={n}: equity curve len={len(result.equity_curve)}"
        )


def test_15m_interval() -> None:
    """Engine runs correctly with 15m interval data."""
    n = 100
    data = _make_synthetic(n, trend=0.1, interval="15m")
    sides = [0] * 10 + [1] * 70 + [0] * 20
    assert len(sides) == n
    strategy = FakeStrategy(sides=sides)
    engine = BacktraderEngine()

    result = engine.run(
        strategy=strategy,
        data=data,
        params={},
        costs=_zero_costs(),
        symbol_spec=_make_spec(),
        initial_cash=Decimal("10000"),
    )

    assert result.engine_name == "backtrader"
    assert len(result.equity_curve) == n
