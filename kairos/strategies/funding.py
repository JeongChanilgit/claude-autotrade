"""Strategy B — Funding Counter.

Frozen spec implementation. See `docs/strategies/B-funding-counter.md`.

Thesis: persistent positive funding -> longs crowded -> mean-revert short.
Persistent negative funding -> shorts crowded -> mean-revert long.
Use 3-consecutive-funding-reading trigger with OI change confirmation and
SMA-based entry timing. Flatten on opposite bias or after holding_bars.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd

from kairos.backtest.protocols import BarsData, SignalFrame


class FundingCounterStrategy:
    """Funding-rate mean-reversion strategy (Strategy B).

    Conforms to ``kairos.backtest.protocols.Strategy``. Deterministic, no
    look-ahead, no RNG. Requires both ``data.funding`` and ``data.metrics``.
    """

    name: str = "funding_counter"
    default_params: Mapping[str, object] = {
        "funding_threshold": 0.0005,
        "consecutive_count": 3,
        "oi_change_min": 0.02,
        "entry_sma_window": 60,
        "holding_bars": 480,
    }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def generate_signals(
        self,
        data: BarsData,
        params: Mapping[str, object],
    ) -> SignalFrame:
        if data.funding is None:
            raise ValueError(
                "FundingCounterStrategy requires data.funding (got None); "
                "provide a DataFrame with a 'funding_rate' column."
            )
        if data.metrics is None:
            raise ValueError(
                "FundingCounterStrategy requires data.metrics (got None); "
                "provide a DataFrame with a 'sum_open_interest' column."
            )

        merged = {**self.default_params, **dict(params)}
        funding_threshold = float(merged["funding_threshold"])
        consecutive_count = int(merged["consecutive_count"])
        oi_change_min = float(merged["oi_change_min"])
        entry_sma_window = int(merged["entry_sma_window"])
        holding_bars = int(merged["holding_bars"])

        ohlcv = data.ohlcv
        index = ohlcv.index
        n = len(index)

        # ----- Funding bias: latest `consecutive_count` readings -------
        # Work on the native (sparse, 8h) funding timeline so "last N readings"
        # is unambiguous and independent of the ohlcv cadence. Then align via
        # reindex + ffill onto the ohlcv index — no look-ahead because at any
        # ohlcv timestamp T we only use funding rows whose funding_time <= T.
        funding_rate = data.funding["funding_rate"].astype(float).sort_index()

        pos_streak = (funding_rate > funding_threshold).astype(int)
        neg_streak = (funding_rate < -funding_threshold).astype(int)
        # Rolling "all of last N are 1" => sum == N.
        pos_run = pos_streak.rolling(consecutive_count, min_periods=consecutive_count).sum()
        neg_run = neg_streak.rolling(consecutive_count, min_periods=consecutive_count).sum()

        bias_native = pd.Series(0, index=funding_rate.index, dtype="int8")
        bias_native[pos_run == consecutive_count] = -1  # short bias
        bias_native[neg_run == consecutive_count] = 1   # long bias

        # Align onto ohlcv index. reindex + ffill gives the most recent
        # funding-time value at each bar; before the first funding reading the
        # value is 0 (no bias).
        bias_aligned = (
            bias_native.reindex(index).ffill().fillna(0).astype(np.int8)
        )

        # ----- OI 24h change rate from metrics -------------------------
        metrics = data.metrics.sort_index()
        oi = metrics["sum_open_interest"].astype(float)

        # Compute 24h change on the native (5m) metrics index so the window is
        # time-correct regardless of gaps, then align to ohlcv.
        oi_24h_ago = oi.reindex(oi.index - pd.Timedelta(hours=24), method="ffill")
        oi_24h_ago.index = oi.index
        with np.errstate(divide="ignore", invalid="ignore"):
            oi_change_native = (oi - oi_24h_ago) / oi_24h_ago
        oi_change_native = oi_change_native.replace([np.inf, -np.inf], np.nan)

        oi_change_aligned = oi_change_native.reindex(index).ffill()

        # Confirmation: bias only valid when |24h OI change| >= oi_change_min.
        oi_ok = oi_change_aligned.abs() >= oi_change_min
        oi_ok = oi_ok.fillna(False).to_numpy()

        bias_arr = bias_aligned.to_numpy()
        bias_confirmed = np.where(oi_ok, bias_arr, 0).astype(np.int8)

        # ----- Entry timing via SMA ------------------------------------
        close = ohlcv["close"].astype(float)
        sma = close.rolling(entry_sma_window, min_periods=entry_sma_window).mean()
        close_arr = close.to_numpy()
        sma_arr = sma.to_numpy()

        # Short entry: bias_confirmed == -1 AND close > SMA
        # Long entry:  bias_confirmed == +1 AND close < SMA
        short_entry = (bias_confirmed == -1) & (close_arr > sma_arr)
        long_entry = (bias_confirmed == 1) & (close_arr < sma_arr)
        # NaN SMA (not enough history) => no entry condition satisfied.
        not_nan_sma = ~np.isnan(sma_arr)
        short_entry &= not_nan_sma
        long_entry &= not_nan_sma

        # ----- State machine: side, holding cap, opposite-bias flatten --
        side = np.zeros(n, dtype=np.int8)
        pos = 0            # current position side: -1, 0, +1
        bars_held = 0      # bars in current position (0 when flat)

        for i in range(n):
            b = int(bias_confirmed[i])
            forced_exit = False  # opposite-bias or holding-cap exit this bar

            # (a) Opposite-bias flatten. If we hold and a confirmed opposite
            # bias is present, flatten this bar. Per spec GC-B5, the flattened
            # bar stays flat (re-entry, if conditions are met, would happen
            # on a subsequent bar).
            if pos != 0 and b != 0 and b != pos:
                pos = 0
                bars_held = 0
                forced_exit = True

            # (b) Holding cap. Spec GC-B4: "481번째 봉부터 side=0", i.e. once
            # the position has been held for `holding_bars` bars (inclusive of
            # the entry bar), the next bar is flat. The flattened bar stays
            # flat even if entry conditions still hold.
            if pos != 0 and bars_held >= holding_bars:
                pos = 0
                bars_held = 0
                forced_exit = True

            # (c) Entry (only if flat AND we did not just forcibly exit this
            # bar). Disallowing same-bar re-entry after a forced exit gives
            # an observable flat bar per the spec's golden cases.
            if pos == 0 and not forced_exit:
                if short_entry[i]:
                    pos = -1
                    bars_held = 1
                elif long_entry[i]:
                    pos = 1
                    bars_held = 1
                else:
                    bars_held = 0
            elif pos != 0:
                bars_held += 1

            side[i] = pos

        df = pd.DataFrame(
            {
                "side": side,
                "size_hint": np.ones(n, dtype=np.float64),
                "stop": np.full(n, np.nan, dtype=np.float64),
                "target": np.full(n, np.nan, dtype=np.float64),
            },
            index=index,
        )
        # Enforce dtypes explicitly.
        df["side"] = df["side"].astype(np.int8)
        df["size_hint"] = df["size_hint"].astype(np.float64)
        df["stop"] = df["stop"].astype(np.float64)
        df["target"] = df["target"].astype(np.float64)

        return SignalFrame(df=df)


__all__ = ["FundingCounterStrategy"]
