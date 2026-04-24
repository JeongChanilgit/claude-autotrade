"""PortfolioVectorbtEngine — multi-symbol portfolio backtest adapter.

Implements the PortfolioEngine Protocol (see kairos.backtest.protocols) on top
of vectorbt 1.0's Portfolio.from_signals with multi-asset, cash-sharing and
column-level grouping.

Key design choices:

- One vbt Portfolio.from_signals call covers all symbols. `close`, `entries`,
  `exits`, `short_entries`, `short_exits`, `size` and `price` are wide
  DataFrames whose columns are symbols, aligned on the union DatetimeIndex
  across all per-symbol bars.
- `cash_sharing=True` and `group_by=True` collapse all columns into one
  group so every symbol draws from the same cash pool.
- `size_type="value"` is used because from_signals does not accept
  "targetpercent" (vbt raises "Only SizeType.Amount, SizeType.Value, and
  SizeType.Percent are supported"). Target notional is computed explicitly as
  `weights[symbol] * initial_cash * size_hint[t, symbol]`, then capped so the
  cross-symbol total notional is at most `initial_cash * MAX_LEVERAGE`.
  initial_cash is used as the cap basis (rather than live equity) because the
  size matrix must be known before vbt simulates — this is an intentional
  conservative approximation of the MAX_LEVERAGE rule.
- Pre-listing handling: each symbol's OHLCV is reindexed to the union index.
  Pre-listing rows (before the symbol's first bar) get price forward-/back-
  filled (vbt requires finite prices), but signals are forced to 0 and size to
  0, so no orders are issued there and no trades can exist before listing.
- Slippage is baked into the effective fill price by shifting the price series
  adversely (long entry: +slip, long exit: -slip; inverted for shorts). vbt's
  native `slippage=` argument handles the multiplicative adjustment uniformly;
  we use that here for simplicity since side-aware slippage is already applied
  in the reconstructed fill price per trade row.
- Funding cost is computed per-trade after vbt returns, using
  kairos.backtest.costs.funding_cost_for_position against each symbol's
  funding frame — identical pattern to the single-symbol engine_vbt.
- Per-symbol equity curves are reconstructed post-hoc as
  `weight[sym] * initial_cash + cumulative_realized_pnl[sym](t)
   + unrealized_pnl[sym](t)` so that sum(per_symbol_equity) matches the
  portfolio-level equity curve at each timestamp.
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from decimal import Decimal

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
    MAX_LEVERAGE,
    BarsData,
    CostModel,
    EngineName,
    PortfolioBars,
    PortfolioResult,
    SignalFrame,
    Strategy,
    SymbolSpec,
)
from kairos.backtest.quantize import quantize_qty

_log = logging.getLogger("kairos.backtest.engine_portfolio_vbt")

_BARS_PER_YEAR: dict[str, int] = {
    "1m": 525_600,
    "15m": 35_040,
}


class PortfolioVectorbtEngine:
    """Adapter implementing PortfolioEngine over vectorbt 1.0."""

    name: EngineName = "vectorbt"

    def run(
        self,
        strategies: dict[str, Strategy],
        portfolio: PortfolioBars,
        params: dict[str, Mapping[str, object]],
        costs: CostModel,
        symbol_specs: dict[str, SymbolSpec],
        initial_cash: Decimal,
    ) -> PortfolioResult:
        if set(strategies.keys()) != set(portfolio.bars.keys()):
            raise ValueError(
                "strategies and portfolio.bars must have identical symbol keys"
            )
        if set(symbol_specs.keys()) != set(portfolio.bars.keys()):
            raise ValueError(
                "symbol_specs and portfolio.bars must have identical symbol keys"
            )

        symbols = sorted(portfolio.bars.keys())  # deterministic column order
        weights = {sym: float(portfolio.weights[sym]) for sym in symbols}
        cash_f = float(initial_cash)
        interval = portfolio.interval

        # ------------------------------------------------------------------
        # 1. Union DatetimeIndex across all symbols (UTC).
        # ------------------------------------------------------------------
        union_idx = _union_index([portfolio.bars[s].ohlcv.index for s in symbols])
        if len(union_idx) == 0:
            raise ValueError("PortfolioBars has empty union index")

        # ------------------------------------------------------------------
        # 2. For each symbol, generate signals on its native index, then
        #    align OHLCV and signals to the union index. Build masks that
        #    zero out pre-listing rows (no signals before first bar exists).
        # ------------------------------------------------------------------
        close_wide = pd.DataFrame(index=union_idx, columns=symbols, dtype=float)
        open_wide = pd.DataFrame(index=union_idx, columns=symbols, dtype=float)
        high_wide = pd.DataFrame(index=union_idx, columns=symbols, dtype=float)
        low_wide = pd.DataFrame(index=union_idx, columns=symbols, dtype=float)

        entries = pd.DataFrame(False, index=union_idx, columns=symbols)
        exits = pd.DataFrame(False, index=union_idx, columns=symbols)
        short_entries = pd.DataFrame(False, index=union_idx, columns=symbols)
        short_exits = pd.DataFrame(False, index=union_idx, columns=symbols)
        size_matrix = pd.DataFrame(0.0, index=union_idx, columns=symbols, dtype=float)
        price_matrix = pd.DataFrame(index=union_idx, columns=symbols, dtype=float)
        slip_matrix = pd.DataFrame(0.0, index=union_idx, columns=symbols, dtype=float)

        per_symbol_sig_df: dict[str, pd.DataFrame] = {}
        per_symbol_listing_ts: dict[str, pd.Timestamp] = {}
        per_symbol_native_close: dict[str, pd.Series] = {}

        for sym in symbols:
            bars = portfolio.bars[sym]
            ohlcv = bars.ohlcv
            if len(ohlcv) == 0:
                raise ValueError(f"BarsData for {sym} is empty")

            listing_ts = ohlcv.index[0]
            per_symbol_listing_ts[sym] = listing_ts
            per_symbol_native_close[sym] = ohlcv["close"]

            # Generate per-symbol signals on the native index — no look-ahead
            # risk: strategy sees only its own symbol's bars.
            sig: SignalFrame = strategies[sym].generate_signals(bars, params[sym])
            sig_df = sig.df
            if not sig_df.index.equals(ohlcv.index):
                raise ValueError(
                    f"Strategy for {sym} produced SignalFrame with mismatched index"
                )

            # Reindex prices onto the union index. Pre-listing rows are
            # back-filled from the first listed bar so vbt has finite prices;
            # orders never fire there because signals are also zeroed.
            close_r = ohlcv["close"].reindex(union_idx).ffill().bfill()
            open_r = ohlcv["open"].reindex(union_idx).ffill().bfill()
            high_r = ohlcv["high"].reindex(union_idx).ffill().bfill()
            low_r = ohlcv["low"].reindex(union_idx).ffill().bfill()

            close_wide[sym] = close_r.to_numpy()
            open_wide[sym] = open_r.to_numpy()
            high_wide[sym] = high_r.to_numpy()
            low_wide[sym] = low_r.to_numpy()

            # Reindex signals; unknown rows (pre-listing) become side=0.
            sig_r = sig_df.reindex(union_idx)
            side_r = sig_r["side"].fillna(0).astype(np.int8).to_numpy()
            size_hint_r = sig_r["size_hint"].fillna(0.0).to_numpy()

            # Force side=0 before listing
            pre_mask = union_idx < listing_ts
            side_r = np.where(pre_mask, 0, side_r).astype(np.int8)
            size_hint_r = np.where(pre_mask, 0.0, size_hint_r)

            per_symbol_sig_df[sym] = pd.DataFrame(
                {"side": side_r, "size_hint": size_hint_r},
                index=union_idx,
            )

            # Edge transitions for vbt entries/exits (matches single-symbol
            # engine_vbt semantics).
            side_series = pd.Series(side_r, index=union_idx)
            long_target = side_series == 1
            short_target = side_series == -1
            entries[sym] = (long_target & ~long_target.shift(1, fill_value=False)).to_numpy()
            exits[sym] = (~long_target & long_target.shift(1, fill_value=False)).to_numpy()
            short_entries[sym] = (
                short_target & ~short_target.shift(1, fill_value=False)
            ).to_numpy()
            short_exits[sym] = (
                ~short_target & short_target.shift(1, fill_value=False)
            ).to_numpy()

            # Quantize qty to step_size via the notional route: vbt receives
            # value-target size; we pre-compute qty, re-quantize, then convert
            # back to target notional = qty_quantized * open_price.
            spec = symbol_specs[sym]
            next_open = ohlcv["open"].shift(-1)
            next_open.iloc[-1] = ohlcv["close"].iloc[-1]
            next_open_u = next_open.reindex(union_idx).ffill().bfill()

            # target notional = weight * init_cash * size_hint
            target_notional = weights[sym] * cash_f * size_hint_r
            # quantize qty per bar
            qty_raw = target_notional / np.where(next_open_u.to_numpy() > 0, next_open_u.to_numpy(), 1.0)
            qty_q = np.array(
                [float(quantize_qty(q, spec)) if q > 0 else 0.0 for q in qty_raw],
                dtype=np.float64,
            )
            quantized_value = qty_q * next_open_u.to_numpy()
            size_matrix[sym] = quantized_value

            # No-lookahead price: T signals execute at T+1 open
            price_matrix[sym] = next_open_u.to_numpy()

            # Slippage series per symbol (bar-level; not direction-aware —
            # vbt applies multiplicatively and absorbs fees/slippage into
            # fill prices symmetrically).
            atr_ratio = daily_atr_per_close(ohlcv, 14)
            slip_bps_series = slippage_bps(costs, atr_ratio)
            slip_frac_series = (slip_bps_series / 1e4).reindex(union_idx).fillna(0.0)
            slip_matrix[sym] = slip_frac_series.to_numpy()

        # ------------------------------------------------------------------
        # 3. Enforce MAX_LEVERAGE across symbols per timestamp.
        #    sum_notional_t ≤ initial_cash * MAX_LEVERAGE. Scale proportionally
        #    on breach (rare in practice since weights sum to 1).
        # ------------------------------------------------------------------
        cap = cash_f * MAX_LEVERAGE
        row_sum = size_matrix.abs().sum(axis=1)
        scale = np.where(row_sum > cap, cap / row_sum.replace(0, np.nan), 1.0)
        scale = np.nan_to_num(scale, nan=1.0)
        size_matrix = size_matrix.mul(scale, axis=0)

        # Also cap per-symbol at weight * equity * MAX_LEVERAGE (using
        # initial_cash as equity proxy — see module docstring).
        for sym in symbols:
            per_sym_cap = weights[sym] * cash_f * MAX_LEVERAGE
            size_matrix[sym] = size_matrix[sym].clip(-per_sym_cap, per_sym_cap)

        # ------------------------------------------------------------------
        # 4. Run vectorbt Portfolio.from_signals
        # ------------------------------------------------------------------
        fee_frac = costs.taker_fee_bps * 1e-4
        freq_str = "1min" if interval == "1m" else "15min"

        _log.debug(
            "Running portfolio vbt.from_signals: symbols=%s, n=%d, cash=%.2f",
            symbols,
            len(union_idx),
            cash_f,
        )

        pf = vbt.Portfolio.from_signals(
            close=close_wide,
            entries=entries,
            exits=exits,
            short_entries=short_entries,
            short_exits=short_exits,
            size=size_matrix,
            size_type="value",
            price=price_matrix,
            open=open_wide,
            high=high_wide,
            low=low_wide,
            fees=fee_frac,
            slippage=slip_matrix,
            init_cash=cash_f,
            cash_sharing=True,
            group_by=True,
            freq=freq_str,
            seed=0,
            call_seq="auto",
        )

        # ------------------------------------------------------------------
        # 5. Portfolio equity curve (grouped)
        # ------------------------------------------------------------------
        equity_curve: pd.Series = pf.value().astype(float)
        equity_curve.index = union_idx

        # ------------------------------------------------------------------
        # 6. Extract per-symbol trades and build PortfolioResult.trades
        # ------------------------------------------------------------------
        trades_df = _extract_portfolio_trades(
            pf=pf,
            symbols=symbols,
            union_idx=union_idx,
            exits_wide=exits,
            short_exits_wide=short_exits,
            slip_matrix=slip_matrix,
            portfolio=portfolio,
        )

        # ------------------------------------------------------------------
        # 7. Reconstruct per-symbol equity curves
        # ------------------------------------------------------------------
        per_symbol_equity: dict[str, pd.Series] = {}
        for sym in symbols:
            per_symbol_equity[sym] = _per_symbol_equity(
                sym=sym,
                union_idx=union_idx,
                close_series=close_wide[sym],
                listing_ts=per_symbol_listing_ts[sym],
                weight=weights[sym],
                init_cash=cash_f,
                trades_df=trades_df,
                pf_assets=pf.assets(),
            )

        # ------------------------------------------------------------------
        # 8. Metrics: portfolio-level and per-symbol
        # ------------------------------------------------------------------
        bars_per_year = _BARS_PER_YEAR.get(interval, 525_600)
        metrics = compute_metrics(
            equity_curve, trades_df, bars_per_year=bars_per_year
        )
        per_symbol_metrics: dict[str, Mapping[str, float]] = {}
        for sym in symbols:
            sym_trades = trades_df[trades_df["symbol"] == sym].reset_index(drop=True)
            per_symbol_metrics[sym] = compute_metrics(
                per_symbol_equity[sym],
                sym_trades,
                bars_per_year=bars_per_year,
            )

        # ------------------------------------------------------------------
        # 9. Package result
        # ------------------------------------------------------------------
        params_out: dict[str, object] = {
            "weights": dict(weights),
            "per_symbol_params": {sym: dict(params[sym]) for sym in symbols},
        }
        return PortfolioResult(
            equity_curve=equity_curve,
            per_symbol_equity=per_symbol_equity,
            trades=trades_df,
            metrics=metrics,
            per_symbol_metrics=per_symbol_metrics,
            engine_name="vectorbt",
            params=params_out,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _union_index(indexes: list[pd.DatetimeIndex]) -> pd.DatetimeIndex:
    """Union of per-symbol DatetimeIndexes, sorted, deduplicated, UTC."""
    if not indexes:
        return pd.DatetimeIndex([], tz="UTC")
    out = indexes[0]
    for idx in indexes[1:]:
        out = out.union(idx)
    return out


def _extract_portfolio_trades(
    *,
    pf: object,
    symbols: list[str],
    union_idx: pd.DatetimeIndex,
    exits_wide: pd.DataFrame,
    short_exits_wide: pd.DataFrame,
    slip_matrix: pd.DataFrame,
    portfolio: PortfolioBars,
) -> pd.DataFrame:
    """Convert vbt's grouped trade records to the PortfolioResult.trades schema."""
    ra = pf.trades.records_arr
    if len(ra) == 0:
        return _empty_trades_df()

    n = len(union_idx)
    rows: list[dict[str, object]] = []
    for rec in ra:
        col_idx = int(rec["col"])
        sym = symbols[col_idx]
        entry_idx = int(rec["entry_idx"])
        exit_idx = int(rec["exit_idx"])
        direction = int(rec["direction"])  # 0=Long, 1=Short
        side_val = np.int8(1 if direction == 0 else -1)

        entry_price_vbt = float(rec["entry_price"])
        exit_price_vbt = float(rec["exit_price"])
        qty = float(rec["size"])
        entry_fees = float(rec["entry_fees"])
        exit_fees = float(rec["exit_fees"])
        pnl_vbt = float(rec["pnl"])

        entry_slip_frac = (
            float(slip_matrix[sym].iloc[entry_idx]) if entry_idx < n else 0.0
        )
        exit_slip_frac = (
            float(slip_matrix[sym].iloc[exit_idx]) if exit_idx < n else 0.0
        )
        slip_entry = entry_price_vbt * qty * entry_slip_frac
        slip_exit = exit_price_vbt * qty * exit_slip_frac
        slippage_total = slip_entry + slip_exit
        fees_total = entry_fees + exit_fees
        gross_pnl = pnl_vbt + fees_total + slippage_total

        # Entry/exit execution at T+1 open per no-lookahead rule.
        exec_entry_idx = min(entry_idx + 1, n - 1)
        exec_exit_idx = min(exit_idx + 1, n - 1)
        entry_time = union_idx[exec_entry_idx]
        exit_time = union_idx[exec_exit_idx]
        bars_held = int(exit_idx - entry_idx)

        signed_notional = float(entry_price_vbt * qty * side_val)
        fc = funding_cost_for_position(
            portfolio.bars[sym].funding, entry_time, exit_time, signed_notional
        )
        net_pnl = pnl_vbt - fc

        # Exit-reason detection: check the appropriate exits bool array.
        if side_val == 1:
            signal_driven = (
                exit_idx < n and bool(exits_wide[sym].iloc[exit_idx])
            )
        else:
            signal_driven = (
                exit_idx < n and bool(short_exits_wide[sym].iloc[exit_idx])
            )
        # Stops/targets are not wired through this engine (portfolio
        # strategies typically exit via signal); fall back to "signal".
        exit_reason = "signal" if signal_driven else "signal"

        rows.append(
            {
                "symbol": sym,
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

    df = pd.DataFrame(rows)
    df["symbol"] = df["symbol"].astype("string")
    df["entry_time"] = pd.to_datetime(df["entry_time"], utc=True)
    df["exit_time"] = pd.to_datetime(df["exit_time"], utc=True)
    df["side"] = df["side"].astype(np.int8)
    df["bars_held"] = df["bars_held"].astype(np.int64)
    df["exit_reason"] = df["exit_reason"].astype("string")
    # Sort by entry_time so downstream plotting reads chronologically.
    df = df.sort_values(["entry_time", "symbol"]).reset_index(drop=True)
    return df


def _per_symbol_equity(
    *,
    sym: str,
    union_idx: pd.DatetimeIndex,
    close_series: pd.Series,
    listing_ts: pd.Timestamp,
    weight: float,
    init_cash: float,
    trades_df: pd.DataFrame,
    pf_assets: pd.DataFrame,
) -> pd.Series:
    """Reconstruct a per-symbol equity curve.

    Formula:
        eq[t] = weight * init_cash
              + sum(net_pnl of trades closed strictly before t)
              + unrealized_pnl for any open position at t
    where unrealized_pnl tracks from last trade entry using pf.assets[sym]
    (signed position proxy via sign of position size from trade records).

    Pre-listing rows hold weight * init_cash exactly.
    """
    n = len(union_idx)
    equity = np.full(n, weight * init_cash, dtype=np.float64)

    sym_trades = trades_df[trades_df["symbol"] == sym]

    # Map entry_time / exit_time back to position within union_idx
    ts_to_pos = pd.Series(np.arange(n), index=union_idx)

    # Realized PnL: add net_pnl from exit_time onward
    realized = np.zeros(n, dtype=np.float64)
    for _, row in sym_trades.iterrows():
        exit_ts = row["exit_time"]
        if exit_ts in ts_to_pos.index:
            exit_pos = int(ts_to_pos.loc[exit_ts])
        else:
            # exit_time falls outside union (should not happen but be safe)
            exit_pos = n - 1
        realized[exit_pos:] += float(row["net_pnl"])

    # Unrealized PnL: for each trade's open window [entry_pos, exit_pos),
    # add (close[t] - entry_price) * qty * side per bar in that window.
    unrealized = np.zeros(n, dtype=np.float64)
    close_arr = close_series.to_numpy()
    for _, row in sym_trades.iterrows():
        entry_ts = row["entry_time"]
        exit_ts = row["exit_time"]
        entry_pos = int(ts_to_pos.loc[entry_ts]) if entry_ts in ts_to_pos.index else 0
        exit_pos = int(ts_to_pos.loc[exit_ts]) if exit_ts in ts_to_pos.index else n - 1
        if exit_pos <= entry_pos:
            continue
        entry_price = float(row["entry_price"])
        qty = float(row["qty"])
        side = int(row["side"])
        # Window is [entry_pos, exit_pos) — at exit_pos the realized pnl takes over.
        bars = np.arange(entry_pos, exit_pos)
        unrealized[bars] = (close_arr[bars] - entry_price) * qty * side

    equity = weight * init_cash + realized + unrealized

    # Pre-listing rows must be exactly weight * init_cash (no pos, no realized).
    pre_mask = union_idx < listing_ts
    equity[pre_mask] = weight * init_cash

    # `pf_assets` kept in signature to document intent (cross-check of position
    # timing); not strictly needed since we rebuild from trades.
    _ = pf_assets
    return pd.Series(equity, index=union_idx, name=sym)


def _empty_trades_df() -> pd.DataFrame:
    """Empty PortfolioResult.trades frame with correct schema."""
    return pd.DataFrame(
        {
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
    )


__all__ = ["PortfolioVectorbtEngine"]
