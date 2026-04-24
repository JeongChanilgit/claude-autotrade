"""Unit tests for PortfolioRiskSimulator.

Uses a deterministic fake PortfolioEngine so the risk-logic tests are fully
isolated from vectorbt/backtrader execution semantics. This lets us assert
exact halt behavior, Kelly scaling, and metric recomputation without
depending on trade-fill heuristics of a real engine.
"""
from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from kairos.backtest.protocols import (
    BarsData,
    CostModel,
    PortfolioBars,
    PortfolioResult,
    RiskRules,
    Strategy,
    SymbolSpec,
)
from kairos.backtest.risk_simulator import (
    HaltWindow,
    PortfolioRiskSimulator,
    RiskSimulator,
    _detect_halts,
)


# ---------------------------------------------------------------------------
# Deterministic fake PortfolioEngine
# ---------------------------------------------------------------------------

class _FakePortfolioEngine:
    """Returns a caller-supplied PortfolioResult verbatim.

    WHY: real engines carry fill/slippage nondeterminism orthogonal to the
    risk-layer behavior we want to test. A caller-controlled fake lets every
    assertion target exactly the transformation the risk layer performs.
    """

    name = "vectorbt"  # literal matches EngineName

    def __init__(self, result: PortfolioResult) -> None:
        self._result = result

    def run(
        self,
        strategies: dict[str, Strategy],
        portfolio: PortfolioBars,
        params: dict[str, Mapping[str, object]],
        costs: CostModel,
        symbol_specs: dict[str, SymbolSpec],
        initial_cash: Decimal,
    ) -> PortfolioResult:
        return self._result


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

def _minimal_ohlcv(idx: pd.DatetimeIndex) -> pd.DataFrame:
    n = len(idx)
    return pd.DataFrame(
        {
            "open": np.full(n, 100.0),
            "high": np.full(n, 100.5),
            "low": np.full(n, 99.5),
            "close": np.full(n, 100.0),
            "volume": np.full(n, 1000.0),
            "quote_volume": np.full(n, 100_000.0),
            "taker_buy_volume": np.full(n, 500.0),
            "taker_buy_quote_volume": np.full(n, 50_000.0),
            "trade_count": np.full(n, 100, dtype=np.int64),
        },
        index=idx,
    )


def _make_portfolio_bars(idx: pd.DatetimeIndex, symbols: list[str]) -> PortfolioBars:
    w = 1.0 / len(symbols)
    bars = {
        s: BarsData(ohlcv=_minimal_ohlcv(idx), symbol=s, interval="1m") for s in symbols
    }
    return PortfolioBars(bars=bars, interval="1m", weights={s: w for s in symbols})


def _empty_trades() -> pd.DataFrame:
    cols = {
        "symbol": pd.Series(dtype="string"),
        "entry_time": pd.Series(dtype="datetime64[ns, UTC]"),
        "exit_time": pd.Series(dtype="datetime64[ns, UTC]"),
        "side": pd.Series(dtype=np.int8),
        "qty": pd.Series(dtype=np.float64),
        "entry_price": pd.Series(dtype=np.float64),
        "exit_price": pd.Series(dtype=np.float64),
        "gross_pnl": pd.Series(dtype=np.float64),
        "fees": pd.Series(dtype=np.float64),
        "slippage": pd.Series(dtype=np.float64),
        "funding_cost": pd.Series(dtype=np.float64),
        "net_pnl": pd.Series(dtype=np.float64),
        "bars_held": pd.Series(dtype=np.int64),
        "exit_reason": pd.Series(dtype="string"),
    }
    return pd.DataFrame(cols)


