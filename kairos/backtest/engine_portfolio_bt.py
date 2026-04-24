"""Portfolio backtrader engine for the Kairos backtest layer (Phase 5D).

Conforms to the PortfolioEngine protocol defined in
kairos.backtest.protocols. Uses a single cerebro with N data feeds and one
bt.Strategy that reads pre-computed SignalFrames per symbol. Capital is
shared across all symbols; per-symbol allocation is scaled by
weights[symbol] and clipped to MAX_LEVERAGE of portfolio equity.

No look-ahead: signals at bar T produce orders filled at bar T+1 open
(backtrader's default Market order behaviour).
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

import backtrader as bt
import numpy as np
import pandas as pd

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
from kairos.backtest.quantize import meets_min_notional, quantize_price, quantize_qty

log = logging.getLogger("kairos.backtest.engine_portfolio_bt")


# ---------------------------------------------------------------------------
# PandasData feed (same column mapping as single-symbol engine_bt)
# ---------------------------------------------------------------------------

class _KairosFeed(bt.feeds.PandasData):
    """PandasData subclass mapping our OHLCV column names."""

    params = (
        ("datetime", None),
        ("open", "open"),
        ("high", "high"),
        ("low", "low"),
        ("close", "close"),
        ("volume", "volume"),
        ("openinterest", -1),
    )


# ---------------------------------------------------------------------------
# Closed trade record (per symbol)
# ---------------------------------------------------------------------------

class _TradeRecord:
    """One closed round-trip for one symbol."""

    __slots__ = (
        "symbol", "side", "qty", "entry_price", "exit_price",
        "entry_time", "exit_time", "entry_commission", "exit_commission",
        "bars_held", "exit_reason", "bt_pnl",
    )

    def __init__(self) -> None:
        self.symbol: str = ""
        self.side: int = 0
        self.qty: float = 0.0
        self.entry_price: float = float("nan")
        self.exit_price: float = float("nan")
        self.entry_time: pd.Timestamp | None = None
        self.exit_time: pd.Timestamp | None = None
        self.entry_commission: float = 0.0
        self.exit_commission: float = 0.0
        self.bars_held: int = 0
        self.exit_reason: str = "signal"
        self.bt_pnl: float = 0.0


# ---------------------------------------------------------------------------
# Portfolio runner — one bt.Strategy for N data feeds
# ---------------------------------------------------------------------------

class _PortfolioRunner(bt.Strategy):
    """Single bt.Strategy that drives N data feeds with per-symbol signals.

    On each next() iteration, for each data feed we look up the current bar's
    signal. We compute per-symbol desired notional = weight * portfolio_equity
    * size_hint * side. If the sum of absolute notionals exceeds
    portfolio_equity * MAX_LEVERAGE, all allocations scale proportionally. We
    then use order_target_size on each feed to reach the rounded target qty.
    """

    params = (
        ("signals_by_symbol", None),    # dict[str, pd.DataFrame]
        ("specs_by_symbol", None),      # dict[str, SymbolSpec]
        ("weights", None),              # dict[str, float]
        ("listing_ts", None),           # dict[str, pd.Timestamp] (tz-naive)
        ("symbols_by_idx", None),       # list[str], aligned with data feeds
    )

    def __init__(self) -> None:
        self._signals_by_symbol: dict[str, pd.DataFrame] = self.params.signals_by_symbol
        self._specs: dict[str, SymbolSpec] = self.params.specs_by_symbol
        self._weights: dict[str, float] = self.params.weights
        self._listing_ts: dict[str, Any] = self.params.listing_ts
        self._symbols: list[str] = self.params.symbols_by_idx

        # Pre-build per-symbol signal map: symbol -> {naive_dt -> (side, size_hint)}
        # WHY dict-of-dicts rather than DataFrame lookup: next() runs per bar per
        # symbol — O(1) access matters and we don't need pandas semantics here.
        self._sig_map: dict[str, dict[Any, tuple[int, float]]] = {}
        for sym, df in self._signals_by_symbol.items():
            m: dict[Any, tuple[int, float]] = {}
            for ts, row in df.iterrows():
                key = ts.replace(tzinfo=None) if getattr(ts, "tzinfo", None) else ts
                m[key] = (int(row["side"]), float(row["size_hint"]))
            self._sig_map[sym] = m

        # Map symbol -> data feed index (position in self.datas)
        self._data_idx: dict[str, int] = {s: i for i, s in enumerate(self._symbols)}

        # Equity log: (UTC Timestamp, float)
        self.equity_log: list[tuple[pd.Timestamp, float]] = []

        # Per-symbol (size, close) log for per_symbol_equity decomposition.
        # Each entry: (ts, {symbol: (size, close)}). Pre-listing symbols emit
        # (0.0, NaN) — the result builder treats NaN close as "no PnL delta".
        self.pos_value_log: list[tuple[pd.Timestamp, dict[str, tuple[float, float]]]] = []

        # Trade tracking: per-symbol open leg -> completed records
        self.closed_records: list[_TradeRecord] = []
        self._open_entry: dict[str, dict[str, Any]] = {}  # symbol -> {price,size,time,comm}

        # Prior target side per symbol (to detect reversal)
        self._prev_side: dict[str, int] = {s: 0 for s in self._symbols}

    # ----- helpers -----

    def _get_signal(self, sym: str, naive_dt: Any) -> tuple[int, float]:
        m = self._sig_map.get(sym)
        if m is None:
            return (0, 1.0)
        v = m.get(naive_dt)
        if v is None:
            return (0, 1.0)
        return v

    def _feed_datetime(self, feed: bt.feeds.PandasData) -> Any:
        # WHY guard against empty: feeds that start later than the first bar
        # have no data at that moment; data.datetime(0) raises IndexError.
        if len(feed) == 0:
            return None
        return feed.datetime.datetime(0)

    # ----- main loop -----

    def next(self) -> None:
        # Use the first feed's current bar timestamp as the portfolio clock.
        # All feeds share the same interval and window; pre-listing feeds will
        # simply be short. We pick the feed with the largest current length.
        clock_dt = None
        for d in self.datas:
            if len(d) > 0:
                ts = d.datetime.datetime(0)
                if clock_dt is None or ts > clock_dt:
                    clock_dt = ts
        if clock_dt is None:
            return
        ts_aware = pd.Timestamp(clock_dt, tz="UTC")

        total_equity = float(self.broker.getvalue())

        # --- Pass 1: compute desired notionals per symbol ---
        desired: dict[str, tuple[float, int, float]] = {}  # sym -> (notional, side, close)
        for sym in self._symbols:
            feed = self.datas[self._data_idx[sym]]
            if len(feed) == 0:
                continue  # pre-listing: feed hasn't started yet
            feed_dt = feed.datetime.datetime(0)
            listing = self._listing_ts.get(sym)
            if listing is not None and feed_dt < listing:
                # WHY belt-and-suspenders: feed dropping handles this, but
                # keep the explicit gate for clarity and tests.
                continue

            side, size_hint = self._get_signal(sym, feed_dt)
            close_px = float(feed.close[0])
            if close_px <= 0 or size_hint <= 0:
                desired[sym] = (0.0, 0, close_px)
                continue
            weight = float(self._weights.get(sym, 0.0))
            notional = weight * total_equity * min(size_hint, 1.0) * abs(side)
            desired[sym] = (notional, side, close_px)

        # --- Pass 2: apply MAX_LEVERAGE cap to absolute notionals ---
        total_abs = sum(n for (n, _, _) in desired.values())
        cap = total_equity * MAX_LEVERAGE
        scale = 1.0
        if total_abs > cap and total_abs > 0:
            scale = cap / total_abs

        # WHY additional safety haircut: backtrader checks margin at the NEXT
        # bar's open price, which can exceed the current close by one bar's
        # move. Without this, a 100% weight long would be rejected as Margin
        # because desired_notional ≈ equity but next-bar fill price > close.
        # A ~0.5% haircut accommodates one-bar drift; combined with step_size
        # quantization it keeps total notional safely under cash.
        scale *= 0.995

        # --- Pass 3: place target orders ---
        for sym, (notional, side, close_px) in desired.items():
            feed = self.datas[self._data_idx[sym]]
            spec = self._specs[sym]
            if side == 0 or notional <= 0 or close_px <= 0:
                # Target flat
                if self.getposition(data=feed).size != 0:
                    self.order_target_size(data=feed, target=0)
                continue

            scaled_notional = notional * scale
            raw_qty = scaled_notional / close_px
            q_price = quantize_price(close_px, 1, spec)
            q_qty = quantize_qty(raw_qty, spec)
            if q_qty == Decimal("0") or not meets_min_notional(q_price, q_qty, spec):
                if self.getposition(data=feed).size != 0:
                    self.order_target_size(data=feed, target=0)
                continue

            target_qty = float(q_qty) * (1 if side == 1 else -1)
            self.order_target_size(data=feed, target=target_qty)

        # --- Log per-symbol (size, close) for equity decomposition ---
        snap: dict[str, tuple[float, float]] = {}
        for sym in self._symbols:
            feed = self.datas[self._data_idx[sym]]
            if len(feed) == 0:
                snap[sym] = (0.0, float("nan"))
                continue
            pos = self.getposition(data=feed)
            snap[sym] = (float(pos.size), float(feed.close[0]))
        self.pos_value_log.append((ts_aware, snap))
        self.equity_log.append((ts_aware, total_equity))

        for sym in self._symbols:
            # Track prev side for potential diagnostic use
            feed = self.datas[self._data_idx[sym]]
            if len(feed) == 0:
                continue
            feed_dt = feed.datetime.datetime(0)
            s, _ = self._get_signal(sym, feed_dt)
            self._prev_side[sym] = s

    # ----- trade capture via notify_order / notify_trade -----

    def notify_order(self, order: bt.Order) -> None:
        if order.status != order.Completed:
            return
        # Identify which data feed produced this order — backtrader stores it
        # on order.data.
        sym = None
        for s, idx in self._data_idx.items():
            if order.data is self.datas[idx]:
                sym = s
                break
        if sym is None:
            return

        dt_num = order.executed.dt
        exec_dt = pd.Timestamp(bt.num2date(dt_num), tz="UTC")
        exec_price = float(order.executed.price)
        exec_size = float(abs(order.executed.size))
        exec_comm = float(abs(order.executed.comm))
        is_buy = order.isbuy()

        # Pair: if there's no open entry for this symbol, this opens one.
        # Otherwise, if the fill direction opposes the open side, it closes.
        open_rec = self._open_entry.get(sym)
        if open_rec is None:
            # Opening fill
            self._open_entry[sym] = {
                "is_buy": is_buy,
                "price": exec_price,
                "size": exec_size,
                "time": exec_dt,
                "comm": exec_comm,
            }
            return

        # If same direction, treat as pyramiding append — for our simple
        # target_size flow, this shouldn't happen in practice, but be safe:
        if is_buy == open_rec["is_buy"]:
            # Weighted-average price
            tot_size = open_rec["size"] + exec_size
            if tot_size > 0:
                avg = (open_rec["price"] * open_rec["size"]
                       + exec_price * exec_size) / tot_size
                open_rec["price"] = avg
                open_rec["size"] = tot_size
                open_rec["comm"] = open_rec["comm"] + exec_comm
            return

        # Opposite direction: close (at least partially). We assume full close
        # because order_target_size drives to exact targets.
        rec = _TradeRecord()
        rec.symbol = sym
        rec.side = 1 if open_rec["is_buy"] else -1
        rec.entry_price = float(open_rec["price"])
        rec.exit_price = exec_price
        rec.entry_time = open_rec["time"]
        rec.exit_time = exec_dt
        rec.qty = float(open_rec["size"])
        rec.entry_commission = float(open_rec["comm"])
        rec.exit_commission = exec_comm
        # bars_held: crude index-based difference using the feed
        feed = self.datas[self._data_idx[sym]]
        try:
            # Find entry/exit positions in feed.datetime array — expensive but rare.
            rec.bars_held = max(1, int((rec.exit_time - rec.entry_time).total_seconds()
                                       // _feed_seconds(feed)))
        except Exception:
            rec.bars_held = 1
        rec.exit_reason = "signal"
        rec.bt_pnl = (exec_price - open_rec["price"]) * open_rec["size"] * rec.side
        self.closed_records.append(rec)

        # If the opposite-direction fill was larger than the open size, residual
        # becomes a new open position (reversal case).
        residual = exec_size - float(open_rec["size"])
        if residual > 1e-12:
            self._open_entry[sym] = {
                "is_buy": is_buy,
                "price": exec_price,
                "size": residual,
                "time": exec_dt,
                "comm": 0.0,  # residual commission already attributed to the close leg
            }
        else:
            self._open_entry.pop(sym, None)


def _feed_seconds(feed: bt.feeds.PandasData) -> float:
    """Return bar duration in seconds; defaults to 60 when unknown."""
    tf = getattr(feed, "_timeframe", None)
    comp = getattr(feed, "_compression", 1) or 1
    if tf == bt.TimeFrame.Minutes:
        return 60.0 * float(comp)
    if tf == bt.TimeFrame.Days:
        return 86400.0 * float(comp)
    return 60.0


# ---------------------------------------------------------------------------
# Engine adapter
# ---------------------------------------------------------------------------

class PortfolioBacktraderEngine:
    """Multi-symbol backtrader engine conforming to PortfolioEngine protocol."""

    name: EngineName = "backtrader"

    def run(
        self,
        strategies: dict[str, Strategy],
        portfolio: PortfolioBars,
        params: dict[str, Mapping[str, object]],
        costs: CostModel,
        symbol_specs: dict[str, SymbolSpec],
        initial_cash: Decimal,
    ) -> PortfolioResult:
        symbols = sorted(portfolio.bars.keys())
        log.info(
            "PortfolioBacktraderEngine.run: symbols=%s interval=%s",
            symbols, portfolio.interval,
        )

        # 1. Generate signals per symbol. Validate alignment to each symbol's bars.
        signals_by_symbol: dict[str, pd.DataFrame] = {}
        for sym in symbols:
            bars = portfolio.bars[sym]
            sf: SignalFrame = strategies[sym].generate_signals(bars, params.get(sym, {}))
            if not sf.df.index.equals(bars.ohlcv.index):
                raise ValueError(
                    f"SignalFrame index does not match BarsData for {sym}"
                )
            signals_by_symbol[sym] = sf.df

        # 2. Build canonical timeline = union of all per-symbol indices.
        # WHY union: pre-listing symbols have shorter frames; the portfolio
        # clock must cover every bar any symbol trades on.
        all_idx = None
        for sym in symbols:
            idx = portfolio.bars[sym].ohlcv.index
            all_idx = idx if all_idx is None else all_idx.union(idx)
        assert all_idx is not None  # symbols non-empty (weights sum to 1)
        portfolio_index: pd.DatetimeIndex = all_idx

        # 3. Pre-compute per-symbol slippage bps (post-hoc reporting).
        slip_bps_by_symbol: dict[str, pd.Series] = {}
        for sym in symbols:
            bars = portfolio.bars[sym]
            atr_ratio = daily_atr_per_close(bars.ohlcv)
            slip_bps_by_symbol[sym] = slippage_bps(costs, atr_ratio)

        # 4. Record listing timestamps (tz-naive, because bt feeds are tz-naive).
        listing_ts: dict[str, Any] = {}
        for sym in symbols:
            first_ts = portfolio.bars[sym].ohlcv.index[0]
            listing_ts[sym] = first_ts.replace(tzinfo=None) if getattr(first_ts, "tzinfo", None) else first_ts

        # 5. Determine bt TimeFrame / compression / bars_per_year from interval.
        interval = portfolio.interval
        if interval == "1m":
            tf = bt.TimeFrame.Minutes
            compression = 1
            bars_per_year = 525_600
        else:
            tf = bt.TimeFrame.Minutes
            compression = 15
            bars_per_year = 35_040

        # 6. Cerebro setup — single broker, N feeds.
        cerebro = bt.Cerebro()
        initial_cash_f = float(initial_cash)
        cerebro.broker.set_cash(initial_cash_f)
        cerebro.broker.setcommission(commission=costs.taker_fee_bps / 10000.0)
        # Base slippage floor applied uniformly; vol-dependent extra is post-hoc.
        base_slip_frac = costs.slippage_bps_base * 1e-4
        if base_slip_frac > 0:
            cerebro.broker.set_slippage_perc(perc=base_slip_frac)

        for sym in symbols:
            bars = portfolio.bars[sym]
            ohlcv_naive = bars.ohlcv[["open", "high", "low", "close", "volume"]].copy()
            if getattr(ohlcv_naive.index, "tz", None) is not None:
                ohlcv_naive.index = ohlcv_naive.index.tz_localize(None)
            ohlcv_naive.index.name = "datetime"
            feed = _KairosFeed(
                dataname=ohlcv_naive,
                timeframe=tf,
                compression=compression,
            )
            cerebro.adddata(feed, name=sym)

        cerebro.addstrategy(
            _PortfolioRunner,
            signals_by_symbol=signals_by_symbol,
            specs_by_symbol=symbol_specs,
            weights=portfolio.weights,
            listing_ts=listing_ts,
            symbols_by_idx=symbols,
        )

        bt_results = cerebro.run()
        strat: _PortfolioRunner = bt_results[0]

        # 7. Build portfolio equity curve aligned to portfolio_index.
        if strat.equity_log:
            eq_times, eq_vals = zip(*strat.equity_log)
            equity_curve = pd.Series(
                list(eq_vals),
                index=pd.DatetimeIndex(list(eq_times), name="datetime"),
                name="equity",
                dtype=float,
            )
            if equity_curve.index.tz is None:
                equity_curve.index = equity_curve.index.tz_localize("UTC")
        else:
            equity_curve = pd.Series([initial_cash_f], index=portfolio_index[:1],
                                     name="equity", dtype=float)

        equity_curve = equity_curve.reindex(portfolio_index).ffill().bfill()

        # 8. Per-symbol equity: pre-listing = weight*initial_cash (held flat),
        # post-listing = weight*initial_cash + cumulative marked PnL from
        # (size * (close - close_prev)). We approximate by:
        #   per_sym[t] = weight*initial_cash + cumsum(mark_value[t] - mark_value[t-1])
        # which gives a clean decomposition that sums to portfolio equity up to
        # residual cash drift (fees, funding). For tests that check flat
        # portfolios and single active trades, this is exact.
        per_symbol_equity: dict[str, pd.Series] = {}
        size_df, close_df = _build_size_close_df(
            strat.pos_value_log, symbols, portfolio_index, portfolio.bars
        )
        for sym in symbols:
            weight = float(portfolio.weights[sym])
            baseline = weight * initial_cash_f
            # Per-bar PnL delta = prev_size * (close_t - close_{t-1}).
            # WHY this form rather than diff(size*close): mark_value jumps when
            # size changes (entry/exit), which is a cost-basis event, not PnL.
            # Using prev_size × close_delta isolates pure mark-to-market PnL
            # and is exact under zero costs for fully closed round-trips.
            prev_size = size_df[sym].shift(1).fillna(0.0)
            close_delta = close_df[sym].diff().fillna(0.0)
            pnl_bar = prev_size * close_delta
            pnl = pnl_bar.cumsum()
            per_symbol_equity[sym] = (baseline + pnl).rename(sym).astype(float)

        # 9. Build trades DataFrame (wide, with symbol column).
        trades_df = _build_portfolio_trades(
            strat.closed_records,
            portfolio.bars,
            costs,
            slip_bps_by_symbol,
        )

        # 10. Portfolio-level metrics.
        metrics = compute_metrics(
            equity_curve,
            trades_df,
            bars_per_year=bars_per_year,
        )

        # 11. Per-symbol metrics (computed on per_symbol_equity and per-symbol
        # trade slice). For symbols with no trades, metrics helper returns the
        # degenerate all-nan variant naturally.
        per_symbol_metrics: dict[str, Mapping[str, float]] = {}
        for sym in symbols:
            sym_trades = trades_df[trades_df["symbol"] == sym] if "symbol" in trades_df.columns else trades_df.iloc[0:0]
            per_symbol_metrics[sym] = compute_metrics(
                per_symbol_equity[sym],
                sym_trades,
                bars_per_year=bars_per_year,
            )

        return PortfolioResult(
            equity_curve=equity_curve,
            per_symbol_equity=per_symbol_equity,
            trades=trades_df,
            metrics=metrics,
            per_symbol_metrics=per_symbol_metrics,
            engine_name="backtrader",
            params=dict(params),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_size_close_df(
    pos_value_log: list[tuple[pd.Timestamp, dict[str, tuple[float, float]]]],
    symbols: list[str],
    portfolio_index: pd.DatetimeIndex,
    bars_by_symbol: dict[str, BarsData],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (size_df, close_df) aligned on portfolio_index for every symbol.

    close_df falls back to the per-symbol OHLCV close reindexed on the
    portfolio_index (ffill), ensuring continuous closes across bars the
    strategy's next() may have skipped.
    """
    if not pos_value_log:
        zero = pd.DataFrame(0.0, index=portfolio_index, columns=symbols, dtype=float)
        return zero, zero.copy()

    times, maps = zip(*pos_value_log)
    size_rows = []
    close_rows = []
    for m in maps:
        size_rows.append({s: float(m.get(s, (0.0, float("nan")))[0]) for s in symbols})
        close_rows.append({s: float(m.get(s, (0.0, float("nan")))[1]) for s in symbols})
    idx = pd.DatetimeIndex(list(times), name="datetime")
    size_df = pd.DataFrame(size_rows, index=idx)
    close_df = pd.DataFrame(close_rows, index=idx)
    if size_df.index.tz is None:
        size_df.index = size_df.index.tz_localize("UTC")
        close_df.index = close_df.index.tz_localize("UTC")
    size_df = size_df.reindex(portfolio_index).ffill().fillna(0.0)
    close_df = close_df.reindex(portfolio_index).ffill()
    # WHY backfill from OHLCV: pre-listing bars leave close NaN; fill from
    # each symbol's OHLCV so close_df is continuous after listing, and the
    # diff() stays zero before (NaN*0 = NaN → handled by fillna).
    for sym in symbols:
        ohlcv_close = bars_by_symbol[sym].ohlcv["close"].reindex(portfolio_index).ffill()
        close_df[sym] = close_df[sym].fillna(ohlcv_close)
    close_df = close_df.fillna(0.0)
    return size_df, close_df


