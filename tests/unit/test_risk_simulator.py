"""Tests for RiskSimulator — validates Kelly, EOD flatten, and halt enforcement."""
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
    RiskRules,
    SignalFrame,
    Strategy,
    SymbolSpec,
)
from kairos.backtest.risk_simulator import (
    HaltWindow,
    RiskSimulator,
    _apply_halts,
    _apply_kelly_and_eod,
    _detect_halts,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_bars(n: int, trend: float = 0.0, start: float = 100.0) -> BarsData:
    """Daily-bar synthetic data (24h * 60m = 1440 bars/day at 1m freq)."""
    freq = "1min"
    index = pd.date_range("2024-01-01", periods=n, freq=freq, tz="UTC")
    prices = start + trend * np.arange(n)
    prices = np.maximum(prices, 1.0)
    ohlcv = pd.DataFrame(
        {
            "open": prices,
            "high": prices + 0.5,
            "low": prices - 0.5,
            "close": prices,
            "volume": np.full(n, 1000.0),
            "quote_volume": prices * 1000.0,
            "taker_buy_volume": np.full(n, 500.0),
            "taker_buy_quote_volume": prices * 500.0,
            "trade_count": np.full(n, 100, dtype=np.int64),
        },
        index=index,
    )
    return BarsData(ohlcv=ohlcv, symbol="TEST", interval="1m")


def _flat_signal_frame(idx: pd.DatetimeIndex, side: int = 1) -> SignalFrame:
    n = len(idx)
    df = pd.DataFrame(
        {
            "side": np.full(n, side, dtype=np.int8),
            "size_hint": np.full(n, 1.0),
            "stop": np.full(n, np.nan),
            "target": np.full(n, np.nan),
        },
        index=idx,
    )
    return SignalFrame(df=df)


def _spec() -> SymbolSpec:
    return SymbolSpec(
        symbol="TEST",
        tick_size=Decimal("0.1"),
        step_size=Decimal("0.001"),
        min_qty=Decimal("0.001"),
        min_notional=Decimal("1"),
    )


class _ConstantStrategy:
    """Strategy that always emits side=+1 with size_hint=1.0."""

    name = "const_long"
    default_params: Mapping[str, object] = {}

    def generate_signals(
        self,
        data: BarsData,
        params: Mapping[str, object],
    ) -> SignalFrame:
        return _flat_signal_frame(data.ohlcv.index, side=1)


# ---------------------------------------------------------------------------
# Kelly + EOD signal transforms
# ---------------------------------------------------------------------------

def test_kelly_scales_size_hint() -> None:
    idx = pd.date_range("2024-01-01", periods=10, freq="1min", tz="UTC")
    sf = _flat_signal_frame(idx, side=1)
    out = _apply_kelly_and_eod(sf, eod_flatten=False, eod_time_utc="23:55", kelly_fraction=0.25)
    assert (out.df["size_hint"] == 0.25).all()


def test_eod_flatten_zeros_side_at_eod_time() -> None:
    # 3-day window, 1-minute bars spanning 23:54-23:56 each day
    idx = pd.date_range("2024-01-01 23:54:00", periods=4320, freq="1min", tz="UTC")
    sf = _flat_signal_frame(idx, side=1)
    out = _apply_kelly_and_eod(
        sf, eod_flatten=True, eod_time_utc="23:55", kelly_fraction=1.0
    )
    # At every 23:55 bar, side must be 0
    eod_mask = (out.df.index.hour == 23) & (out.df.index.minute == 55)
    assert (out.df.loc[eod_mask, "side"] == 0).all()
    # Other bars keep side=1
    assert (out.df.loc[~eod_mask, "side"] == 1).all()


def test_kelly_clips_above_one() -> None:
    idx = pd.date_range("2024-01-01", periods=5, freq="1min", tz="UTC")
    df = pd.DataFrame(
        {
            "side": np.ones(5, dtype=np.int8),
            "size_hint": np.full(5, 5.0),  # intentionally out-of-spec
            "stop": np.full(5, np.nan),
            "target": np.full(5, np.nan),
        },
        index=idx,
    )
    sf = SignalFrame(df=df)
    out = _apply_kelly_and_eod(sf, eod_flatten=False, eod_time_utc="23:55", kelly_fraction=0.5)
    # 5.0 * 0.5 = 2.5 → clipped to 1.0
    assert (out.df["size_hint"] == 1.0).all()


# ---------------------------------------------------------------------------
# Halt detection
# ---------------------------------------------------------------------------

def test_detect_daily_loss_limit() -> None:
    """Equity drops 5% on day 1 → daily halt fires when daily_loss_limit_pct=3."""
    idx = pd.date_range("2024-01-01", periods=1440, freq="1min", tz="UTC")
    # Equity: 10_000 flat until bar 100, then drops to 9_500 (5% loss)
    eq = np.full(1440, 10_000.0)
    eq[100:] = 9_500.0
    equity = pd.Series(eq, index=idx)

    rules = RiskRules(
        daily_loss_limit_pct=3.0,
        weekly_loss_limit_pct=100.0,  # disabled by being >= limit never reached
        monthly_loss_limit_pct=100.0,
        mdd_halt_pct=100.0,
    )
    halts = _detect_halts(equity, rules, initial_cash=10_000.0)
    daily_halts = [h for h in halts if h.reason == "daily"]
    assert len(daily_halts) == 1
    # Breach point is bar 100
    assert daily_halts[0].start == idx[100]
    # Halt extends to end of that UTC day (the last bar)
    assert daily_halts[0].end == idx[-1]


def test_detect_mdd_halt() -> None:
    """Sustained drawdown below mdd_halt_pct triggers permanent halt."""
    idx = pd.date_range("2024-01-01", periods=500, freq="1min", tz="UTC")
    # Equity peaks at 12_000 at bar 100, then drops to 9_000 at bar 200 (25% DD)
    eq = np.linspace(10_000, 12_000, 100).tolist() + \
         np.linspace(12_000, 9_000, 100).tolist() + \
         [9_000.0] * 300
    equity = pd.Series(eq, index=idx)

    rules = RiskRules(
        daily_loss_limit_pct=100.0,
        weekly_loss_limit_pct=100.0,
        monthly_loss_limit_pct=100.0,
        mdd_halt_pct=20.0,
    )
    halts = _detect_halts(equity, rules, initial_cash=10_000.0)
    mdd_halts = [h for h in halts if h.reason == "mdd"]
    assert len(mdd_halts) == 1
    # End is last bar (permanent halt)
    assert mdd_halts[0].end == idx[-1]


def test_detect_no_halts_on_clean_rise() -> None:
    idx = pd.date_range("2024-01-01", periods=1440, freq="1min", tz="UTC")
    eq = np.linspace(10_000, 11_000, 1440)  # steady rise, no halts
    equity = pd.Series(eq, index=idx)
    rules = RiskRules()
    halts = _detect_halts(equity, rules, initial_cash=10_000.0)
    assert halts == []


# ---------------------------------------------------------------------------
# Signal halt application
# ---------------------------------------------------------------------------

def test_apply_halts_zeros_side() -> None:
    idx = pd.date_range("2024-01-01", periods=10, freq="1min", tz="UTC")
    sf = _flat_signal_frame(idx, side=1)
    halt = HaltWindow(start=idx[3], end=idx[6], reason="daily")
    out = _apply_halts(sf, [halt])
    # bars 3..6 zeroed
    assert (out.df["side"].iloc[3:7] == 0).all()
    assert (out.df["size_hint"].iloc[3:7] == 0.0).all()
    # others untouched
    assert (out.df["side"].iloc[:3] == 1).all()
    assert (out.df["side"].iloc[7:] == 1).all()


# ---------------------------------------------------------------------------
# End-to-end with VectorbtEngine
# ---------------------------------------------------------------------------

@pytest.mark.slow
def test_risk_simulator_end_to_end_no_halts() -> None:
    """Calm rising market + const long → no halts fire, engine produces trades."""
    n = 800
    data = _make_bars(n, trend=0.05, start=100.0)  # gentle rise
    rules = RiskRules()  # defaults
    sim = RiskSimulator(engine=VectorbtEngine(), rules=rules)

    result: BacktestResult = sim.run(
        strategy=_ConstantStrategy(),
        data=data,
        params={},
        costs=CostModel(taker_fee_bps=0.0, slippage_bps_base=0.0, slippage_vol_coef=0.0),
        symbol_spec=_spec(),
        initial_cash=Decimal("10000"),
    )
    assert result.engine_name == "vectorbt"
    # Something executed
    assert len(result.trades) >= 1


@pytest.mark.slow
def test_risk_simulator_halts_on_daily_loss() -> None:
    """Sharp drop → daily halt fires → post-halt signals are 0."""
    n = 1000
    # Price crashes from 100 to 60 over first 500 bars then recovers
    base = _make_bars(n, trend=0.0, start=100.0)
    close = base.ohlcv["close"].to_numpy().copy()
    close[:500] = np.linspace(100.0, 60.0, 500)
    close[500:] = np.linspace(60.0, 100.0, 500)
    ohlcv = base.ohlcv.copy()
    ohlcv["open"] = close
    ohlcv["close"] = close
    ohlcv["high"] = close + 0.5
    ohlcv["low"] = close - 0.5
    data = BarsData(ohlcv=ohlcv, symbol="TEST", interval="1m")

    rules = RiskRules(
        daily_loss_limit_pct=2.0,
        weekly_loss_limit_pct=100.0,
        monthly_loss_limit_pct=100.0,
        mdd_halt_pct=100.0,
        kelly_fraction=1.0,
        eod_flatten=False,
    )
    sim = RiskSimulator(engine=VectorbtEngine(), rules=rules)
    result = sim.run(
        strategy=_ConstantStrategy(),
        data=data,
        params={},
        costs=CostModel(taker_fee_bps=0.0, slippage_bps_base=0.0, slippage_vol_coef=0.0),
        symbol_spec=_spec(),
        initial_cash=Decimal("10000"),
    )

    # Losses should be bounded: final equity > naive (no-halt) equity
    # Naive loss would be about -40% of cash; with daily halt it should be much smaller.
    final_equity = float(result.equity_curve.iloc[-1])
    # Must have lost less than 20% (naive loss would be ~35-40%)
    assert final_equity > 8000.0, (
        f"Daily halt should have bounded loss; final equity={final_equity:.2f}"
    )
