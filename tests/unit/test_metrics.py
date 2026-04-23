"""Unit tests for kairos.backtest.metrics.compute_metrics.

Each test is fast (<200ms) and verifies a single metric or edge-case cluster.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from kairos.backtest.metrics import compute_metrics


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_equity(values: list[float], freq: str = "1min") -> pd.Series:
    idx = pd.date_range(start="2024-01-01", periods=len(values), freq=freq, tz="UTC")
    return pd.Series(values, index=idx, dtype=float)


def _empty_trades() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "entry_time", "exit_time", "side", "qty",
            "entry_price", "exit_price", "gross_pnl",
            "fees", "slippage", "funding_cost", "net_pnl",
            "bars_held", "exit_reason",
        ]
    )


def _make_trades(net_pnls: list[float], bars_held: int = 10) -> pd.DataFrame:
    """Build a minimal trades DataFrame with the required columns."""
    n = len(net_pnls)
    return pd.DataFrame(
        {
            "entry_time": pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC"),
            "exit_time": pd.date_range("2024-01-02", periods=n, freq="1h", tz="UTC"),
            "side": np.ones(n, dtype=np.int8),
            "qty": np.ones(n, dtype=float),
            "entry_price": np.full(n, 100.0),
            "exit_price": np.array([100.0 + p for p in net_pnls]),
            "gross_pnl": np.array(net_pnls, dtype=float),
            "fees": np.zeros(n, dtype=float),
            "slippage": np.zeros(n, dtype=float),
            "funding_cost": np.zeros(n, dtype=float),
            "net_pnl": np.array(net_pnls, dtype=float),
            "bars_held": np.full(n, bars_held, dtype=np.int64),
            "exit_reason": ["signal"] * n,
        }
    )


# ---------------------------------------------------------------------------
# Test 1 — flat equity → cagr=0, mdd=0, sharpe=nan
# ---------------------------------------------------------------------------

def test_flat_equity_returns_nan_sharpe_zero_cagr() -> None:
    """1-year flat curve at 100 should give cagr≈0, mdd=0, sharpe=nan (0/0)."""
    # Use daily freq so 1 year is manageable; pass bars_per_year=365
    values = [100.0] * 366  # ~1 year of daily bars
    eq = _make_equity(values, freq="1D")
    m = compute_metrics(eq, _empty_trades(), bars_per_year=365)

    assert abs(m["cagr"]) < 1e-9, f"cagr should be 0, got {m['cagr']}"
    assert m["mdd"] == pytest.approx(0.0, abs=1e-12)
    assert math.isnan(m["sharpe"]), "sharpe should be nan for flat curve"


# ---------------------------------------------------------------------------
# Test 2 — known MDD from manual curve
# ---------------------------------------------------------------------------

def test_known_mdd() -> None:
    """Curve 100→120→60→90: peak=120, trough=60 → mdd = (120-60)/120 = 0.5."""
    eq = _make_equity([100.0, 120.0, 60.0, 90.0])
    m = compute_metrics(eq, _empty_trades())
    assert m["mdd"] == pytest.approx(0.5, rel=1e-9)


# ---------------------------------------------------------------------------
# Test 3 — CAGR doubling in one year
# ---------------------------------------------------------------------------

def test_cagr_doubling_in_one_year() -> None:
    """100 → 200 over exactly 1 year (daily bars) → cagr ≈ 1.0."""
    n = 366  # start + 365 steps ≈ 1 year
    values = np.linspace(100.0, 200.0, n).tolist()
    eq = _make_equity(values, freq="1D")
    m = compute_metrics(eq, _empty_trades(), bars_per_year=365)
    # elapsed ≈ 365 days; (200/100)^(1/1) - 1 = 1.0
    assert m["cagr"] == pytest.approx(1.0, rel=0.01)


# ---------------------------------------------------------------------------
# Test 4 — profit factor
# ---------------------------------------------------------------------------

def test_profit_factor() -> None:
    """PnLs [+10, +20, -5, -10]: wins=30, losses=15 → PF = 2.0."""
    trades = _make_trades([10.0, 20.0, -5.0, -10.0])
    eq = _make_equity([100.0, 110.0, 130.0, 125.0, 115.0])
    m = compute_metrics(eq, trades)
    assert m["profit_factor"] == pytest.approx(2.0, rel=1e-9)


# ---------------------------------------------------------------------------
# Test 5 — max consecutive losing streak
# ---------------------------------------------------------------------------

def test_max_consec_loss() -> None:
    """PnLs [+1, -1, -1, -1, +1, -1, -1] → longest losing streak = 3."""
    trades = _make_trades([1.0, -1.0, -1.0, -1.0, 1.0, -1.0, -1.0])
    eq = _make_equity([100.0] * 8)
    m = compute_metrics(eq, trades)
    assert m["max_consec_loss"] == pytest.approx(3.0)


# ---------------------------------------------------------------------------
# Test 6 — win_rate and avg win/loss
# ---------------------------------------------------------------------------

def test_win_rate_and_avg_pnls() -> None:
    """PnLs [+10, +20, -5, -15]: win_rate=0.5, avg_win=15, avg_loss=-10."""
    trades = _make_trades([10.0, 20.0, -5.0, -15.0])
    eq = _make_equity([100.0, 110.0, 130.0, 125.0, 110.0])
    m = compute_metrics(eq, trades)
    assert m["win_rate"] == pytest.approx(0.5)
    assert m["avg_win"] == pytest.approx(15.0)
    assert m["avg_loss"] == pytest.approx(-10.0)


# ---------------------------------------------------------------------------
# Test 7 (bonus) — empty trades does not raise; returns documented nan/0
# ---------------------------------------------------------------------------

def test_empty_trades_safe() -> None:
    """Empty trade log must return 0-trade defaults without raising."""
    eq = _make_equity([100.0, 105.0, 110.0])
    m = compute_metrics(eq, _empty_trades())

    assert m["total_trades"] == pytest.approx(0.0)
    assert math.isnan(m["win_rate"])
    assert math.isnan(m["avg_win"])
    assert math.isnan(m["avg_loss"])
    assert math.isnan(m["profit_factor"])
    assert m["max_consec_loss"] == pytest.approx(0.0)
    assert m["exposure"] == pytest.approx(0.0)
    assert m["turnover"] == pytest.approx(0.0)
    # equity-based metrics that are computable on any span
    # (mdd is always defined given >=2 bars; cagr may be nan on tiny spans)
    assert not math.isnan(m["mdd"])


# ---------------------------------------------------------------------------
# Test 8 — Sharpe sign check (trending-up curve should have positive Sharpe)
# ---------------------------------------------------------------------------

def test_sharpe_positive_for_trending_up_curve() -> None:
    """A monotonically increasing equity curve should produce positive Sharpe."""
    values = [100.0 * (1.001 ** i) for i in range(500)]
    eq = _make_equity(values)
    m = compute_metrics(eq, _empty_trades())
    assert m["sharpe"] > 0.0, f"expected positive sharpe, got {m['sharpe']}"


# ---------------------------------------------------------------------------
# Test 9 — VaR and CVaR are non-negative
# ---------------------------------------------------------------------------

def test_var_cvar_non_negative() -> None:
    """VaR_95 and CVaR_95 must be returned as positive fractions."""
    rng = np.random.default_rng(42)
    # Simulated equity with some downside
    returns = 1.0 + rng.normal(0.0001, 0.01, 300)
    values = [100.0]
    for r in returns:
        values.append(values[-1] * r)
    eq = _make_equity(values)
    m = compute_metrics(eq, _empty_trades())
    assert m["var_95"] >= 0.0
    assert m["cvar_95"] >= 0.0
    # CVaR should be >= VaR (average of worst tail >= boundary)
    assert m["cvar_95"] >= m["var_95"] - 1e-12


# ---------------------------------------------------------------------------
# Test 10 — single-bar equity does not raise
# ---------------------------------------------------------------------------

def test_single_bar_equity_returns_nan_not_raise() -> None:
    """A 1-bar equity curve is degenerate; must return without raising."""
    eq = _make_equity([100.0])
    m = compute_metrics(eq, _empty_trades())
    # Everything should be nan/0 — just check no exception and key presence
    required_keys = {
        "cagr", "mdd", "sharpe", "sortino", "calmar", "profit_factor",
        "max_consec_loss", "var_95", "cvar_95", "total_trades",
        "win_rate", "avg_win", "avg_loss", "exposure", "turnover",
    }
    assert required_keys == set(m.keys())
