"""Strategy C — Volatility Compression Breakout.

Implements the FROZEN spec at ``docs/strategies/C-vol-compression.md``.

Thesis: a Bollinger-Band-inside-Keltner-Channel "squeeze" flags impending
breakout. Squeeze release + RSI divergence picks the direction.

Implementation notes
--------------------
* ATR is computed **per bar** using Wilder smoothing, *not* the daily ATR
  helper in ``kairos.backtest.costs`` (that one is for cost modelling).
* RSI uses Wilder smoothing (``alpha = 1/n``).
* EMA is seeded via pandas ``ewm(span=n, adjust=False)``.
* All rolling computations reference only bars with timestamp <= current bar
  (no look-ahead); the release condition uses the *previous* bar's squeeze
  state and the *current* bar's post-release state, which both satisfy the
  no-look-ahead invariant.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd

from kairos.backtest.protocols import BarsData, SignalFrame


# ---------------------------------------------------------------------------
# Indicators
# ---------------------------------------------------------------------------

def _wilder_atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int) -> pd.Series:
    """Per-bar Wilder ATR. Uses alpha = 1/n EWM smoothing on True Range.

    True Range at bar t = max(high-low, |high - prev_close|, |low - prev_close|).
    The first bar's TR is (high - low) since prev_close is NaN.
    """
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            (high - low),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    # Wilder smoothing: EWM with alpha = 1/n, adjust=False gives the recursive
    # ATR_t = ATR_{t-1} + (TR_t - ATR_{t-1}) / n seeded by the first TR value.
    atr = tr.ewm(alpha=1.0 / n, adjust=False).mean()
    return atr


def _wilder_rsi(close: pd.Series, n: int) -> pd.Series:
    """Wilder RSI. alpha = 1/n. Returns values in [0, 100]."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / n, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / n, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - 100.0 / (1.0 + rs)
    # When avg_loss == 0 and avg_gain > 0, RSI is 100. When both are 0, keep NaN.
    rsi = rsi.where(~((avg_loss == 0.0) & (avg_gain > 0.0)), 100.0)
    return rsi


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------

class VolCompressionStrategy:
    """Volatility Compression Breakout — conforms to ``Strategy`` protocol."""

    name: str = "vol_compression"
    default_params: Mapping[str, object] = {
        "bb_window": 20,
        "bb_std": 2.0,
        "kc_window": 20,
        "kc_mult": 1.5,
        "rsi_window": 14,
        "divergence_lookback": 20,
        "holding_bars": 240,
    }

    def generate_signals(
        self,
        data: BarsData,
        params: Mapping[str, object],
    ) -> SignalFrame:
        merged: dict[str, Any] = {**self.default_params, **dict(params)}
        bb_window = int(merged["bb_window"])
        bb_std = float(merged["bb_std"])
        kc_window = int(merged["kc_window"])
        kc_mult = float(merged["kc_mult"])
        rsi_window = int(merged["rsi_window"])
        divergence_lookback = int(merged["divergence_lookback"])
        holding_bars = int(merged["holding_bars"])

        ohlcv = data.ohlcv
        idx = ohlcv.index
        n_bars = len(idx)

        close = ohlcv["close"].astype("float64")
        high = ohlcv["high"].astype("float64")
        low = ohlcv["low"].astype("float64")

        # --- Bollinger Band ------------------------------------------------
        mid = close.rolling(bb_window, min_periods=bb_window).mean()
        # ddof=0 for population std to match typical BB convention across libs;
        # either ddof matches the frozen spec as long as it's deterministic.
        std = close.rolling(bb_window, min_periods=bb_window).std(ddof=0)
        upper_bb = mid + bb_std * std
        lower_bb = mid - bb_std * std

        # --- Keltner Channel ----------------------------------------------
        ema_mid = close.ewm(span=kc_window, adjust=False).mean()
        atr = _wilder_atr(high, low, close, kc_window)
        upper_kc = ema_mid + kc_mult * atr
        lower_kc = ema_mid - kc_mult * atr

        # --- Squeeze state -------------------------------------------------
        squeeze = (upper_bb < upper_kc) & (lower_bb > lower_kc)
        # Squeeze release at bar T: squeeze[T-1] True AND squeeze[T] False.
        prev_squeeze = squeeze.shift(1, fill_value=False)
        release = prev_squeeze & (~squeeze)

        # --- RSI ----------------------------------------------------------
        rsi = _wilder_rsi(close, rsi_window)

        # --- Build signal array ------------------------------------------
        close_np = close.to_numpy()
        mid_np = mid.to_numpy()
        rsi_np = rsi.to_numpy()
        release_np = release.to_numpy()

        side = np.zeros(n_bars, dtype=np.int8)

        # Position state across the timeline.
        in_pos = 0  # -1 / 0 / +1
        entry_bar = -1

        for t in range(n_bars):
            if np.isnan(mid_np[t]):
                continue

            # --- Exit logic (evaluated first so we can re-enter elsewhere
            # on the same bar only via a fresh release; spec does not require
            # immediate re-entry, so exits take precedence on the exit bar).
            if in_pos != 0:
                held = t - entry_bar
                flatten = False
                if held >= holding_bars:
                    flatten = True
                else:
                    # Opposite mid cross.
                    if in_pos == 1 and close_np[t] < mid_np[t]:
                        flatten = True
                    elif in_pos == -1 and close_np[t] > mid_np[t]:
                        flatten = True
                if flatten:
                    in_pos = 0
                    entry_bar = -1
                    side[t] = 0
                else:
                    side[t] = in_pos
                    continue  # already in position; do not re-enter this bar

            # --- Entry logic (only when flat) --------------------------------
            if in_pos == 0 and release_np[t]:
                # Divergence window: last `divergence_lookback` bars ending at t
                # (inclusive). Use close for price extremes and rsi for indicator.
                start = max(0, t - divergence_lookback + 1)
                end = t + 1  # exclusive
                window_close = close_np[start:end]
                window_rsi = rsi_np[start:end]
                if len(window_close) >= 2 and not np.any(np.isnan(window_rsi)):
                    # Bullish divergence: bar of window-min close has RSI that
                    # is NOT the window-min RSI.
                    argmin_price = int(np.argmin(window_close))
                    argmin_rsi = int(np.argmin(window_rsi))
                    bullish_div = argmin_price != argmin_rsi and (
                        window_rsi[argmin_price] > window_rsi[argmin_rsi]
                    )
                    # Bearish divergence: bar of window-max close has RSI that
                    # is NOT the window-max RSI.
                    argmax_price = int(np.argmax(window_close))
                    argmax_rsi = int(np.argmax(window_rsi))
                    bearish_div = argmax_price != argmax_rsi and (
                        window_rsi[argmax_price] < window_rsi[argmax_rsi]
                    )
                    # Direction-consistent mid cross on the release bar.
                    if bullish_div and close_np[t] > mid_np[t]:
                        in_pos = 1
                        entry_bar = t
                        side[t] = 1
                    elif bearish_div and close_np[t] < mid_np[t]:
                        in_pos = -1
                        entry_bar = t
                        side[t] = -1

        # --- Assemble SignalFrame -----------------------------------------
        df = pd.DataFrame(
            {
                "side": side,
                "size_hint": np.ones(n_bars, dtype=np.float64),
                "stop": np.full(n_bars, np.nan, dtype=np.float64),
                "target": np.full(n_bars, np.nan, dtype=np.float64),
            },
            index=idx,
        )
        df["side"] = df["side"].astype(np.int8)
        return SignalFrame(df=df)


__all__ = ["VolCompressionStrategy"]
