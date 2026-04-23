"""Unit tests for VectorbtEngine adapter.

Tests run fully offline with synthetic data (no network, no file I/O).
First run may be slow (numba JIT compilation, 10-30 s expected).
"""
from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from kairos.backtest.engine_vbt import VectorbtEngine
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
    """Build a synthetic BarsData with *n* 1-minute bars.

    trend: price change per bar (e.g. 0.2 → price rises 0.2 each bar).
    All OHLCV values are derived from the price series.
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


def _high_costs() -> CostModel:
    return CostModel(taker_fee_bps=100.0, slippage_bps_base=0.0, slippage_vol_coef=0.0)


class FakeStrategy:
    """Minimal Strategy implementation that returns a pre-defined side array."""

    name: str = "fake_vbt"
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
# Tests
# ---------------------------------------------------------------------------


def test_flat_signal_zero_trades() -> None:
    """500-bar synthetic with side always 0 → trades empty, equity constant."""
    n = 500
    data = _make_synthetic(n, trend=0.0)
    strategy = FakeStrategy(sides=[0] * n)
    engine = VectorbtEngine()
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
    assert len(result.equity_curve) == n, (
        f"Equity curve length {len(result.equity_curve)} != {n}"
    )
    # Equity should be flat (no trades, no costs)
    assert float(result.equity_curve.iloc[-1]) == pytest.approx(float(initial), rel=1e-6)
    assert result.engine_name == "vectorbt"


def test_single_long_trade_profitable() -> None:
    """Price rises 100→120 over 200 bars; side=+1 at bar 10, side=0 at bar 100.

    Expect exactly 1 trade with net_pnl > 0, engine_name='vectorbt'.
    """
    n = 200
    # price goes from 100 to ~120 over first 100 bars then flat
    data = _make_synthetic(n, trend=0.2, start_price=100.0)

    # Signal at bar 10, exit at bar 100
    sides = [0] * 10 + [1] * 90 + [0] * 100
    assert len(sides) == n

    strategy = FakeStrategy(sides=sides)
    engine = VectorbtEngine()
    initial = Decimal("10000")

    result = engine.run(
        strategy=strategy,
        data=data,
        params={"test_param": "abc"},
        costs=_zero_costs(),
        symbol_spec=_make_spec(),
        initial_cash=initial,
    )

    assert result.engine_name == "vectorbt"
    assert len(result.trades) >= 1, f"Expected ≥1 trade, got {len(result.trades)}"

    trade = result.trades.iloc[0]
    assert trade["side"] == 1, f"Expected long trade (side=1), got side={trade['side']}"
    assert trade["net_pnl"] > 0, f"Expected profitable trade, got net_pnl={trade['net_pnl']}"
    assert trade["qty"] > 0, "qty must be positive"
    assert trade["entry_price"] < trade["exit_price"], (
        f"Long trade: entry {trade['entry_price']} should be below exit {trade['exit_price']}"
    )
    # Equity curve has same length as ohlcv
    assert len(result.equity_curve) == n
    # params round-trip
    assert result.params["test_param"] == "abc"


def test_cost_reduces_pnl() -> None:
    """Same profitable trade: taker_fee_bps=100 vs 0 → net_pnl lower with fees.

    Approximate fee delta: 2 * notional * fee_fraction (entry + exit).
    """
    n = 200
    data = _make_synthetic(n, trend=0.2, start_price=100.0)
    sides = [0] * 10 + [1] * 90 + [0] * 100
    assert len(sides) == n

    engine = VectorbtEngine()
    initial = Decimal("10000")
    spec = _make_spec()

    result_free = engine.run(
        strategy=FakeStrategy(sides=sides),
        data=data,
        params={},
        costs=_zero_costs(),
        symbol_spec=spec,
        initial_cash=initial,
    )

    result_costly = engine.run(
        strategy=FakeStrategy(sides=sides),
        data=data,
        params={},
        costs=_high_costs(),
        symbol_spec=spec,
        initial_cash=initial,
    )

    assert len(result_free.trades) >= 1
    assert len(result_costly.trades) >= 1

    pnl_free = float(result_free.trades.iloc[0]["net_pnl"])
    pnl_costly = float(result_costly.trades.iloc[0]["net_pnl"])

    assert pnl_costly < pnl_free, (
        f"Higher fees should reduce pnl: free={pnl_free:.4f}, costly={pnl_costly:.4f}"
    )

    # Approximate delta check: 2 * entry_notional * 1% (entry + exit, ignoring price change)
    trade = result_costly.trades.iloc[0]
    entry_notional = float(trade["qty"]) * float(trade["entry_price"])
    expected_fee_delta = 2.0 * entry_notional * 100.0 * 1e-4
    actual_delta = pnl_free - pnl_costly

    # Allow 30% tolerance since exit price differs from entry price
    assert actual_delta == pytest.approx(expected_fee_delta, rel=0.30), (
        f"Fee delta {actual_delta:.4f} not close to expected {expected_fee_delta:.4f}"
    )

    # The fees column in the trade record should be non-zero for the costly run
    assert float(trade["fees"]) > 0, "fees column should be positive with costs enabled"


def test_signal_shift_no_lookahead() -> None:
    """Signal at bar T must execute at bar T+1 (price='nextopen' semantics).

    We place the long signal starting at bar index `signal_bar`.
    The trade entry_time must correspond to bar signal_bar+1.
    """
    n = 80
    data = _make_synthetic(n, trend=0.0, start_price=100.0)

    signal_bar = 10  # 0-based; signal first at bar 10
    sides = [0] * signal_bar + [1] * (n - signal_bar - 1) + [0]
    assert len(sides) == n

    strategy = FakeStrategy(sides=sides)
    engine = VectorbtEngine()
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

    # Bar T=signal_bar is the signal bar; T+1=signal_bar+1 is the execution bar
    expected_entry_time = data.ohlcv.index[signal_bar + 1]
    actual_entry_time = pd.Timestamp(trade["entry_time"])

    assert actual_entry_time == expected_entry_time, (
        f"No-lookahead violated: entry_time={actual_entry_time} "
        f"expected={expected_entry_time} (bar {signal_bar + 1})"
    )


def test_short_trade_produces_negative_side() -> None:
    """Synthetic down move; short entry → trade with side=-1, net_pnl > 0."""
    n = 200
    # Price falls from 100 to ~80 over 100 bars
    data = _make_synthetic(n, trend=-0.2, start_price=100.0)

    sides = [0] * 10 + [-1] * 90 + [0] * 100
    assert len(sides) == n

    strategy = FakeStrategy(sides=sides)
    engine = VectorbtEngine()
    initial = Decimal("10000")

    result = engine.run(
        strategy=strategy,
        data=data,
        params={},
        costs=_zero_costs(),
        symbol_spec=_make_spec(),
        initial_cash=initial,
    )

    assert len(result.trades) >= 1, f"Expected ≥1 trade, got {len(result.trades)}"
    trade = result.trades.iloc[0]

    assert int(trade["side"]) == -1, (
        f"Expected short trade (side=-1), got side={trade['side']}"
    )
    assert trade["net_pnl"] > 0, (
        f"Expected profitable short on downtrend, got net_pnl={trade['net_pnl']}"
    )
    assert trade["entry_price"] > trade["exit_price"], (
        f"Short entry {trade['entry_price']} should be above exit {trade['exit_price']}"
    )


def test_backtest_result_schema() -> None:
    """BacktestResult must contain all required fields with correct types."""
    n = 100
    data = _make_synthetic(n, trend=0.1)
    sides = [0] * 5 + [1] * 85 + [0] * 10
    strategy = FakeStrategy(sides=sides)
    engine = VectorbtEngine()
    initial = Decimal("5000")

    result = engine.run(
        strategy=strategy,
        data=data,
        params={"window": 14},
        costs=CostModel(taker_fee_bps=4.5, slippage_bps_base=1.0, slippage_vol_coef=0.5),
        symbol_spec=_make_spec(),
        initial_cash=initial,
    )

    assert result.engine_name == "vectorbt"

    # equity_curve is UTC-aware DatetimeIndex pd.Series
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
    assert required_keys.issubset(result.metrics.keys()), (
        f"Missing keys: {required_keys - set(result.metrics.keys())}"
    )

    # params round-trip
    assert result.params["window"] == 14

    # trades columns when non-empty
    if len(result.trades) > 0:
        required_cols = {
            "entry_time", "exit_time", "side", "qty",
            "entry_price", "exit_price", "gross_pnl",
            "fees", "slippage", "funding_cost", "net_pnl",
            "bars_held", "exit_reason",
        }
        assert required_cols.issubset(result.trades.columns), (
            f"Missing columns: {required_cols - set(result.trades.columns)}"
        )

        # Dtypes spot check
        t = result.trades.iloc[0]
        assert isinstance(float(t["qty"]), float)
        assert isinstance(float(t["net_pnl"]), float)
        assert isinstance(int(t["bars_held"]), int)
        assert isinstance(str(t["exit_reason"]), str)


def test_15m_interval() -> None:
    """Engine runs correctly with 15m interval data, bars_per_year=35040."""
    n = 100
    data = _make_synthetic(n, trend=0.1, interval="15m")
    sides = [0] * 10 + [1] * 80 + [0] * 10
    strategy = FakeStrategy(sides=sides)
    engine = VectorbtEngine()

    result = engine.run(
        strategy=strategy,
        data=data,
        params={},
        costs=_zero_costs(),
        symbol_spec=_make_spec(),
        initial_cash=Decimal("10000"),
    )

    assert result.engine_name == "vectorbt"
    assert len(result.equity_curve) == n


def test_net_pnl_decomposition_consistent() -> None:
    """gross_pnl - fees - slippage - funding_cost == net_pnl (within floating tolerance)."""
    n = 150
    data = _make_synthetic(n, trend=0.3, start_price=100.0)
    sides = [0] * 5 + [1] * 100 + [0] * 45
    strategy = FakeStrategy(sides=sides)
    engine = VectorbtEngine()

    result = engine.run(
        strategy=strategy,
        data=data,
        params={},
        costs=CostModel(taker_fee_bps=10.0, slippage_bps_base=2.0, slippage_vol_coef=0.0),
        symbol_spec=_make_spec(),
        initial_cash=Decimal("10000"),
    )

    for i, row in result.trades.iterrows():
        expected_net = float(row["gross_pnl"]) - float(row["fees"]) - float(row["slippage"]) - float(row["funding_cost"])
        actual_net = float(row["net_pnl"])
        assert abs(expected_net - actual_net) < 1e-6, (
            f"Trade {i}: gross_pnl - fees - slip - funding != net_pnl: "
            f"{expected_net:.8f} != {actual_net:.8f}"
        )