def _trade_row(
    symbol: str,
    entry: pd.Timestamp,
    exit_ts: pd.Timestamp,
    qty: float = 1.0,
    net_pnl: float = 10.0,
    exit_reason: str = "signal",
) -> dict:
    return {
        "symbol": symbol,
        "entry_time": entry,
        "exit_time": exit_ts,
        "side": np.int8(1),
        "qty": qty,
        "entry_price": 100.0,
        "exit_price": 100.0 + net_pnl / max(qty, 1e-9),
        "gross_pnl": net_pnl,
        "fees": 0.0,
        "slippage": 0.0,
        "funding_cost": 0.0,
        "net_pnl": net_pnl,
        "bars_held": np.int64(1),
        "exit_reason": exit_reason,
    }


def _make_result(
    equity: pd.Series,
    per_symbol: dict[str, pd.Series],
    trades: pd.DataFrame,
) -> PortfolioResult:
    return PortfolioResult(
        equity_curve=equity,
        per_symbol_equity=per_symbol,
        trades=trades,
        metrics={},
        per_symbol_metrics={s: {} for s in per_symbol},
        engine_name="vectorbt",
        params={},
    )


def _spec(symbol: str = "BTCUSDT") -> SymbolSpec:
    return SymbolSpec(
        symbol=symbol,
        tick_size=Decimal("0.1"),
        step_size=Decimal("0.001"),
        min_qty=Decimal("0.001"),
        min_notional=Decimal("1"),
    )


def _no_op_rules(**overrides) -> RiskRules:
    """Rules with every limit effectively disabled — flip the ones under test."""
    base = dict(
        daily_loss_limit_pct=100.0,
        weekly_loss_limit_pct=100.0,
        monthly_loss_limit_pct=100.0,
        mdd_halt_pct=100.0,
        kelly_fraction=1.0,
        eod_flatten=False,
        eod_time_utc="23:55",
    )
    base.update(overrides)
    return RiskRules(**base)


# ---------------------------------------------------------------------------
# Halt detection unit tests
# ---------------------------------------------------------------------------

def test_detect_daily_loss_limit_fires_at_first_breach() -> None:
    idx = pd.date_range("2024-01-01", periods=1440, freq="1min", tz="UTC")
    eq = np.full(1440, 10_000.0)
    eq[100:] = 9_600.0  # -4% drop
    equity = pd.Series(eq, index=idx)
    rules = _no_op_rules(daily_loss_limit_pct=3.0)
    halts = _detect_halts(equity, rules, initial_cash=10_000.0)
    daily = [h for h in halts if h.reason == "daily"]
    assert len(daily) == 1
    assert daily[0].start == idx[100]
    assert daily[0].end == idx[-1]


def test_detect_no_halts_on_clean_rise() -> None:
    idx = pd.date_range("2024-01-01", periods=1440, freq="1min", tz="UTC")
    equity = pd.Series(np.linspace(10_000, 11_000, 1440), index=idx)
    halts = _detect_halts(equity, RiskRules(), initial_cash=10_000.0)
    assert halts == []


# ---------------------------------------------------------------------------
# End-to-end via fake engine
# ---------------------------------------------------------------------------

def _run(
    sim_result: PortfolioResult,
    rules: RiskRules,
    symbols: list[str],
) -> PortfolioResult:
    idx = sim_result.equity_curve.index
    portfolio = _make_portfolio_bars(idx, symbols)
    engine = _FakePortfolioEngine(sim_result)
    sim = PortfolioRiskSimulator(engine=engine, rules=rules)
    return sim.run(
        strategies={s: None for s in symbols},  # fake engine ignores strategies
        portfolio=portfolio,
        params={s: {} for s in symbols},
        costs=CostModel(),
        symbol_specs={s: _spec(s) for s in symbols},
        initial_cash=Decimal("10000"),
    )


