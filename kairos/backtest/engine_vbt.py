"""VectorbtEngine — backtest adapter using vectorbt 1.0.

Design invariants (per protocols.py §3):
  - No look-ahead: signals at bar T execute at bar T+1's open via next-open price series.
  - No risk rules embedded: fees/slippage only; no daily-loss halt or Kelly sizing.
  - Deterministic: seed=0 passed to vbt (no RNG used in practice for from_signals).
  - Pure outputs: BacktestResult is fully picklable.

Exit-reason detection strategy (vbt 1.0 trade_dt has no exit_reason field):
  We compare the exit_idx of each trade against the exits/short_exits boolean arrays.
  - If the exit signal (exits or short_exits) is True at exit_idx → "signal".
  - Else if sl_stop was passed and exit price ≈ SL level → "stop".
  - Else if tp_stop was passed and exit price ≈ TP level → "target".
  - Fallback → "signal".
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from decimal import Decimal
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
import vectorbt as vbt

from kairos.backtest.costs import (
    daily_atr_per_close,
    funding_cost_for_position,
    slippage_bps,
)
from kairos.backtest.metrics import compute_metrics
from kairos.backtest.protocols import (
    BacktestResult,
    BarsData,
    CostModel,
    EngineName,
    SignalFrame,
    Strategy,
    SymbolSpec,
)

if TYPE_CHECKING:
    pass

_log = logging.getLogger("kairos.backtest.engine_vbt")

_BARS_PER_YEAR: dict[str, int] = {
    "1m": 525_600,
    "15m": 35_040,
}

# Tolerance for price-level matching when detecting stop/target exits
_PRICE_MATCH_TOL = 0.005  # 0.5% — generous to handle fill-price rounding


class VectorbtEngine:
    """Engine adapter wrapping vectorbt 1.0 Portfolio.from_signals."""

    name: EngineName = "vectorbt"

    def run(
        self,
        strategy: Strategy,
        data: BarsData,
        params: Mapping[str, object],
        costs: CostModel,
        symbol_spec: SymbolSpec,
        initial_cash: Decimal,
    ) -> BacktestResult:
        ohlcv = data.ohlcv
        n = len(ohlcv)

        # ------------------------------------------------------------------
        # 1. Generate signals
        # ------------------------------------------------------------------
        signals: SignalFrame = strategy.generate_signals(data, params)
        sig_df = signals.df

        if not sig_df.index.equals(ohlcv.index):
            raise ValueError(
                "SignalFrame index does not match BarsData.ohlcv.index"
            )

        # ------------------------------------------------------------------
        # 2. Translate side column to vbt entry/exit bool arrays
        # ------------------------------------------------------------------
        side_arr = sig_df["side"].astype(np.int8)

        long_target = side_arr == 1
        short_target = side_arr == -1

        # Edge transitions: True on the bar where the state FIRST becomes active
        entries = long_target & ~long_target.shift(1, fill_value=False)
        exits = ~long_target & long_target.shift(1, fill_value=False)
        short_entries = short_target & ~short_target.shift(1, fill_value=False)
        short_exits = ~short_target & short_target.shift(1, fill_value=False)

        # ------------------------------------------------------------------
        # 3. Size series: qty = size_hint * (initial_cash / open_price)
        #    vectorbt handles cash constraints internally.
        # ------------------------------------------------------------------
        cash_f = float(initial_cash)
        base_qty = cash_f / ohlcv["open"]  # qty if fully invested at this bar's open
        size = sig_df["size_hint"] * base_qty

        # ------------------------------------------------------------------
        # 4. No-lookahead price: signal at T executes at T+1 open.
        #    Shift open by -1 so that price[T] = open[T+1].
        #    The last bar gets close[last] as a harmless fallback.
        # ------------------------------------------------------------------
        next_open = ohlcv["open"].shift(-1)
        next_open.iloc[-1] = ohlcv["close"].iloc[-1]

        # ------------------------------------------------------------------
        # 5. Cost series
        # ------------------------------------------------------------------
        atr_ratio = daily_atr_per_close(ohlcv, 14)
        slip_bps_series = slippage_bps(costs, atr_ratio)  # in bps
        slip_frac_series = slip_bps_series / 1e4           # fractional
        fee_frac = costs.taker_fee_bps * 1e-4              # constant scalar

        # ------------------------------------------------------------------
        # 6. Stop / target → fractional offsets from entry price
        # ------------------------------------------------------------------
        raw_stop = sig_df["stop"]
        raw_target = sig_df["target"]
        has_stops = raw_stop.notna().any()
        has_targets = raw_target.notna().any()

        sl_stop_arg = None
        tp_stop_arg = None

        if has_stops:
            close_s = ohlcv["close"]
            # For long (side=+1): sl_frac = (close - stop) / close
            # For short (side=-1): sl_frac = (stop - close) / close
            sl_frac_long = (close_s - raw_stop).abs() / close_s
            sl_frac_short = (raw_stop - close_s).abs() / close_s
            sl_stop_arg = pd.Series(
                np.where(side_arr == 1, sl_frac_long, sl_frac_short),
                index=ohlcv.index,
                dtype=np.float64,
            )
            # Preserve NaN where stop is NaN
            sl_stop_arg[raw_stop.isna()] = np.nan

        if has_targets:
            close_s = ohlcv["close"]
            tp_frac_long = (raw_target - close_s).abs() / close_s
            tp_frac_short = (close_s - raw_target).abs() / close_s
            tp_stop_arg = pd.Series(
                np.where(side_arr == 1, tp_frac_long, tp_frac_short),
                index=ohlcv.index,
                dtype=np.float64,
            )
            tp_stop_arg[raw_target.isna()] = np.nan

        # ------------------------------------------------------------------
        # 7. Run vectorbt
        # ------------------------------------------------------------------
        freq_str = "1min" if data.interval == "1m" else "15min"

        _log.debug(
            "Running vbt.Portfolio.from_signals: n=%d, interval=%s, cash=%.2f",
            n,
            data.interval,
            cash_f,
        )

        pf = vbt.Portfolio.from_signals(
            close=ohlcv["close"],
            entries=entries,
            exits=exits,
            short_entries=short_entries,
            short_exits=short_exits,
            size=size,
            size_type="amount",
            price=next_open,
            open=ohlcv["open"],
            high=ohlcv["high"],
            low=ohlcv["low"],
            fees=fee_frac,
            slippage=slip_frac_series,
            sl_stop=sl_stop_arg,
            tp_stop=tp_stop_arg,
            init_cash=cash_f,
            freq=freq_str,
            seed=0,
            call_seq="auto",
        )

        # ------------------------------------------------------------------
        # 8. Extract equity curve
        # ------------------------------------------------------------------
        equity_curve: pd.Series = pf.value()
        equity_curve.index = ohlcv.index  # ensure UTC alignment

        # ------------------------------------------------------------------
        # 9. Extract trades and build BacktestResult.trades DataFrame
        # ------------------------------------------------------------------
        trades_df = _extract_trades(
            pf=pf,
            ohlcv=ohlcv,
            entries=entries,
            exits=exits,
            short_entries=short_entries,
            short_exits=short_exits,
            slip_frac_series=slip_frac_series,
            sl_stop_arg=sl_stop_arg,
            tp_stop_arg=tp_stop_arg,
            raw_stop=raw_stop,
            raw_target=raw_target,
            sig_df=sig_df,
            funding=data.funding,
        )

        # ------------------------------------------------------------------
        # 10. Compute metrics
        # ------------------------------------------------------------------
        bars_per_year = _BARS_PER_YEAR.get(data.interval, 525_600)
        metrics = compute_metrics(equity_curve, trades_df, bars_per_year=bars_per_year)

        return BacktestResult(
            equity_curve=equity_curve,
            trades=trades_df,
            metrics=metrics,
            engine_name="vectorbt",
            params=dict(params),
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_trades(
    *,
    pf: object,
    ohlcv: pd.DataFrame,
    entries: pd.Series,
    exits: pd.Series,
    short_entries: pd.Series,
    short_exits: pd.Series,
    slip_frac_series: pd.Series,
    sl_stop_arg: pd.Series | None,
    tp_stop_arg: pd.Series | None,
    raw_stop: pd.Series,
    raw_target: pd.Series,
    sig_df: pd.DataFrame,
    funding: pd.DataFrame | None,
) -> pd.DataFrame:
    """Convert vbt trade records to BacktestResult.trades schema."""
    idx = ohlcv.index
    n = len(idx)

    # vbt trades records array
    ra = pf.trades.records_arr  # structured numpy array

    if len(ra) == 0:
        return _empty_trades_df()

    rows = []
    for rec in ra:
        entry_idx = int(rec["entry_idx"])
        exit_idx = int(rec["exit_idx"])
        direction = int(rec["direction"])  # 0=Long, 1=Short
        side_val = np.int8(1 if direction == 0 else -1)

        entry_price_vbt = float(rec["entry_price"])
        exit_price_vbt = float(rec["exit_price"])
        qty = float(rec["size"])
        entry_fees = float(rec["entry_fees"])
        exit_fees = float(rec["exit_fees"])
        pnl_vbt = float(rec["pnl"])  # net after fees; slippage baked into price

        # ------------------------------------------------------------------
        # Slippage: reconstruct from fill prices using the slip fraction
        # The slip fraction at entry_idx determines how much price was moved.
        # slip_cost = fill_price * qty * slip_frac (approximation — exact up to O(slip^2))
        # ------------------------------------------------------------------
        entry_slip_frac = float(slip_frac_series.iloc[entry_idx]) if entry_idx < n else 0.0
        exit_slip_frac = float(slip_frac_series.iloc[exit_idx]) if exit_idx < n else 0.0

        slip_entry = entry_price_vbt * qty * entry_slip_frac
        slip_exit = exit_price_vbt * qty * exit_slip_frac
        slippage_total = slip_entry + slip_exit

        fees_total = entry_fees + exit_fees

        # gross_pnl = pnl_vbt + fees + slippage  (pnl_vbt is already net of both)
        gross_pnl = pnl_vbt + fees_total + slippage_total

        # ------------------------------------------------------------------
        # Entry/exit timestamps:
        # Execution is at T+1 open for signal-driven entries.
        # entry_idx is the signal bar (T); actual execution at T+1.
        # Clamp to valid range.
        # ------------------------------------------------------------------
        exec_entry_idx = min(entry_idx + 1, n - 1)
        exec_exit_idx = min(exit_idx + 1, n - 1)

        entry_time = idx[exec_entry_idx]
        exit_time = idx[exec_exit_idx]

        # bars_held
        bars_held = int(exit_idx - entry_idx)

        # ------------------------------------------------------------------
        # Funding cost
        # ------------------------------------------------------------------
        signed_notional = float(entry_price_vbt * qty * side_val)
        fc = funding_cost_for_position(
            funding, entry_time, exit_time, signed_notional
        )

        net_pnl = pnl_vbt - fc

        # ------------------------------------------------------------------
        # Exit reason detection
        # ------------------------------------------------------------------
        exit_reason = _detect_exit_reason(
            exit_idx=exit_idx,
            entry_idx=entry_idx,
            side_val=side_val,
            exit_price_vbt=exit_price_vbt,
            entry_price_vbt=entry_price_vbt,
            exits=exits,
            short_exits=short_exits,
            sl_stop_arg=sl_stop_arg,
            tp_stop_arg=tp_stop_arg,
        )

        rows.append(
            {
                "entry_time": entry_time,
                "exit_time": exit_time,
                "side": side_val,
                "qty": qty,
                "entry_price": entry_price_vbt,
                "exit_price": exit_price_vbt,
                "gross_pnl": gross_pnl,
                "fees": fees_total,
                "slippage": slippage_total,
                "funding_cost": fc,
                "net_pnl": net_pnl,
                "bars_held": bars_held,
                "exit_reason": exit_reason,
            }
        )

    if not rows:
        return _empty_trades_df()

    df = pd.DataFrame(rows)
    df["entry_time"] = pd.to_datetime(df["entry_time"], utc=True)
    df["exit_time"] = pd.to_datetime(df["exit_time"], utc=True)
    df["side"] = df["side"].astype(np.int8)
    df["bars_held"] = df["bars_held"].astype(np.int64)
    df["exit_reason"] = df["exit_reason"].astype("string")
    return df


def _detect_exit_reason(
    *,
    exit_idx: int,
    entry_idx: int,
    side_val: np.int8,
    exit_price_vbt: float,
    entry_price_vbt: float,
    exits: pd.Series,
    short_exits: pd.Series,
    sl_stop_arg: pd.Series | None,
    tp_stop_arg: pd.Series | None,
) -> str:
    """Determine exit reason by comparing exit_idx against signal and stop/target arrays."""
    n = len(exits)

    if exit_idx >= n:
        return "signal"

    # If the exit signal boolean is True at exit_idx, it was signal-driven
    if side_val == 1 and bool(exits.iloc[exit_idx]):
        return "signal"
    if side_val == -1 and bool(short_exits.iloc[exit_idx]):
        return "signal"

    # Check if SL fired: compare exit_price to expected SL level
    if sl_stop_arg is not None:
        sl_frac_at_entry = float(sl_stop_arg.iloc[entry_idx]) if not np.isnan(float(sl_stop_arg.iloc[entry_idx])) else np.nan
        if not np.isnan(sl_frac_at_entry):
            if side_val == 1:
                expected_sl = entry_price_vbt * (1.0 - sl_frac_at_entry)
            else:
                expected_sl = entry_price_vbt * (1.0 + sl_frac_at_entry)
            if abs(exit_price_vbt - expected_sl) / max(expected_sl, 1e-9) < _PRICE_MATCH_TOL:
                return "stop"

    # Check if TP fired
    if tp_stop_arg is not None:
        tp_frac_at_entry = float(tp_stop_arg.iloc[entry_idx]) if not np.isnan(float(tp_stop_arg.iloc[entry_idx])) else np.nan
        if not np.isnan(tp_frac_at_entry):
            if side_val == 1:
                expected_tp = entry_price_vbt * (1.0 + tp_frac_at_entry)
            else:
                expected_tp = entry_price_vbt * (1.0 - tp_frac_at_entry)
            if abs(exit_price_vbt - expected_tp) / max(expected_tp, 1e-9) < _PRICE_MATCH_TOL:
                return "target"

    # Fallback
    return "signal"


def _empty_trades_df() -> pd.DataFrame:
    """Return an empty DataFrame with the correct schema."""
    return pd.DataFrame(
        {
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
    )


__all__ = ["VectorbtEngine"]