_TRADES_EMPTY_DTYPES: dict[str, str] = {
    "symbol": "string",
    "entry_time": "datetime64[ns, UTC]",
    "exit_time": "datetime64[ns, UTC]",
    "side": "int8",
    "qty": "float64",
    "entry_price": "float64",
    "exit_price": "float64",
    "gross_pnl": "float64",
    "fees": "float64",
    "slippage": "float64",
    "funding_cost": "float64",
    "net_pnl": "float64",
    "bars_held": "int64",
    "exit_reason": "string",
}


def _build_portfolio_trades(
    records: list[_TradeRecord],
    bars_by_symbol: dict[str, BarsData],
    costs: CostModel,
    slip_bps_by_symbol: dict[str, pd.Series],
) -> pd.DataFrame:
    """Convert closed trade records into the PortfolioResult trades schema."""
    if not records:
        return pd.DataFrame(columns=list(_TRADES_EMPTY_DTYPES)).astype(_TRADES_EMPTY_DTYPES)

    base_bps = costs.slippage_bps_base
    rows = []
    for rec in records:
        sym = rec.symbol
        entry_time = rec.entry_time
        exit_time = rec.exit_time
        side = rec.side
        qty = rec.qty
        entry_price = rec.entry_price
        exit_price = rec.exit_price

        if not (np.isnan(entry_price) or np.isnan(exit_price)) and qty > 0:
            gross_pnl = (exit_price - entry_price) * qty * side
        else:
            gross_pnl = rec.bt_pnl

        fees = rec.entry_commission + rec.exit_commission

        entry_base_slip = abs(entry_price * qty) * base_bps * 1e-4 if not np.isnan(entry_price) else 0.0
        exit_base_slip = abs(exit_price * qty) * base_bps * 1e-4 if not np.isnan(exit_price) else 0.0

        entry_extra_slip = 0.0
        exit_extra_slip = 0.0
        slip_series = slip_bps_by_symbol.get(sym)
        if slip_series is not None:
            if entry_time is not None and entry_time in slip_series.index:
                total_bps = float(slip_series.loc[entry_time])
                extra_bps = max(0.0, total_bps - base_bps)
                entry_extra_slip = abs(entry_price * qty) * extra_bps * 1e-4
            if exit_time is not None and exit_time in slip_series.index:
                total_bps = float(slip_series.loc[exit_time])
                extra_bps = max(0.0, total_bps - base_bps)
                exit_extra_slip = abs(exit_price * qty) * extra_bps * 1e-4 if not np.isnan(exit_price) else 0.0

        slippage = entry_base_slip + exit_base_slip + entry_extra_slip + exit_extra_slip

        funding_cost_val = 0.0
        bars = bars_by_symbol.get(sym)
        if bars is not None and bars.funding is not None and entry_time is not None and exit_time is not None:
            signed_notional = side * qty * entry_price if not np.isnan(entry_price) else 0.0
            funding_cost_val = funding_cost_for_position(
                bars.funding, entry_time, exit_time, signed_notional
            )

        net_pnl = gross_pnl - fees - slippage - funding_cost_val

        rows.append({
            "symbol": sym,
            "entry_time": entry_time,
            "exit_time": exit_time,
            "side": side,
            "qty": qty,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "gross_pnl": gross_pnl,
            "fees": fees,
            "slippage": slippage,
            "funding_cost": funding_cost_val,
            "net_pnl": net_pnl,
            "bars_held": rec.bars_held,
            "exit_reason": rec.exit_reason,
        })

    df = pd.DataFrame(rows)
    df["symbol"] = df["symbol"].astype("string")
    df["entry_time"] = pd.to_datetime(df["entry_time"], utc=True)
    df["exit_time"] = pd.to_datetime(df["exit_time"], utc=True)
    df["side"] = df["side"].astype("int8")
    df["qty"] = df["qty"].astype("float64")
    df["entry_price"] = df["entry_price"].astype("float64")
    df["exit_price"] = df["exit_price"].astype("float64")
    df["gross_pnl"] = df["gross_pnl"].astype("float64")
    df["fees"] = df["fees"].astype("float64")
    df["slippage"] = df["slippage"].astype("float64")
    df["funding_cost"] = df["funding_cost"].astype("float64")
    df["net_pnl"] = df["net_pnl"].astype("float64")
    df["bars_held"] = df["bars_held"].astype("int64")
    df["exit_reason"] = df["exit_reason"].astype("string")

    # Place 'symbol' first
    cols = ["symbol"] + [c for c in df.columns if c != "symbol"]
    return df[cols]


__all__ = ["PortfolioBacktraderEngine"]