def test_daily_limit_halt_flattens_equity_and_drops_trades() -> None:
    # 4-day window at 1h cadence (96 bars). Day 2 drops 4% (limit=3%).
    idx = pd.date_range("2024-01-01", periods=96, freq="1h", tz="UTC")
    eq = np.full(96, 10_000.0)
    # Day 1 (0..23): flat 10_000. Day 2 (24..47): drops to 9_600 by bar 30.
    # Day 3 (48..71): raw would recover to 10_500.
    # Day 4 (72..95): flat 10_500.
    eq[24:30] = np.linspace(10_000.0, 9_600.0, 6)
    eq[30:48] = 9_600.0
    eq[48:72] = np.linspace(9_600.0, 10_500.0, 24)
    eq[72:] = 10_500.0
    equity = pd.Series(eq, index=idx)

    # Trades: one on day 1 (kept), one entered day 2 after breach (dropped),
    # one on day 4 (kept).
    trades = pd.DataFrame([
        _trade_row("BTCUSDT", idx[5], idx[6], net_pnl=5.0),
        _trade_row("BTCUSDT", idx[35], idx[36], net_pnl=-50.0),
        _trade_row("BTCUSDT", idx[80], idx[81], net_pnl=20.0),
    ])
    per_symbol = {"BTCUSDT": equity.copy()}
    raw = _make_result(equity, per_symbol, trades)

    rules = _no_op_rules(daily_loss_limit_pct=3.0)
    out = _run(raw, rules, ["BTCUSDT"])

    # Day-2 trade dropped; day-1 and day-4 trades kept.
    assert len(out.trades) == 2
    assert list(out.trades["entry_time"]) == [idx[5], idx[80]]

    # Equity is held flat from the breach bar through end of day 2.
    # Day 2 breach occurs somewhere in 24..29 (bar where eq first < 9700).
    breach_bar = int(np.argmax(eq < 9_700.0))
    # After breach through bar 47, equity must be constant at the breach value.
    flat_region = out.equity_curve.iloc[breach_bar:48].to_numpy()
    assert np.allclose(flat_region, flat_region[0])


def test_weekly_limit_halt_covers_rest_of_iso_week() -> None:
    # ISO week starts Monday. 2024-01-01 is a Monday.
    idx = pd.date_range("2024-01-01", periods=7 * 24, freq="1h", tz="UTC")
    eq = np.full(len(idx), 10_000.0)
    # Drop -10% mid-week (weekly limit 8%).
    eq[48:] = 9_000.0
    equity = pd.Series(eq, index=idx)
    trades = pd.DataFrame([
        _trade_row("ETHUSDT", idx[60], idx[61], net_pnl=1.0),  # mid-week, post-breach
        _trade_row("ETHUSDT", idx[10], idx[11], net_pnl=1.0),  # pre-breach, kept
    ])
    raw = _make_result(equity, {"ETHUSDT": equity.copy()}, trades)

    rules = _no_op_rules(weekly_loss_limit_pct=8.0, daily_loss_limit_pct=100.0)
    out = _run(raw, rules, ["ETHUSDT"])
    # Post-breach trade dropped.
    assert len(out.trades) == 1
    assert out.trades.iloc[0]["entry_time"] == idx[10]


def test_monthly_limit_halt_covers_rest_of_month() -> None:
    # 28 days starting 2024-01-01, 4h cadence.
    idx = pd.date_range("2024-01-01", periods=28 * 6, freq="4h", tz="UTC")
    eq = np.full(len(idx), 10_000.0)
    # -20% drop mid-month (monthly limit 15%).
    eq[60:] = 8_000.0
    equity = pd.Series(eq, index=idx)
    trades = pd.DataFrame([
        _trade_row("SOLUSDT", idx[10], idx[11], net_pnl=1.0),
        _trade_row("SOLUSDT", idx[100], idx[101], net_pnl=1.0),  # inside halt
    ])
    raw = _make_result(equity, {"SOLUSDT": equity.copy()}, trades)

    rules = _no_op_rules(
        monthly_loss_limit_pct=15.0,
        weekly_loss_limit_pct=100.0,
        daily_loss_limit_pct=100.0,
    )
    out = _run(raw, rules, ["SOLUSDT"])
    assert len(out.trades) == 1
    assert out.trades.iloc[0]["entry_time"] == idx[10]


