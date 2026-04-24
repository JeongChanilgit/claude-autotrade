"""Strategy N — Inside Bar Breakout.

FROZEN implementation conforming to ``docs/strategies/N-inside-bar-breakout.md``.
Deterministic, no look-ahead, no risk/sizing (size_hint fixed to 1.0).

Detects an inside bar (bar T fully contained in bar T-1's range) and enters
on the next bar (T+1) when close breaks the mother bar's high/low in the
direction of the EMA trend. Exits on EMA re-cross in the opposite direction
or a holding-bar cap.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd

from kairos.backtest.protocols import BarsData, SignalFrame


def _is_inside_bar(
    high: np.ndarray, low: np.ndarray, t: int
) -> bool:
    if t < 1:
        return False
    return bool(high[t] < high[t - 1] and low[t] > low[t - 1])


class InsideBarBreakoutStrategy:
    name: str = "inside_bar_breakout"
    default_params: Mapping[str, Any] = {
        "ema_trend_span": 50,
        "holding_bars": 12,
    }

    def generate_signals(
        self, data: BarsData, params: Mapping[str, Any]
    ) -> SignalFrame:
        merged: dict[str, Any] = {**self.default_params, **dict(params)}
        ema_trend_span = int(merged["ema_trend_span"])
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

        close = ohlcv["close"].astype(float)
        high = ohlcv["high"].astype(float)
        low = ohlcv["low"].astype(float)
        ema_trend = close.ewm(span=ema_trend_span, adjust=False).mean()

        close_np = close.to_numpy()
        high_np = high.to_numpy()
        low_np = low.to_numpy()
        ema_np = ema_trend.to_numpy()

        side = np.zeros(n, dtype=np.int8)
        pos = 0
        entry_bar = -1

        for t in range(n):
            if pos != 0:
                held = t - entry_bar
                flatten = False
                if pos == 1 and np.isfinite(ema_np[t]) and close_np[t] < ema_np[t]:
                    flatten = True
                elif pos == -1 and np.isfinite(ema_np[t]) and close_np[t] > ema_np[t]:
                    flatten = True
                elif held >= holding_bars:
                    flatten = True

                if flatten:
                    pos = 0
                    entry_bar = -1
                    side[t] = 0
                else:
                    side[t] = pos
                    continue

            # Entry: we're at bar t == T+1. Mother bar = t-2, inside bar = t-1.
            if pos == 0 and t >= 2:
                mother = t - 2
                inside = t - 1
                if _is_inside_bar(high_np, low_np, inside) and inside - 1 == mother:
                    if not np.isfinite(ema_np[t]):
                        continue
                    if (
                        close_np[t] > high_np[mother]
                        and close_np[t] > ema_np[t]
                    ):
                        pos = 1
                        entry_bar = t
                        side[t] = 1
                    elif (
                        close_np[t] < low_np[mother]
                        and close_np[t] < ema_np[t]
                    ):
                        pos = -1
                        entry_bar = t
                        side[t] = -1

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


__all__ = ["InsideBarBreakoutStrategy"]


# --- Registry self-registration -------------------------------------------
from kairos.strategies.registry import ParamField, StrategyEntry, register

register(
    StrategyEntry(
        name="inside_bar_breakout",
        label="Inside Bar Breakout",
        factory=lambda: InsideBarBreakoutStrategy(),
        default_params=InsideBarBreakoutStrategy.default_params,
        param_schema={
            "ema_trend_span": ParamField(
                type="int", default=50, min=5, max=500,
                help="추세 EMA 길이",
            ),
            "holding_bars": ParamField(
                type="int", default=12, min=1, max=200,
                help="최대 보유 봉 수",
            ),
        },
        supports_funding=False,
        supports_metrics=False,
        description=(
            "Inside bar 압축 뒤 mother bar 고/저점 돌파 + EMA 추세 방향 필터, "
            "EMA 반대 방향 재돌파/보유 상한으로 청산."
        ),
    )
)
