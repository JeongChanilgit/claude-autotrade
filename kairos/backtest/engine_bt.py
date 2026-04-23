"""Backtrader engine adapter for the Kairos backtest layer.

Conforms to the Engine protocol defined in kairos.backtest.protocols.
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
    BacktestResult,
    BarsData,
    CostModel,
    EngineName,
    SignalFrame,
    Strategy,
    SymbolSpec,
)
from kairos.backtest.quantize import meets_min_notional, quantize_price, quantize_qty

log = logging.getLogger("kairos.backtest.engine_bt")


# ---------------------------------------------------------------------------
# PandasData feed
# ---------------------------------------------------------------------------

class _KairosFeed(bt.feeds.PandasData):
    """PandasData subclass that maps our OHLCV column names."""

    params = (
        ("datetime", None),    # index is DatetimeIndex
        ("open", "open"),
        ("high", "high"),
        ("low", "low"),
        ("close", "close"),
        ("volume", "volume"),
        ("openinterest", -1),  # not present
    )


# ---------------------------------------------------------------------------
# Order completion record (collected from notify_order)
# ---------------------------------------------------------------------------

class _OrderFill:
    """Snapshot of one completed order fill."""

    __slots__ = ("ref", "is_buy", "price", "size", "commission", "dt", "exectype")

    def __init__(
        self,
        ref: int,
        is_buy: bool,
        price: float,
        size: float,
        commission: float,
        dt: pd.Timestamp,
        exectype: int,
    ) -> None:
        self.ref = ref
        self.is_buy = is_buy
        self.price = price
        self.size = size
        self.commission = commission
        self.dt = dt
        self.exectype = exectype


# ---------------------------------------------------------------------------
# Closed trade record
# ---------------------------------------------------------------------------

class _TradeRecord:
    """Immutable-enough container for one closed round-trip."""

    __slots__ = (
        "side", "qty", "entry_price", "exit_price",
        "entry_time", "exit_time", "entry_commission", "exit_commission",
        "bars_held", "exit_reason", "bt_pnl",
    )

    def __init__(self) -> None:
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
# backtrader Strategy — reads pre-computed signals
# ---------------------------------------------------------------------------

class _SignalStrategy(bt.Strategy):
    """Reads a pre-computed signal series and places Market orders next bar."""

    params = (
        ("signals_df", None),
        ("symbol_spec", None),
        ("initial_cash", 0.0),
        ("cost_model", None),
    )

    def __init__(self) -> None:
        self._signals: pd.DataFrame = self.params.signals_df
        self._spec: SymbolSpec = self.params.symbol_spec
        self._initial_cash: float = self.params.initial_cash

        # Pre-build a mapping: naive datetime → (side, size_hint, stop, target)
        # The signals_df index is UTC-aware; build a dict keyed by naive timestamps
        # for O(1) lookup inside next().
        self._sig_map: dict[Any, tuple[int, float, float, float]] = {}
        for ts, row in self._signals.iterrows():
            naive_key = ts.replace(tzinfo=None)
            self._sig_map[naive_key] = (
                int(row["side"]),
                float(row["size_hint"]),
                float(row["stop"]) if not pd.isna(row["stop"]) else float("nan"),
                float(row["target"]) if not pd.isna(row["target"]) else float("nan"),
            )

        # Trade tracking
        self._fills: list[_OrderFill] = []
        self.closed_records: list[_TradeRecord] = []

        # Track open trade ref → entry fill
        self._open_entry_fill: _OrderFill | None = None
        self._open_trade_side: int = 0

        # Stop/target order refs for exit_reason detection
        self._stop_ref: int | None = None
        self._target_ref: int | None = None

        # Equity log: list of (UTC Timestamp, float)
        self.equity_log: list[tuple[pd.Timestamp, float]] = []

        # Previous bar target side
        self._prev_side: int = 0

        # Reversal state: after close completes, open this side
        self._reversal_target: int = 0
        self._reversal_size_hint: float = 1.0

    def _get_signal(self, naive_dt: Any) -> tuple[int, float, float, float]:
        """Return (side, size_hint, stop, target) for a naive datetime."""
        sig = self._sig_map.get(naive_dt)
        if sig is not None:
            return sig
        return (0, 1.0, float("nan"), float("nan"))

    def _compute_qty(self, price: float, size_hint: float) -> float:
        """Compute order quantity using available cash and symbol spec."""
        if price <= 0:
            return 0.0
        cash = self.broker.getcash()
        raw_qty = (cash * min(size_hint, 1.0)) / price
        q_price = quantize_price(price, 1, self._spec)
        q_qty = quantize_qty(raw_qty, self._spec)
        if q_qty == Decimal("0"):
            return 0.0
        if not meets_min_notional(q_price, q_qty, self._spec):
            return 0.0
        return float(q_qty)

    def next(self) -> None:
        dt = self.data.datetime.datetime(0)  # naive UTC datetime

        # Record equity at start of bar
        ts_aware = pd.Timestamp(dt, tz="UTC")
        self.equity_log.append((ts_aware, self.broker.getvalue()))

        target_side, size_hint, stop, target = self._get_signal(dt)
        cur_pos = self.getposition()
        has_pos = cur_pos.size != 0

        side_changed = target_side != self._prev_side

        if side_changed:
            # Cancel all pending conditional orders on signal change
            for o in self.broker.get_orders_open():
                self.cancel(o)
            self._stop_ref = None
            self._target_ref = None

        # --- Position management ---
        if target_side == 0 and has_pos:
            # Flatten
            self._reversal_target = 0
            self.close()

        elif target_side == 1 and not has_pos:
            # Open long
            qty = self._compute_qty(self.data.close[0], size_hint)
            if qty > 0:
                self.buy(size=qty)

        elif target_side == -1 and not has_pos:
            # Open short
            qty = self._compute_qty(self.data.close[0], size_hint)
            if qty > 0:
                self.sell(size=qty)

        elif target_side == 1 and has_pos and cur_pos.size < 0:
            # Reverse short → long
            self._reversal_target = 1
            self._reversal_size_hint = size_hint
            self.close()

        elif target_side == -1 and has_pos and cur_pos.size > 0:
            # Reverse long → short
            self._reversal_target = -1
            self._reversal_size_hint = size_hint
            self.close()

        self._prev_side = target_side

        # --- Place conditional orders for existing position ---
        if has_pos and not side_changed:
            if self._stop_ref is None and not np.isnan(stop):
                if cur_pos.size > 0:
                    o = self.sell(size=abs(cur_pos.size), exectype=bt.Order.Stop, price=stop)
                else:
                    o = self.buy(size=abs(cur_pos.size), exectype=bt.Order.Stop, price=stop)
                self._stop_ref = o.ref

            if self._target_ref is None and not np.isnan(target):
                if cur_pos.size > 0:
                    o = self.sell(size=abs(cur_pos.size), exectype=bt.Order.Limit, price=target)
                else:
                    o = self.buy(size=abs(cur_pos.size), exectype=bt.Order.Limit, price=target)
                self._target_ref = o.ref

    def notify_order(self, order: bt.Order) -> None:
        if order.status != order.Completed:
            return

        dt_num = order.executed.dt
        exec_dt = pd.Timestamp(bt.num2date(dt_num), tz="UTC")
        exec_price = float(order.executed.price)
        exec_size = float(abs(order.executed.size))
        exec_comm = float(abs(order.executed.comm))

        # Determine exit reason
        exit_reason = "signal"
        if order.ref == self._stop_ref:
            exit_reason = "stop"
            self._stop_ref = None
            self._target_ref = None
        elif order.ref == self._target_ref:
            exit_reason = "target"
            self._target_ref = None
            self._stop_ref = None

        fill = _OrderFill(
            ref=order.ref,
            is_buy=order.isbuy(),
            price=exec_price,
            size=exec_size,
            commission=exec_comm,
            dt=exec_dt,
            exectype=order.exectype,
        )
        self._fills.append(fill)

        cur_pos_size = self.getposition().size

        # If a close/reversal just completed, fire the reversal open leg
        if self._reversal_target != 0 and cur_pos_size == 0:
            new_side = self._reversal_target
            sh = self._reversal_size_hint
            self._reversal_target = 0
            close_price = self.data.close[0]
            qty = self._compute_qty(close_price, sh)
            if qty > 0:
                if new_side == 1:
                    self.buy(size=qty)
                else:
                    self.sell(size=qty)

    def notify_trade(self, trade: bt.Trade) -> None:
        if not trade.isclosed:
            return

        rec = _TradeRecord()
        rec.side = 1 if trade.long else -1
        rec.bt_pnl = float(trade.pnl)
        rec.bars_held = int(trade.barclose - trade.baropen)

        # Reconstruct timestamps from trade floats
        open_dt = pd.Timestamp(bt.num2date(trade.dtopen), tz="UTC")
        close_dt = pd.Timestamp(bt.num2date(trade.dtclose), tz="UTC")
        rec.entry_time = open_dt
        rec.exit_time = close_dt

        # Match entry fill: first fill at open_dt in the trade's direction
        entry_is_buy = (rec.side == 1)
        exit_is_buy = not entry_is_buy

        entry_fills = [
            f for f in self._fills
            if f.dt == open_dt and f.is_buy == entry_is_buy
        ]
        exit_fills = [
            f for f in self._fills
            if f.dt == close_dt and f.is_buy == exit_is_buy
        ]

        if entry_fills:
            ef = entry_fills[-1]
            rec.entry_price = ef.price
            rec.qty = ef.size
            rec.entry_commission = ef.commission
        else:
            rec.entry_price = float(trade.price) if trade.price else float("nan")
            rec.qty = 0.0
            rec.entry_commission = 0.0

        if exit_fills:
            xf = exit_fills[-1]
            rec.exit_price = xf.price
            rec.exit_commission = xf.commission
            rec.exit_reason = xf.exectype and (
                "stop" if xf.exectype == bt.Order.Stop else
                "target" if xf.exectype == bt.Order.Limit else
                "signal"
            ) or "signal"
        else:
            rec.exit_price = float("nan")
            rec.exit_commission = 0.0
            rec.exit_reason = "signal"

        # Check whether exit was stop or target from tracked refs
        # (already encoded into fill.exectype above)

        self.closed_records.append(rec)


# ---------------------------------------------------------------------------
# Engine adapter
# ---------------------------------------------------------------------------

class BacktraderEngine:
    """Backtrader engine adapter conforming to Engine protocol."""

    name: EngineName = "backtrader"

    def run(
        self,
        strategy: Strategy,
        data: BarsData,
        params: Mapping[str, object],
        costs: CostModel,
        symbol_spec: SymbolSpec,
        initial_cash: Decimal,
    ) -> BacktestResult:
        log.info(
            "BacktraderEngine.run: symbol=%s interval=%s bars=%d",
            data.symbol,
            data.interval,
            len(data.ohlcv),
        )

        # 1. Generate signals; validate index alignment
        signal_frame: SignalFrame = strategy.generate_signals(data, params)
        signals_df = signal_frame.df

        if not signals_df.index.equals(data.ohlcv.index):
            raise ValueError(
                "SignalFrame index does not match BarsData.ohlcv index. "
                f"Signal len={len(signals_df)}, OHLCV len={len(data.ohlcv)}"
            )

        # 2. Pre-compute per-bar slippage bps (for post-hoc extra slippage)
        atr_ratio = daily_atr_per_close(data.ohlcv)
        slip_bps_series = slippage_bps(costs, atr_ratio)

        base_slip_frac = costs.slippage_bps_base * 1e-4
        taker_fee_frac = costs.taker_fee_bps * 1e-4

        # 3. Build feed — strip tz from index (backtrader requires tz-naive)
        ohlcv_naive = data.ohlcv[["open", "high", "low", "close", "volume"]].copy()
        if getattr(ohlcv_naive.index, "tz", None) is not None:
            ohlcv_naive.index = ohlcv_naive.index.tz_localize(None)
        ohlcv_naive.index.name = "datetime"

        # Determine timeframe
        interval = data.interval
        if interval == "1m":
            tf = bt.TimeFrame.Minutes
            compression = 1
            bars_per_year = 525_600
        else:  # 15m
            tf = bt.TimeFrame.Minutes
            compression = 15
            bars_per_year = 35_040

        bt_feed = _KairosFeed(
            dataname=ohlcv_naive,
            timeframe=tf,
            compression=compression,
        )

        # 4. Cerebro setup
        cerebro = bt.Cerebro()
        cerebro.adddata(bt_feed)

        initial_cash_f = float(initial_cash)
        cerebro.broker.setcash(initial_cash_f)

        # Commission: taker fee as fraction of notional
        cerebro.broker.setcommission(
            commission=taker_fee_frac,
            margin=None,
            mult=1.0,
            commtype=bt.CommInfoBase.COMM_PERC,
        )

        # Base slippage at broker level (constant floor)
        cerebro.broker.set_slippage_perc(perc=base_slip_frac)

        # Add strategy
        cerebro.addstrategy(
            _SignalStrategy,
            signals_df=signals_df,
            symbol_spec=symbol_spec,
            initial_cash=initial_cash_f,
            cost_model=costs,
        )

        # 5. Run
        bt_results = cerebro.run()
        strat: _SignalStrategy = bt_results[0]

        # 6. Build equity curve from strategy's equity_log
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
            equity_curve = pd.Series(
                [initial_cash_f],
                index=data.ohlcv.index[:1],
                name="equity",
                dtype=float,
            )

        # Align equity curve length to OHLCV
        equity_curve = equity_curve.reindex(data.ohlcv.index).ffill().bfill()

        # 7. Build trades DataFrame
        trades_df = _build_trades_df(
            strat.closed_records,
            data,
            costs,
            slip_bps_series,
            taker_fee_frac,
        )

        # 8. Metrics
        metrics = compute_metrics(
            equity_curve,
            trades_df,
            bars_per_year=bars_per_year,
        )

        return BacktestResult(
            equity_curve=equity_curve,
            trades=trades_df,
            metrics=metrics,
            engine_name="backtrader",
            params=dict(params),
        )


# ---------------------------------------------------------------------------
# Trades DataFrame builder
# ---------------------------------------------------------------------------

_EMPTY_TRADES_DTYPES: dict[str, str] = {
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


def _build_trades_df(
    records: list[_TradeRecord],
    data: BarsData,
    costs: CostModel,
    slip_bps_series: pd.Series,
    taker_fee_frac: float,
) -> pd.DataFrame:
    """Convert _TradeRecord list to the BacktestResult trades schema."""

    if not records:
        return pd.DataFrame(columns=list(_EMPTY_TRADES_DTYPES)).astype(_EMPTY_TRADES_DTYPES)

    base_bps = costs.slippage_bps_base
    rows = []

    for rec in records:
        entry_time = rec.entry_time
        exit_time = rec.exit_time
        side = rec.side
        qty = rec.qty
        entry_price = rec.entry_price
        exit_price = rec.exit_price

        # Gross PnL = (exit - entry) * qty * side (no costs)
        if not (np.isnan(entry_price) or np.isnan(exit_price)) and qty > 0:
            gross_pnl = (exit_price - entry_price) * qty * side
        else:
            gross_pnl = rec.bt_pnl

        # Fees from broker commissions recorded in the fill
        fees = rec.entry_commission + rec.exit_commission

        # If broker commission was zero (zero-cost run), fees stay at 0
        # so we don't double-count — broker already applied fee to fill price.

        # --- Slippage ---
        # The broker's set_slippage_perc already baked base_bps into execution prices.
        # We report slippage = base component (both legs) + extra vol component post-hoc.
        entry_base_slip = abs(entry_price * qty) * base_bps * 1e-4 if not np.isnan(entry_price) else 0.0
        exit_base_slip = abs(exit_price * qty) * base_bps * 1e-4 if not np.isnan(exit_price) else 0.0

        # Extra variable slippage (vol-dependent beyond base_bps)
        entry_extra_slip = 0.0
        exit_extra_slip = 0.0
        if entry_time is not None and entry_time in slip_bps_series.index:
            total_bps = float(slip_bps_series.loc[entry_time])
            extra_bps = max(0.0, total_bps - base_bps)
            entry_extra_slip = abs(entry_price * qty) * extra_bps * 1e-4

        if exit_time is not None and exit_time in slip_bps_series.index:
            total_bps = float(slip_bps_series.loc[exit_time])
            extra_bps = max(0.0, total_bps - base_bps)
            exit_extra_slip = abs(exit_price * qty) * extra_bps * 1e-4 if not np.isnan(exit_price) else 0.0

        slippage = entry_base_slip + exit_base_slip + entry_extra_slip + exit_extra_slip

        # Funding cost
        funding_cost_val = 0.0
        if data.funding is not None and entry_time is not None and exit_time is not None:
            signed_notional = side * qty * entry_price if not np.isnan(entry_price) else 0.0
            funding_cost_val = funding_cost_for_position(
                data.funding, entry_time, exit_time, signed_notional
            )

        net_pnl = gross_pnl - fees - slippage - funding_cost_val

        rows.append({
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

    return df


__all__ = ["BacktraderEngine"]
