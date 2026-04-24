"""Performance metrics calculator for Kairos backtests.

Entry point: compute_metrics(equity_curve, trades, bars_per_year, risk_free_rate)
Returns exactly the key-set documented on BacktestResult.metrics in protocols.py.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


def compute_metrics(
    equity_curve: pd.Series,
    trades: pd.DataFrame,
    bars_per_year: int = 525_600,
    risk_free_rate: float = 0.0,
) -> dict[str, float]:
    """Compute standard performance metrics from an equity curve and trade log.

    Parameters
    ----------
    equity_curve:
        DatetimeIndex (UTC), values = equity in quote currency.
    trades:
        DataFrame matching BacktestResult.trades schema.  May be empty.
    bars_per_year:
        Annualisation factor.  Caller passes 35_040 for 15m bars, 365 for daily.
        Default 525_600 is correct for 1-minute bars (365 * 24 * 60).
    risk_free_rate:
        Annualised risk-free rate (e.g. 0.05 for 5 %).  Converted to per-bar
        before use as: rf_per_bar = (1 + risk_free_rate) ** (1 / bars_per_year) - 1.
    """
    _nan = float("nan")

    # ------------------------------------------------------------------ equity
    eq = equity_curve.dropna()
    n_bars = len(eq)

    if n_bars < 2:
        initial = float(eq.iloc[0]) if n_bars == 1 else _nan
        return _all_nan(initial, trades, bars_per_year)

    initial = float(eq.iloc[0])
    final = float(eq.iloc[-1])

    # elapsed years from index span
    t0 = eq.index[0]
    t1 = eq.index[-1]
    elapsed_seconds = (t1 - t0).total_seconds()
    seconds_per_year = 365.25 * 24 * 3600
    elapsed_years = elapsed_seconds / seconds_per_year

    # ------------------------------------------------------------------ CAGR
    # `final <= 0` guard: a strategy that blew through all capital leaves
    # equity <= 0, and `(negative) ** (fractional)` returns a complex number
    # which then explodes `math.isnan(cagr)` downstream. Treat total-ruin as
    # NaN CAGR.
    if elapsed_years <= 0 or initial <= 0 or final <= 0:
        cagr = _nan
    else:
        try:
            cagr = (final / initial) ** (1.0 / elapsed_years) - 1.0
        except (OverflowError, ZeroDivisionError):
            # elapsed_years is tiny relative to exponent (e.g. a 5-bar test);
            # return nan rather than raising
            cagr = _nan

    # ------------------------------------------------------------------ MDD
    running_max = eq.cummax()
    drawdowns = (running_max - eq) / running_max
    mdd = float(drawdowns.max()) if len(drawdowns) else _nan
    if math.isnan(mdd):
        mdd = _nan

    # ------------------------------------------------------------------ per-bar log returns
    log_ret = np.log(eq.values[1:] / eq.values[:-1])

    # ------------------------------------------------------------------ Sharpe / Sortino
    rf_per_bar = (1.0 + risk_free_rate) ** (1.0 / bars_per_year) - 1.0
    # convert rf to log scale for consistency with log returns
    rf_log = math.log(1.0 + rf_per_bar)

    excess = log_ret - rf_log
    std_all = float(np.std(excess, ddof=1)) if len(excess) >= 2 else _nan

    if std_all == 0.0 or math.isnan(std_all):
        sharpe = _nan
    else:
        sharpe = float(np.mean(excess) / std_all * math.sqrt(bars_per_year))

    downside = excess[excess < 0.0]
    if len(downside) < 2:
        sortino = _nan
    else:
        std_down = float(np.std(downside, ddof=1))
        if std_down == 0.0:
            sortino = _nan
        else:
            sortino = float(np.mean(excess) / std_down * math.sqrt(bars_per_year))

    # ------------------------------------------------------------------ Calmar
    if math.isnan(cagr) or math.isnan(mdd) or mdd == 0.0:
        calmar = _nan
    else:
        calmar = cagr / mdd

    # ------------------------------------------------------------------ VaR / CVaR (95 %)
    if len(log_ret) == 0:
        var_95 = _nan
        cvar_95 = _nan
    else:
        p5 = float(np.percentile(log_ret, 5))
        var_95 = -p5 if p5 < 0.0 else 0.0
        tail = log_ret[log_ret <= p5]
        cvar_95 = float(-np.mean(tail)) if len(tail) > 0 else _nan

    # ------------------------------------------------------------------ trade metrics
    has_trades = len(trades) > 0

    total_trades = float(len(trades))

    if not has_trades:
        return {
            "cagr": cagr,
            "mdd": mdd,
            "sharpe": sharpe,
            "sortino": sortino,
            "calmar": calmar,
            "profit_factor": _nan,
            "max_consec_loss": 0.0,
            "var_95": var_95,
            "cvar_95": cvar_95,
            "total_trades": 0.0,
            "win_rate": _nan,
            "avg_win": _nan,
            "avg_loss": _nan,
            "exposure": 0.0,
            "turnover": 0.0,
        }

    pnls = trades["net_pnl"].values.astype(float)
    wins = pnls[pnls > 0.0]
    losses = pnls[pnls <= 0.0]

    win_rate = float(len(wins)) / float(len(pnls))

    avg_win = float(np.mean(wins)) if len(wins) > 0 else _nan
    avg_loss = float(np.mean(losses)) if len(losses) > 0 else _nan

    gross_wins = float(np.sum(wins)) if len(wins) > 0 else 0.0
    gross_loss = float(np.sum(losses)) if len(losses) > 0 else 0.0

    if gross_loss == 0.0:
        # no losing trades at all
        profit_factor = _nan if gross_wins == 0.0 else float("inf")
    else:
        profit_factor = gross_wins / abs(gross_loss)

    # max consecutive losing streak
    max_consec_loss = _max_consec_losing(pnls)

    # ------------------------------------------------------------------ Exposure
    total_bars = n_bars
    if "bars_held" in trades.columns and total_bars > 0:
        exposure = float(min(trades["bars_held"].sum() / total_bars, 1.0))
    else:
        exposure = _nan

    # ------------------------------------------------------------------ Turnover
    if elapsed_years > 0 and initial > 0 and "qty" in trades.columns:
        entry_notional = (trades["qty"].abs() * trades["entry_price"]).sum()
        exit_notional = (trades["qty"].abs() * trades["exit_price"]).sum()
        total_notional = float(entry_notional + exit_notional)
        turnover = total_notional / initial / elapsed_years
    else:
        turnover = _nan

    return {
        "cagr": cagr,
        "mdd": mdd,
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": calmar,
        "profit_factor": profit_factor,
        "max_consec_loss": float(max_consec_loss),
        "var_95": var_95,
        "cvar_95": cvar_95,
        "total_trades": total_trades,
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "exposure": exposure,
        "turnover": turnover,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _max_consec_losing(pnls: np.ndarray) -> int:
    """Return the length of the longest run of non-positive pnls."""
    max_run = 0
    current = 0
    for p in pnls:
        if p <= 0.0:
            current += 1
            if current > max_run:
                max_run = current
        else:
            current = 0
    return max_run


def _all_nan(initial: float, trades: pd.DataFrame, bars_per_year: int) -> dict[str, float]:
    """Return a fully-NaN metrics dict for degenerate equity curves."""
    _nan = float("nan")
    has_trades = len(trades) > 0
    total_trades = float(len(trades))
    return {
        "cagr": _nan,
        "mdd": _nan,
        "sharpe": _nan,
        "sortino": _nan,
        "calmar": _nan,
        "profit_factor": _nan if not has_trades else _nan,
        "max_consec_loss": 0.0,
        "var_95": _nan,
        "cvar_95": _nan,
        "total_trades": total_trades,
        "win_rate": _nan,
        "avg_win": _nan,
        "avg_loss": _nan,
        "exposure": 0.0,
        "turnover": 0.0,
    }


__all__ = ["compute_metrics"]
