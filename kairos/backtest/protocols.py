"""Backtest layer interface contracts. **FROZEN — do not modify without Opus review.**

All engine adapters, strategies, cost models, and risk rules implementations must
conform to these protocols. Sonnet delegations must treat this file as the
single source of truth. If the contract is insufficient for a real implementation,
escalate to Opus instead of extending the protocol ad-hoc.

Design invariants (violation = automatic rejection in Opus review):

1. **No look-ahead**: at timestamp T, a strategy may read bars whose close_time
   ≤ T. The execution adapter must enter positions at the open of bar T+1.
2. **Risk is wrapper-level, not engine-level**: engines do not apply daily/weekly/
   monthly loss limits, MDD halts, or fractional-Kelly sizing. Those live in
   `kairos.backtest.risk_simulator`, which runs *on top of* an Engine's raw PnL.
   If an engine embeds a risk rule internally, the dual-engine cross-check
   becomes meaningless.
3. **Decimal quantization at one site**: price/qty rounding to Binance
   tickSize/stepSize happens in `kairos.backtest.quantize` only, using Decimal.
4. **Pure outputs**: `BacktestResult` is fully picklable (no open file handles,
   no connection objects).
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Protocol

import pandas as pd

# ---- Timestamp convention --------------------------------------------------
# All DatetimeIndex values are UTC-aware pandas Timestamps.
# All integer timestamp columns are UTC milliseconds (int64), matching Binance.

Interval = Literal["1m", "15m"]
EngineName = Literal["vectorbt", "backtrader"]
Side = Literal[-1, 0, 1]  # -1 short, 0 flat, +1 long


# ---- Input container -------------------------------------------------------

@dataclass(frozen=True, slots=True)
class BarsData:
    """All input market data for a single symbol/interval backtest window.

    The ohlcv frame is the primary time axis. `funding` and `metrics`, when
    present, are aligned by forward-fill at strategy time (strategies do this
    themselves via `.reindex(ohlcv.index).ffill()` — the framework does NOT
    pre-join, because different strategies want different alignment policies).
    """
    ohlcv: pd.DataFrame
    """DatetimeIndex (UTC). Columns: open, high, low, close, volume, quote_volume,
    taker_buy_volume, taker_buy_quote_volume, trade_count. All float64 except
    trade_count (int64). Index monotonic increasing, no duplicates, gap-free
    within the declared window."""

    symbol: str
    interval: Interval

    funding: pd.DataFrame | None = None
    """DatetimeIndex (UTC, 8h spacing typically). Columns: funding_rate,
    funding_interval_hours."""

    metrics: pd.DataFrame | None = None
    """DatetimeIndex (UTC, 5m spacing). Columns: sum_open_interest,
    sum_open_interest_value, count_toptrader_long_short_ratio,
    sum_toptrader_long_short_ratio, count_long_short_ratio,
    sum_taker_long_short_vol_ratio."""


# ---- Signal container ------------------------------------------------------

@dataclass(frozen=True, slots=True)
class SignalFrame:
    """Per-bar strategy output. Index must match BarsData.ohlcv.index 1-to-1.

    side:       -1, 0, or +1. Interpreted as target position on the next bar
                open. Changes from prior bar produce (possibly partial) trades.
    size_hint:  relative size suggestion in [0.0, 1.0]. Final sizing is applied
                by RiskSimulator (fractional-Kelly), not the engine.
    stop:       protective stop price for this bar's position. NaN = none.
    target:     take-profit price. NaN = none.

    Engines must treat stop/target as intra-bar triggers: if the bar's
    [low, high] touches the level, exit at that level within the same bar.
    """
    df: pd.DataFrame
    """DatetimeIndex (UTC). Columns with dtypes:
        side: int8 in {-1, 0, 1}
        size_hint: float64 in [0, 1]
        stop: float64 (NaN allowed)
        target: float64 (NaN allowed)
    """


# ---- Cost model ------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class CostModel:
    """Transaction costs applied by both engine adapters identically.

    Slippage model:
        effective_slippage_bps =
            slippage_bps_base + slippage_vol_coef * (daily_ATR / close)
        where daily_ATR is 14-period ATR resampled to 1D, forward-filled.

    Funding is NOT a parameter here — the RiskSimulator/Engine applies the
    actual per-8h funding from BarsData.funding to open positions.
    """
    taker_fee_bps: float = 4.5        # 0.045% Binance USDⓈ-M taker
    slippage_bps_base: float = 1.0    # 1 bp floor on any market order
    slippage_vol_coef: float = 0.5    # bps per unit of daily ATR/close ratio


# ---- Risk rules (wrapper-level; not passed to engines) ---------------------

@dataclass(frozen=True, slots=True)
class RiskRules:
    """Applied by kairos.backtest.risk_simulator *outside* any Engine.

    When a halt fires, RiskSimulator rewrites subsequent signals to 0 for the
    halt window, then resumes from the natural signal stream.

    eod_flatten: if True, at eod_time_utc each trading day the simulator
    forces flat regardless of strategy signal. Positions reopen on next UTC
    day's first bar if strategy still signals.
    """
    daily_loss_limit_pct: float = 3.0
    weekly_loss_limit_pct: float = 8.0
    monthly_loss_limit_pct: float = 15.0
    mdd_halt_pct: float = 20.0
    kelly_fraction: float = 0.25
    eod_flatten: bool = True
    eod_time_utc: str = "23:55"


# ---- Symbol spec (quantization) --------------------------------------------

@dataclass(frozen=True, slots=True)
class SymbolSpec:
    """Exchange-defined quantization. Loaded from Binance exchangeInfo (cached).

    Engines must round order prices DOWN to nearest `tick_size` for buys and
    UP for sells (never through), and round quantity to `step_size`.
    Resulting notional must be ≥ min_notional; else the order is dropped.
    """
    symbol: str
    tick_size: Decimal
    step_size: Decimal
    min_qty: Decimal
    min_notional: Decimal


# ---- Output container ------------------------------------------------------

@dataclass(frozen=True, slots=True)
class BacktestResult:
    """Engine output. Must be fully picklable for SQLite persistence."""

    equity_curve: pd.Series
    """Value = total equity in quote currency, DatetimeIndex (UTC) at bar cadence."""

    trades: pd.DataFrame
    """One row per closed trade. Columns with dtypes:
        entry_time: datetime64[ns, UTC]
        exit_time: datetime64[ns, UTC]
        side: int8  (+1 long, -1 short)
        qty: float64
        entry_price: float64
        exit_price: float64
        gross_pnl: float64   # (exit - entry) * qty * side, no costs
        fees: float64        # entry_fee + exit_fee (positive = cost)
        slippage: float64    # positive = cost
        funding_cost: float64  # positive = cost to long during positive funding
        net_pnl: float64     # gross_pnl - fees - slippage - funding_cost
        bars_held: int64
        exit_reason: string  # "signal" | "stop" | "target" | "eod_flatten" | "risk_halt"
    """

    metrics: Mapping[str, float]
    """Standard keys (see kairos.backtest.metrics):
        cagr, mdd, sharpe, sortino, calmar, profit_factor,
        max_consec_loss, var_95, cvar_95, total_trades, win_rate,
        avg_win, avg_loss, exposure, turnover.
    Engines MAY add engine-specific diagnostics under keys prefixed "_engine__".
    """

    engine_name: EngineName
    params: Mapping[str, object]
    """The exact param dict passed to strategy + engine, for reproducibility."""


# ---- Strategy Protocol -----------------------------------------------------

class Strategy(Protocol):
    """Pure signal generator. Must NOT:
        - look at future bars (timestamp > current bar close_time)
        - apply position sizing beyond size_hint
        - apply risk rules (that's RiskSimulator's job)
        - import any engine (vectorbt/backtrader) module

    Must be stateless across calls — `generate_signals` is deterministic in
    (data, params). Any internal caching is fine as long as the output is
    purely a function of the inputs.
    """
    name: str
    default_params: Mapping[str, object]

    def generate_signals(
        self,
        data: BarsData,
        params: Mapping[str, object],
    ) -> SignalFrame: ...


# ---- Engine Protocol -------------------------------------------------------

class Engine(Protocol):
    """Adapter over a concrete backtest library.

    Invariants enforced by Opus review:
      - run() takes signals via strategy.generate_signals(data, params) and
        treats them as next-bar-open orders.
      - run() applies CostModel uniformly; slippage and fees must be visible
        in result.trades per-row.
      - run() does NOT consult RiskRules; that's the wrapper's job.
      - run() must be deterministic given same inputs (seed any RNGs).
    """
    name: EngineName

    def run(
        self,
        strategy: Strategy,
        data: BarsData,
        params: Mapping[str, object],
        costs: CostModel,
        symbol_spec: SymbolSpec,
        initial_cash: Decimal,
    ) -> BacktestResult: ...


__all__ = [
    "BacktestResult",
    "BarsData",
    "CostModel",
    "Engine",
    "EngineName",
    "Interval",
    "RiskRules",
    "Side",
    "SignalFrame",
    "Strategy",
    "SymbolSpec",
]
