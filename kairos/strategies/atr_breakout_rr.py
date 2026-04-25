"""Strategy N — ATR Breakout with fixed R:R.

FROZEN implementation conforming to ``docs/strategies/N-atr-breakout-rr.md``.
Deterministic, no look-ahead. Strategy fills stop/target columns and flips
``side`` to 0 on the bar the stop or target is touched (engine-agnostic exits).
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd

from kairos.backtest.protocols import BarsData, SignalFrame


def _wilder_atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            (high - low).abs(),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / n, adjust=False).mean()


class AtrBreakoutRrStrategy:
    name: str = "atr_breakout_rr"
    default_params: Mapping[str, Any] = {
        "lookback": 20,
        "atr_window": 14,
        "stop_atr_mult": 1.5,
        "risk_reward": 2.0,
        "holding_bars": 48,
    }

    def generate_signals(
        self, data: BarsData, params: Mapping[str, Any]
    ) -> SignalFrame:
        merged: dict[str, Any] = {**self.default_params, **dict(params)}
        lookback = int(merged["lookback"])
        atr_window = int(merged["atr_window"])
        stop_mult = float(merged["stop_atr_mult"])
        rr = float(merged["risk_reward"])
        holding_bars = int(merged["holding_bars"])

        ohlcv = data.ohlcv
        idx = ohlcv.index
        n = len(idx)

        if n == 0:
            return SignalFrame(
                df=pd.DataFrame(
                    {
                        "side": np.zeros(0, dtype=np.int8),
                        "size_hint": np.ones(0, dtype=np.float64),
                        "stop": np.zeros(0, dtype=np.float64),
                        "target": np.zeros(0, dtype=np.float64),
                    },
                    index=idx,
                )
            )

        close = ohlcv["close"].astype(float)
        high = ohlcv["high"].astype(float)
        low = ohlcv["low"].astype(float)

        upper = high.rolling(lookback).max().shift(1)
        lower = low.rolling(lookback).min().shift(1)
        atr = _wilder_atr(high, low, close, atr_window)

        close_np = close.to_numpy()
        high_np = high.to_numpy()
        low_np = low.to_numpy()
        upper_np = upper.to_numpy()
        lower_np = lower.to_numpy()
        atr_np = atr.to_numpy()

        side = np.zeros(n, dtype=np.int8)
        stop_arr = np.full(n, np.nan, dtype=np.float64)
        target_arr = np.full(n, np.nan, dtype=np.float64)

        pos = 0
        entry_bar = -1
        entry_stop = np.nan
        entry_target = np.nan

        for t in range(n):
            if pos != 0:
                held = t - entry_bar
                exited = False
                if pos == 1:
                    # Stop wins on same-bar tie.
                    if low_np[t] <= entry_stop:
                        exited = True
                    elif high_np[t] >= entry_target:
                        exited = True
                else:
                    if high_np[t] >= entry_stop:
                        exited = True
                    elif low_np[t] <= entry_target:
                        exited = True

                if exited:
                    side[t] = 0
                    pos = 0
                    entry_bar = -1
                    entry_stop = np.nan
                    entry_target = np.nan
                    continue

                if held >= holding_bars:
                    # Time exit at this bar's close — flip to flat now.
                    side[t] = 0
                    pos = 0
                    entry_bar = -1
                    entry_stop = np.nan
                    entry_target = np.nan
                    continue

                side[t] = pos
                stop_arr[t] = entry_stop
                target_arr[t] = entry_target
                continue

            up = upper_np[t]
            lo = lower_np[t]
            c = close_np[t]
            a = atr_np[t]
            if not (np.isfinite(up) and np.isfinite(lo) and np.isfinite(a) and a > 0.0):
                continue

            if c > up:
                entry_stop = c - stop_mult * a
                entry_target = c + rr * (c - entry_stop)
                if not (np.isfinite(entry_stop) and entry_stop < c):
                    continue
                pos = 1
                entry_bar = t
                side[t] = 1
                stop_arr[t] = entry_stop
                target_arr[t] = entry_target
            elif c < lo:
                entry_stop = c + stop_mult * a
                entry_target = c - rr * (entry_stop - c)
                if not (np.isfinite(entry_stop) and entry_stop > c):
                    continue
                pos = -1
                entry_bar = t
                side[t] = -1
                stop_arr[t] = entry_stop
                target_arr[t] = entry_target

        df = pd.DataFrame(
            {
                "side": side.astype(np.int8),
                "size_hint": np.ones(n, dtype=np.float64),
                "stop": stop_arr,
                "target": target_arr,
            },
            index=idx,
        )
        return SignalFrame(df=df)


__all__ = ["AtrBreakoutRrStrategy"]


# --- Registry self-registration -------------------------------------------
from kairos.strategies.registry import ParamField, StrategyEntry, register

register(
    StrategyEntry(
        name="atr_breakout_rr",
        label="ATR Breakout (R:R)",
        factory=lambda: AtrBreakoutRrStrategy(),
        default_params=AtrBreakoutRrStrategy.default_params,
        param_schema={
            "lookback": ParamField(
                type="int", default=20, min=5, max=200,
                help="Donchian 룩백 봉 수",
            ),
            "atr_window": ParamField(
                type="int", default=14, min=2, max=100,
                help="ATR 길이",
            ),
            "stop_atr_mult": ParamField(
                type="float", default=1.5, min=0.2, max=5.0,
                help="진입가 대비 손절 ATR 배수",
            ),
            "risk_reward": ParamField(
                type="float", default=2.0, min=0.5, max=10.0,
                help="손절 대비 익절 배수(손익비)",
            ),
            "holding_bars": ParamField(
                type="int", default=48, min=1, max=500,
                help="최대 보유 봉 수",
            ),
        },
        supports_funding=False,
        supports_metrics=False,
        description=(
            "N-bar Donchian 돌파 진입, ATR 기반 고정 손절, "
            "손익비로 익절 자동 산출."
        ),
    )
)