def test_mdd_halt_is_permanent() -> None:
    idx = pd.date_range("2024-01-01", periods=200, freq="1h", tz="UTC")
    eq = np.concatenate(
        [
            np.linspace(10_000.0, 12_000.0, 50),
            np.linspace(12_000.0, 9_000.0, 50),  # -25% DD from peak
            np.full(100, 9_000.0),
        ]
    )
    equity = pd.Series(eq, index=idx)
    # Synthetic trade that would execute in the permanent-halt tail.
    trades = pd.DataFrame([
        _trade_row("BTCUSDT", idx[30], idx[31], net_pnl=10.0),   # pre-halt
        _trade_row("BTCUSDT", idx[150], idx[151], net_pnl=10.0),  # in permanent halt
    ])
    raw = _make_result(equity, {"BTCUSDT": equity.copy()}, trades)

    rules = _no_op_rules(mdd_halt_pct=20.0)
    out = _run(raw, rules, ["BTCUSDT"])

    # Halt fires at the bar where DD first ≤ -20%. After that bar, equity
    # is flat for the rest of the run.
    assert len(out.trades) == 1
    assert out.trades.iloc[0]["entry_time"] == idx[30]
    # Last bar equity equals the equity at first breach.
    peak = np.maximum.accumulate(eq)
    dd = (eq - peak) / peak
    first_breach = int(np.argmax(dd <= -0.20))
    assert out.equity_curve.iloc[-1] == pytest.approx(out.equity_curve.iloc[first_breach])


def test_eod_flatten_halts_trading_during_cutoff_window() -> None:
    # Span 2 UTC days with 1-min bars starting 23:50 -> 00:10 next day.
    idx = pd.date_range("2024-01-01 23:50:00", periods=41, freq="1min", tz="UTC")
    # Equity flat — we only care about halt window + trade filtering.
    equity = pd.Series(np.full(len(idx), 10_000.0), index=idx)
    # Bars: 00..09 are 23:50..23:59 (day1), bar 10 is 00:00 day2, bars
    # 11..40 are 00:01..00:30 day2. "23:55" cutoff halts bars 5..9 (day1).
    trades = pd.DataFrame([
        _trade_row("BTCUSDT", idx[0], idx[1], net_pnl=1.0),   # before cutoff: kept
        _trade_row("BTCUSDT", idx[6], idx[7], net_pnl=1.0),   # 23:56 entry: dropped
        _trade_row("BTCUSDT", idx[15], idx[16], net_pnl=1.0),  # next day 00:05: kept
    ])
    raw = _make_result(equity, {"BTCUSDT": equity.copy()}, trades)

    rules = _no_op_rules(eod_flatten=True, eod_time_utc="23:55")
    out = _run(raw, rules, ["BTCUSDT"])

    # The 23:56 trade is dropped; the other two remain.
    assert len(out.trades) == 2
    entry_times = list(out.trades["entry_time"])
    assert idx[6] not in entry_times
    assert idx[0] in entry_times
    assert idx[15] in entry_times


def test_kelly_fraction_scales_trade_qty_and_pnl() -> None:
    idx = pd.date_range("2024-01-01", periods=10, freq="1h", tz="UTC")
    equity = pd.Series(np.full(10, 10_000.0), index=idx)
    trades = pd.DataFrame([
        _trade_row("BTCUSDT", idx[1], idx[2], qty=4.0, net_pnl=100.0),
    ])
    # Set gross/fees/funding so we can verify each scales.
    trades.loc[0, "gross_pnl"] = 120.0
    trades.loc[0, "fees"] = 10.0
    trades.loc[0, "slippage"] = 5.0
    trades.loc[0, "funding_cost"] = 5.0
    trades.loc[0, "net_pnl"] = 100.0
    raw = _make_result(equity, {"BTCUSDT": equity.copy()}, trades)

    rules = _no_op_rules(kelly_fraction=0.25)
    out = _run(raw, rules, ["BTCUSDT"])

    row = out.trades.iloc[0]
    assert row["qty"] == pytest.approx(1.0)
    assert row["gross_pnl"] == pytest.approx(30.0)
    assert row["fees"] == pytest.approx(2.5)
    assert row["slippage"] == pytest.approx(1.25)
    assert row["funding_cost"] == pytest.approx(1.25)
    assert row["net_pnl"] == pytest.approx(25.0)


