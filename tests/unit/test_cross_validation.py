"""Phase 2 Go/No-Go gate: dual-engine cross-validation.

Same strategy → VectorbtEngine and BacktraderEngine. Final equity must agree
within ±1% on a benchmark SMA crossover. If this fails, no strategy in Phase 3
can trust its backtest results.
"""
from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from kairos.backtest.engine_bt import BacktraderEngine
from kairos.backtest.engine_vbt import VectorbtEngine
from kairos.backtest.protocols import (
    BarsData,
    BacktestResult,
    CostModel,
    SignalFrame,
    SymbolSpec,
)


# ---------------------------------------------------------------------------
# Reference strategy: SMA crossover
# ---------------------------------------------------------------------------

class SmaCrossover:
    """Long when fast_sma > slow_sma, flat otherwise.

    Purely deterministic, no look-ahead (rolling() uses only past values).
    Returns 0 for bars where the slow SMA is not yet defined.
    """

    name: str = "sma_crossover"
    default_params: Mapping[str, object] = {"fast": 10, "slow": 30}

    def generate_signals(
        self,
        data: BarsData,
        params: Mapping[str, object],
    ) -> SignalFrame:
        close = data.ohlcv["close"]
        fast_w = int(params.get("fast", 10))
        slow_w = int(params.get("slow", 30))

        fast = close.rolling(fast_w, min_periods=fast_w).mean()
        slow = close.rolling(slow_w, min_periods=slow_w).mean()

        n = len(close)
        side = np.zeros(n, dtype=np.int8)
        mask = (fast > slow).to_numpy()
        # Where slow is NaN, keep 0.
        valid = (~slow.isna()).to_numpy()
        side[valid & mask] = 1

        df = pd.DataFrame(
            {
                "side": side,
                "size_hint": np.ones(n, dtype=np.float64),
                "stop": np.full(n, np.nan),
                "target": np.full(n, np.nan),
            },
            index=data.ohlcv.index,
        )
        return SignalFrame(df=df)


# ---------------------------------------------------------------------------
# Synthetic data
# ---------------------------------------------------------------------------

def _make_bars(n: int, seed: int = 42) -> BarsData:
    """Random-walk OHLCV with enough variation to produce crossovers."""
    rng = np.random.default_rng(seed)
    # Log-normal random walk so price stays positive, with a slight drift
    increments = rng.normal(loc=0.0001, scale=0.003, size=n)
    logp = np.cumsum(increments) + np.log(100.0)
    close = np.exp(logp)

    # OHLC centred on close with bar-level noise
    spread = close * 0.001
    open_ = np.concatenate([[close[0]], close[:-1]])
    high = np.maximum(open_, close) + spread
    low = np.minimum(open_, close) - spread

    index = pd.date_range("2024-01-01", periods=n, freq="1min", tz="UTC")
    ohlcv = pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": np.full(n, 1000.0),
            "quote_volume": close * 1000.0,
            "taker_buy_volume": np.full(n, 500.0),
            "taker_buy_quote_volume": close * 500.0,
            "trade_count": np.full(n, 100, dtype=np.int64),
        },
        index=index,
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


def _rel_diff(a: float, b: float) -> float:
    denom = max(abs(a), abs(b), 1e-9)
    return abs(a - b) / denom


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.slow
def test_sma_crossover_equity_agreement_zero_cost() -> None:
    """With zero fees and zero slippage, final equity must agree within 1%."""
    data = _make_bars(n=800, seed=7)
    costs = CostModel(taker_fee_bps=0.0, slippage_bps_base=0.0, slippage_vol_coef=0.0)
    strategy = SmaCrossover()
    initial = Decimal("10000")

    vbt_result: BacktestResult = VectorbtEngine().run(
        strategy=strategy,
        data=data,
        params={"fast": 10, "slow": 30},
        costs=costs,
        symbol_spec=_spec(),
        initial_cash=initial,
    )
    bt_result: BacktestResult = BacktraderEngine().run(
        strategy=strategy,
        data=data,
        params={"fast": 10, "slow": 30},
        costs=costs,
        symbol_spec=_spec(),
        initial_cash=initial,
    )

    vbt_final = float(vbt_result.equity_curve.iloc[-1])
    bt_final = float(bt_result.equity_curve.iloc[-1])

    # Both engines must have traded
    assert len(vbt_result.trades) >= 3, f"vbt trades={len(vbt_result.trades)}"
    assert len(bt_result.trades) >= 3, f"bt  trades={len(bt_result.trades)}"

    # Final equity within 1 %
    diff = _rel_diff(vbt_final, bt_final)
    assert diff < 0.01, (
        f"Zero-cost equity divergence > 1%: "
        f"vbt={vbt_final:.4f}, bt={bt_final:.4f}, rel_diff={diff:.4%}"
    )


@pytest.mark.slow
def test_sma_crossover_equity_agreement_with_fees() -> None:
    """With realistic 4.5 bps taker fee, final equity must agree within 1%."""
    data = _make_bars(n=800, seed=11)
    costs = CostModel(taker_fee_bps=4.5, slippage_bps_base=0.0, slippage_vol_coef=0.0)
    strategy = SmaCrossover()
    initial = Decimal("10000")

    vbt_result = VectorbtEngine().run(
        strategy=strategy,
        data=data,
        params={"fast": 10, "slow": 30},
        costs=costs,
        symbol_spec=_spec(),
        initial_cash=initial,
    )
    bt_result = BacktraderEngine().run(
        strategy=strategy,
        data=data,
        params={"fast": 10, "slow": 30},
        costs=costs,
        symbol_spec=_spec(),
        initial_cash=initial,
    )

    vbt_final = float(vbt_result.equity_curve.iloc[-1])
    bt_final = float(bt_result.equity_curve.iloc[-1])

    diff = _rel_diff(vbt_final, bt_final)
    assert diff < 0.01, (
        f"With-fees equity divergence > 1%: "
        f"vbt={vbt_final:.4f}, bt={bt_final:.4f}, rel_diff={diff:.4%}"
    )


@pytest.mark.slow
def test_sma_crossover_trade_count_close() -> None:
    """Both engines should produce a similar number of trades (±2)."""
    data = _make_bars(n=800, seed=3)
    costs = CostModel(taker_fee_bps=0.0, slippage_bps_base=0.0, slippage_vol_coef=0.0)
    strategy = SmaCrossover()
    initial = Decimal("10000")

    vbt_trades = len(
        VectorbtEngine().run(
            strategy=strategy, data=data, params={"fast": 10, "slow": 30},
            costs=costs, symbol_spec=_spec(), initial_cash=initial,
        ).trades
    )
    bt_trades = len(
        BacktraderEngine().run(
            strategy=strategy, data=data, params={"fast": 10, "slow": 30},
            costs=costs, symbol_spec=_spec(), initial_cash=initial,
        ).trades
    )

    assert abs(vbt_trades - bt_trades) <= 2, (
        f"Trade count mismatch: vbt={vbt_trades}, bt={bt_trades}"
    )
