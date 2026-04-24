"""Strategy M — Supertrend Follower.

FROZEN implementation conforming to ``docs/strategies/M-supertrend.md``.
Deterministic, no look-ahead, no risk/sizing (size_hint fixed to 1.0).

Classic Supertrend with Wilder ATR and a state-carrying band/direction loop.
Entering a bar with a direction flip is modeled by emitting the new direction
at the flip bar itself. A holding-bars cap forces a flat without changing
direction bookkeeping beyond the cap bar.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd

from kairos.backtest.protocols import BarsData, SignalFrame


def _wilder_atr(
    high: pd.Series, low: pd.Series, close: pd.Series, n: int
) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            (high - low),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    # Wilder smoothing == ewm(alpha=1/n, adjust=False).
    atr = tr.ewm(alpha=1.0 / n, adjust=False).mean()
    # Mask the first `n` bars — ATR is considered unstable until then.
    atr.iloc[: n] = np.nan
    return atr


class SupertrendStrategy:
    name: str = "supertrend"
    default_params: Mapping[str, Any] = {
        "atr_window": 10,
        "atr_mult": 3.0,
        "holding_bars": 96,
    }

    def generate_signals(
        self, data: BarsData, params: Mapping[str, Any]
    ) -> SignalFrame:
        merged: dict[str, Any] = {**self.default_params, **dict(params)}
        atr_window = int(merged["atr_window"])
        atr_mult = float(merged["atr_mult"])
        holding_bars = int(merged["holding_bars"])

        ohlcv = data.ohlcv
        idx = ohlcv.index
        n = len(idx)

        empty = pd.DataFrame(
            {
                "side": np.zeros(n, dtype=np.int8),
                "size_hint": np.ones(n, dtype=np.float64),
                "stop": np.full(n, np.nan, dtype=np.float64),
                "target": np.full(n, np.nan, dtype=np.float64),
            },
            index=idx,
        )
        if n == 0:
            return SignalFrame(df=empty)

        high = ohlcv["high"].astype(float)
        low = ohlcv["low"].astype(float)
        close = ohlcv["close"].astype(float)
        hl2 = (high + low) / 2.0
        atr = _wilder_atr(high, low, close, atr_window)
        upper_basic = hl2 + atr_mult * atr
        lower_basic = hl2 - atr_mult * atr

        close_np = close.to_numpy()
        ub_np = upper_basic.to_numpy()
        lb_np = lower_basic.to_numpy()

        upper_band = np.full(n, np.nan, dtype=np.float64)
        lower_band = np.full(n, np.nan, dtype=np.float64)
        direction = np.zeros(n, dtype=np.int8)
        side = np.zeros(n, dtype=np.int8)

        prev_dir = 0
        entry_bar = -1  # bar index at which the current direction began.

        for t in range(n):
            # Stable-ATR gate.
            if not np.isfinite(ub_np[t]):
                # Keep all outputs zero/NaN.
                continue

            # Smoothed bands.
            if t == 0 or not np.isfinite(upper_band[t - 1]):
                upper_band[t] = ub_np[t]
                lower_band[t] = lb_np[t]
            else:
                if (
                    ub_np[t] < upper_band[t - 1]
                    or close_np[t - 1] > upper_band[t - 1]
                ):
                    upper_band[t] = ub_np[t]
                else:
                    upper_band[t] = upper_band[t - 1]
                if (
                    lb_np[t] > lower_band[t - 1]
                    or close_np[t - 1] < lower_band[t - 1]
                ):
                    lower_band[t] = lb_np[t]
                else:
                    lower_band[t] = lower_band[t - 1]

            # Direction transition.
            if prev_dir == 0:
                # First stable bar — seed direction from close vs. bands.
                if close_np[t] > upper_band[t]:
                    new_dir = 1
                elif close_np[t] < lower_band[t]:
                    new_dir = -1
                else:
                    new_dir = 0
            elif prev_dir == 1:
                new_dir = -1 if close_np[t] < lower_band[t] else 1
            else:  # prev_dir == -1
                new_dir = 1 if close_np[t] > upper_band[t] else -1

            # Holding cap: if we've held prev_dir for >= holding_bars since
            # entry, force a flat on this bar; direction resets until the
            # next genuine flip.
            if new_dir != 0 and new_dir == prev_dir and entry_bar >= 0:
                held = t - entry_bar
                if held >= holding_bars:
                    direction[t] = 0
                    side[t] = 0
                    prev_dir = 0
                    entry_bar = -1
                    continue

            # Entry-bar bookkeeping — only on a genuine flip (or seed).
            if new_dir != prev_dir and new_dir != 0:
                entry_bar = t

            direction[t] = new_dir
            side[t] = new_dir
            prev_dir = new_dir

        df = pd.DataFrame(
            {
                "side": side.astype(np.int8),
                "size_hint": np.ones(n, dtype=np.float64),
                "stop": np.full(n, np.nan, dtype=np.float64),
                "target": np.full(n, np.nan, dtype=np.float64),
            },
            index=idx,
        )
        return SignalFrame(df=df)


__all__ = ["SupertrendStrategy"]


# --- Registry self-registration -------------------------------------------
from kairos.strategies.registry import ParamField, StrategyEntry, register

register(
    StrategyEntry(
        name="supertrend",
        label="Supertrend Follower",
        factory=lambda: SupertrendStrategy(),
        default_params=SupertrendStrategy.default_params,
        param_schema={
            "atr_window": ParamField(
                type="int", default=10, min=2, max=200,
                help="Wilder ATR 길이",
            ),
            "atr_mult": ParamField(
                type="float", default=3.0, min=0.5, max=10.0,
                help="ATR 배수",
            ),
            "holding_bars": ParamField(
                type="int", default=96, min=1, max=1000,
                help="최대 보유 봉 수(cap)",
            ),
        },
        supports_funding=False,
        supports_metrics=False,
        description=(
            "Wilder ATR 기반 Supertrend 라인의 방향 flip을 따라가는 추세 추종, "
            "반대 flip 또는 보유 상한으로 청산."
        ),
    )
)