def test_no_halts_happy_path_preserves_raw_trade_set() -> None:
    idx = pd.date_range("2024-01-01", periods=48, freq="1h", tz="UTC")
    # Gentle rise, no halts possible.
    equity = pd.Series(np.linspace(10_000.0, 10_500.0, 48), index=idx)
    trades = pd.DataFrame([
        _trade_row("BTCUSDT", idx[5], idx[6], net_pnl=10.0),
        _trade_row("ETHUSDT", idx[10], idx[11], net_pnl=-5.0),
    ])
    per_symbol = {
        "BTCUSDT": equity.copy() / 2,
        "ETHUSDT": equity.copy() / 2,
    }
    raw = _make_result(equity, per_symbol, trades)

    rules = _no_op_rules(kelly_fraction=1.0)  # no Kelly scaling either
    out = _run(raw, rules, ["BTCUSDT", "ETHUSDT"])

    assert len(out.trades) == 2
    # Equity exactly preserved (no halts, no Kelly scaling).
    pd.testing.assert_series_equal(out.equity_curve, equity, check_names=False)
    # Metrics were recomputed (non-empty).
    assert "cagr" in out.metrics
    assert "mdd" in out.metrics


def test_daily_halt_dominates_eod_when_fires_first() -> None:
    # Hourly bars across 2 days. Daily limit fires mid-day-2, and EOD would
    # fire at 23:00 — the earlier halt should subsume the EOD window.
    idx = pd.date_range("2024-01-01", periods=48, freq="1h", tz="UTC")
    eq = np.full(48, 10_000.0)
    # Day 2 (bars 24..47): drop to -4% by bar 28, then hold.
    eq[24:28] = np.linspace(10_000.0, 9_600.0, 4)
    eq[28:] = 9_600.0
    equity = pd.Series(eq, index=idx)
    trades = pd.DataFrame([
        _trade_row("BTCUSDT", idx[30], idx[31], net_pnl=1.0),  # inside daily halt
        _trade_row("BTCUSDT", idx[46], idx[47], net_pnl=1.0),  # also inside halt
        _trade_row("BTCUSDT", idx[5], idx[6], net_pnl=1.0),    # day 1 pre-halt
    ])
    raw = _make_result(equity, {"BTCUSDT": equity.copy()}, trades)

    rules = _no_op_rules(daily_loss_limit_pct=3.0, eod_flatten=True, eod_time_utc="23:00")
    out = _run(raw, rules, ["BTCUSDT"])

    # Day-2 trades both dropped (daily halt covers both); day-1 trade kept.
    assert len(out.trades) == 1
    assert out.trades.iloc[0]["entry_time"] == idx[5]
    # Equity past the breach bar is flat all the way to the end.
    first_breach = int(np.argmax(eq < 9_700.0))
    tail = out.equity_curve.iloc[first_breach:].to_numpy()
    assert np.allclose(tail, tail[0])


def test_risksimulator_alias_equals_portfoliorisksimulator() -> None:
    # WHY: kairos.backtest.stress still imports RiskSimulator by that name.
    # Removing the alias would be an incompatible change outside Phase 5E's
    # scope; this test pins the alias.
    assert RiskSimulator is PortfolioRiskSimulator


def test_haltwindow_is_exported_for_stress_module() -> None:
    # stress.py imports HaltWindow from this module — keep it public.
    hw = HaltWindow(
        start=pd.Timestamp("2024-01-01", tz="UTC"),
        end=pd.Timestamp("2024-01-02", tz="UTC"),
        reason="daily",
    )
    assert hw.reason == "daily"
