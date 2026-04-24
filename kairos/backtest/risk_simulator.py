"""Portfolio-level risk simulator — wraps a PortfolioEngine with RiskRules.

Replaces the single-symbol RiskSimulator. Lives *above* the PortfolioEngine
layer: the engine is a pure execution adapter; risk lives here so the
dual-engine cross-check (vectorbt vs backtrader) stays meaningful.

Two-pass design (see docs/phase5/PORTFOLIO.md §"Portfolio-level RiskSimulator"):

1. Run the wrapped PortfolioEngine once to obtain the raw PortfolioResult
   (equity curve + trades per symbol, Kelly-scaled trade sizes).
2. Walk the **portfolio** equity curve bar by bar. Maintain rolling daily /
   weekly / monthly P&L windows anchored at each window's first bar. When a
   window's loss breaches its limit the simulator enters a halt state that
   lasts until the window resets (next UTC day / ISO week / calendar month).
3. MDD halt: drawdown from all-time portfolio peak; on breach, halt for the
   rest of the run (no recovery).
4. EOD flatten: from `eod_time_utc` each UTC day through the last bar before
   the next UTC day, treat as halted. This matches the single-symbol
   behavior — positions reopen on the next day's first bar if signals
   persist.
5. Halt application rewrites the raw result in place:
   - per_symbol_equity is held flat at the halt-entry value
   - trades whose entry_time is inside any halt window are dropped
   - trades whose exit_time falls inside a halt window are force-closed and
     stamped exit_reason = "risk_halt" (the engine already computed their
     net_pnl up to the original exit; we do NOT retro-model a different
     fill price because the raw engine's cost accounting is the source of
     truth for executed trades — we only *cancel* trades that would have
     opened mid-halt).
6. Kelly sizing is applied post-hoc: trade `qty` (and therefore `gross_pnl`,
   `fees`, `slippage`, `funding_cost`, `net_pnl`) are all scaled by
   `kelly_fraction`. This matches the single-symbol approach's spirit (do
   not re-run the engine with rescaled sizes; scale the output). Position
   limits per symbol are applied by the engine's own `MAX_LEVERAGE` clip
   before Kelly, so kelly_fraction * clipped_size is always well-formed.

Portfolio metrics are recomputed on the halt-adjusted equity curve via
`kairos.backtest.metrics.compute_metrics`.
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

import numpy as np
import pandas as pd

from kairos.backtest.metrics import compute_metrics
from kairos.backtest.protocols import (
    CostModel,
    PortfolioBars,
    PortfolioEngine,
    PortfolioResult,
    RiskRules,
    Strategy,
    SymbolSpec,
)

log = logging.getLogger("kairos.backtest.risk_simulator")


# ---------------------------------------------------------------------------
# Halt window type — kept as a public name because kairos.backtest.stress
# imports it. A halt spans [start, end] inclusive in bar timestamps.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HaltWindow:
    """Contiguous span [start, end] (both inclusive) where trading is halted."""

    start: pd.Timestamp
    end: pd.Timestamp
    reason: str  # "daily" | "weekly" | "monthly" | "mdd" | "eod"


# ---------------------------------------------------------------------------
# Halt detection — public because stress.py consumes it directly on raw
# engine output to report "would have fired" halts.
# ---------------------------------------------------------------------------

def _group_keys(index: pd.DatetimeIndex, freq: str) -> pd.DatetimeIndex:
    """Return a per-bar anchor timestamp for the given calendar grouping."""
    if freq == "D":
        return index.floor("D")
    if freq == "W-MON":
        # WHY manual weekday offset: pandas W-MON via to_period drops tz info;
        # we keep tz-aware UTC anchors so downstream comparisons stay valid.
        day_of_week = index.weekday  # Monday=0
        return index.floor("D") - pd.to_timedelta(day_of_week, unit="D")
    if freq == "MS":
        return pd.DatetimeIndex(
            [
                pd.Timestamp(year=t.year, month=t.month, day=1, tz=index.tz)
                for t in index
            ]
        )
    raise ValueError(f"unknown group freq: {freq}")


def _detect_halts(
    equity: pd.Series,
    rules: RiskRules,
    initial_cash: float,  # noqa: ARG001 — kept for stress.py signature compat
) -> list[HaltWindow]:
    """Scan a portfolio equity curve and return halt windows.

    Calendar-group halts (daily/weekly/monthly): each group's anchor equity
    is the group's first bar. A breach extends the halt from first breach
    through the last bar of the group (inclusive).

    MDD halt: first drawdown ≤ -mdd_halt_pct/100 against the all-time peak
    extends through the final bar.
    """
    halts: list[HaltWindow] = []
    if len(equity) == 0:
        return halts

    for group_freq, limit_pct, reason in (
        ("D", rules.daily_loss_limit_pct, "daily"),
        ("W-MON", rules.weekly_loss_limit_pct, "weekly"),
        ("MS", rules.monthly_loss_limit_pct, "monthly"),
    ):
        # WHY the >= 100 guard: test fixtures use 100.0 to disable a limit;
        # treat as disabled rather than trying to trigger on -100% drawdown.
        if limit_pct <= 0 or limit_pct >= 100:
            continue
        threshold = 1.0 - limit_pct / 100.0
        key_series = pd.Series(_group_keys(equity.index, group_freq), index=equity.index)

        for _group_anchor, group_idx in equity.groupby(key_series).groups.items():
            if len(group_idx) == 0:
                continue
            grp_equity = equity.loc[group_idx]
            anchor = float(grp_equity.iloc[0])
            floor = anchor * threshold
            breach_mask = grp_equity < floor
            if not breach_mask.any():
                continue
            first_breach = grp_equity.index[breach_mask][0]
            group_end = grp_equity.index[-1]
            halts.append(HaltWindow(first_breach, group_end, reason))

    if 0 < rules.mdd_halt_pct < 100:
        peak = equity.cummax()
        dd = (equity - peak) / peak
        mdd_limit = -rules.mdd_halt_pct / 100.0
        breach = dd <= mdd_limit
        if breach.any():
            first = equity.index[breach][0]
            halts.append(HaltWindow(first, equity.index[-1], "mdd"))

    return halts


def _eod_halts(index: pd.DatetimeIndex, eod_time_utc: str) -> list[HaltWindow]:
    """Halt from `eod_time_utc` each UTC day through the day's final bar.

    The next UTC day's first bar reopens trading. Matches the spec: "at
    `eod_time_utc` daily, all symbols flat; positions reopen on next day's
    first bar if signals still active."
    """
    if len(index) == 0:
        return []
    hh, mm = (int(x) for x in eod_time_utc.split(":"))
    cutoff_mask = (index.hour > hh) | ((index.hour == hh) & (index.minute >= mm))
    if not cutoff_mask.any():
        return []

    # Build [start, end] per UTC day: start = first bar on/after eod, end =
    # last bar before next UTC day's 00:00.
    day_key = index.floor("D")
    halts: list[HaltWindow] = []
    mask = np.asarray(cutoff_mask)
    for day in np.unique(day_key[mask]):
        day_mask = (day_key == day) & mask
        day_bars = index[day_mask]
        if len(day_bars) == 0:
            continue
        halts.append(HaltWindow(day_bars[0], day_bars[-1], "eod"))
    return halts


def _bar_in_any(ts: pd.Timestamp, halts: list[HaltWindow]) -> bool:
    for h in halts:
        if h.start <= ts <= h.end:
            return True
    return False


# ---------------------------------------------------------------------------
# Halt application to a raw PortfolioResult
# ---------------------------------------------------------------------------

def _apply_halts_to_equity(equity: pd.Series, halts: list[HaltWindow]) -> pd.Series:
    """Hold equity flat inside halt windows at the halt-entry value.

    Inside a halt window, no new P&L accrues on the portfolio curve. Between
    windows, the bar-over-bar delta from the raw curve is preserved.
    """
    if not halts:
        return equity.copy()

    values = equity.to_numpy(copy=True)
    idx = equity.index
    # Build a boolean mask of halted bars for vectorised application.
    halted = np.zeros(len(values), dtype=bool)
    for h in halts:
        start_pos = idx.searchsorted(h.start, side="left")
        end_pos = idx.searchsorted(h.end, side="right")
        halted[start_pos:end_pos] = True

    out = values.copy()
    for i in range(1, len(out)):
        if halted[i]:
            # Hold at previous bar's (post-halt-adjusted) value — zero P&L.
            out[i] = out[i - 1]
        else:
            # Preserve raw delta across the boundary.
            raw_delta = values[i] - values[i - 1]
            out[i] = out[i - 1] + raw_delta

    return pd.Series(out, index=idx, name=equity.name)


def _filter_and_mark_trades(
    trades: pd.DataFrame,
    halts: list[HaltWindow],
) -> pd.DataFrame:
    """Drop trades entered inside halts; mark trades exiting inside halts."""
    if trades.empty or not halts:
        return trades.copy()

    keep_rows: list[int] = []
    new_reason = trades["exit_reason"].astype(object).copy()
    for i, row in trades.reset_index(drop=True).iterrows():
        entry_halted = _bar_in_any(row["entry_time"], halts)
        if entry_halted:
            continue
        if _bar_in_any(row["exit_time"], halts):
            new_reason.iloc[i] = "risk_halt"
        keep_rows.append(i)

    out = trades.reset_index(drop=True).iloc[keep_rows].copy()
    out["exit_reason"] = new_reason.iloc[keep_rows].values
    # WHY preserve string dtype: the contract documents exit_reason as
    # pandas string; cast back if the original frame used it.
    if pd.api.types.is_string_dtype(trades["exit_reason"]):
        out["exit_reason"] = out["exit_reason"].astype("string")
    return out.reset_index(drop=True)


def _scale_trades_by_kelly(trades: pd.DataFrame, kelly_fraction: float) -> pd.DataFrame:
    """Scale trade size and its derived money columns by kelly_fraction.

    Kelly is applied *post-engine* — the single-symbol sim did the same: it
    is cheaper than a second engine run and arithmetically equivalent for
    linear cost models.
    """
    if kelly_fraction == 1.0 or trades.empty:
        return trades.copy()
    out = trades.copy()
    k = float(kelly_fraction)
    for col in ("qty", "gross_pnl", "fees", "slippage", "funding_cost", "net_pnl"):
        if col in out.columns:
            out[col] = out[col] * k
    return out


def _scale_equity_by_kelly(
    equity: pd.Series,
    kelly_fraction: float,
    initial_cash: float,
) -> pd.Series:
    """Scale the P&L portion of the equity curve by kelly_fraction.

    The base (initial_cash) is not levered; only P&L above/below it scales.
    """
    if kelly_fraction == 1.0:
        return equity.copy()
    pnl = equity - initial_cash
    return initial_cash + pnl * float(kelly_fraction)


# ---------------------------------------------------------------------------
# PortfolioRiskSimulator
# ---------------------------------------------------------------------------

class PortfolioRiskSimulator:
    """Wraps a PortfolioEngine with RiskRules enforcement.

    Constructor: `PortfolioRiskSimulator(engine, rules)`.
    Public method `run` has the same signature as PortfolioEngine.run and
    returns a PortfolioResult with metrics recomputed on the halt-adjusted
    equity curve.
    """

    def __init__(self, engine: PortfolioEngine, rules: RiskRules) -> None:
        self.engine = engine
        self.rules = rules

    def run(
        self,
        strategies: dict[str, Strategy],
        portfolio: PortfolioBars,
        params: dict[str, Mapping[str, object]],
        costs: CostModel,
        symbol_specs: dict[str, SymbolSpec],
        initial_cash: Decimal,
    ) -> PortfolioResult:
        raw = self.engine.run(
            strategies=strategies,
            portfolio=portfolio,
            params=params,
            costs=costs,
            symbol_specs=symbol_specs,
            initial_cash=initial_cash,
        )
        initial_cash_f = float(initial_cash)

        # Apply Kelly first (scales equity P&L and trade sizes uniformly),
        # then detect halts on the Kelly-scaled curve. Rationale: halts
        # respond to *effective* portfolio losses; running halt detection
        # on an unscaled curve would trigger too aggressively when Kelly<1.
        kelly_equity = _scale_equity_by_kelly(
            raw.equity_curve, self.rules.kelly_fraction, initial_cash_f
        )
        kelly_per_symbol = {
            s: _scale_equity_by_kelly(
                eq,
                self.rules.kelly_fraction,
                initial_cash_f * float(portfolio.weights.get(s, 0.0)),
            )
            for s, eq in raw.per_symbol_equity.items()
        }
        kelly_trades = _scale_trades_by_kelly(raw.trades, self.rules.kelly_fraction)

        halts = _detect_halts(kelly_equity, self.rules, initial_cash_f)
        if self.rules.eod_flatten:
            halts.extend(_eod_halts(kelly_equity.index, self.rules.eod_time_utc))

        if halts:
            log.info(
                "portfolio_risk_simulator: %d halt window(s) — reasons=%s",
                len(halts),
                sorted({h.reason for h in halts}),
            )

        adj_equity = _apply_halts_to_equity(kelly_equity, halts)
        adj_per_symbol = {
            s: _apply_halts_to_equity(eq, halts) for s, eq in kelly_per_symbol.items()
        }
        adj_trades = _filter_and_mark_trades(kelly_trades, halts)

        # Recompute metrics on the halt-adjusted curve. Per-symbol metrics
        # are recomputed using the per-symbol sub-slice of trades so that
        # win rate / turnover reflect the post-halt reality.
        metrics = compute_metrics(adj_equity, adj_trades)
        per_symbol_metrics: dict[str, Mapping[str, float]] = {}
        for sym, eq in adj_per_symbol.items():
            sym_trades = (
                adj_trades[adj_trades["symbol"] == sym]
                if "symbol" in adj_trades.columns
                else adj_trades.iloc[0:0]
            )
            per_symbol_metrics[sym] = compute_metrics(eq, sym_trades)

        return PortfolioResult(
            equity_curve=adj_equity,
            per_symbol_equity=adj_per_symbol,
            trades=adj_trades,
            metrics=metrics,
            per_symbol_metrics=per_symbol_metrics,
            engine_name=raw.engine_name,
            params=raw.params,
        )


# ---------------------------------------------------------------------------
# Backward-compat alias. WHY kept: kairos.backtest.stress and
# kairos.dashboard.api.jobs still reference `RiskSimulator` by that name.
# These modules are owned by other phases and will migrate; exposing the
# alias prevents gratuitous import breakage while Phase 5 lands.
# ---------------------------------------------------------------------------

RiskSimulator = PortfolioRiskSimulator


__all__ = [
    "HaltWindow",
    "PortfolioRiskSimulator",
    "RiskSimulator",
]
