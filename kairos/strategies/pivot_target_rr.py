"""Strategy Q — Pivot Target Reversion with fixed R:R.

FROZEN implementation conforming to ``docs/strategies/Q-pivot-target-rr.md``.
Deterministic, no look-ahead. Strategy fills stop/target columns and flips
``side`` to 0 on the bar a stop/target is touched (engine-agnostic exits).

Classic floor-trader daily pivots derived from the PRIOR UTC day's OHLC:
  P  = (H + L + C) / 3
  S1 = 2P - H
  R1 = 2P - L
Long on S1 touch + close back above S1; short on R1 touch + close back below.
Target = P (fixed); stop derived from the target distance / risk_reward.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd

from kairos.backtest.protocols import BarsData, SignalFrame


class PivotTargetRrStrategy:
    name: str = "pivot_target_rr"
    default_params: Mapping[str, Any] = {
        "risk_reward": 2.0,
        "holding_bars": 48,
    }

    def generate_signals(
        self, data: BarsData, params: Mapping[str, Any]
    ) -> SignalFrame:
        merged: dict[str, Any] = {**self.default_params, **dict(params)}
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

        # Daily aggregation in UTC; shift(1) so bars on day D use D-1's values.
        daily = ohlcv[["high", "low", "close"]].resample("1D").agg(
            {"high": "max", "low": "min", "close": "last"}
        )
        prev = daily.shift(1)
        prev_p = (prev["high"] + prev["low"] + prev["close"]) / 3.0
        prev_s1 = 2.0 * prev_p - prev["high"]
        prev_r1 = 2.0 * prev_p - prev["low"]

        # Broadcast per-bar: reindex to bar index and ffill within day.
        bar_day = idx.normalize()
        p_series = prev_p.reindex(pd.DatetimeIndex(bar_day)).ffill()
        s1_series = prev_s1.reindex(pd.DatetimeIndex(bar_day)).ffill()
        r1_series = prev_r1.reindex(pd.DatetimeIndex(bar_day)).ffill()
        p_np = p_series.to_numpy()
        s1_np = s1_series.to_numpy()
        r1_np = r1_series.to_numpy()

        close_np = close.to_numpy()
        high_np = high.to_numpy()
        low_np = low.to_numpy()
        day_np = pd.Series(bar_day, index=idx).to_numpy()

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
                    side[t] = 0
                    pos = 0
                    entry_bar = -1
                    entry_stop = np.nan
                    entry_target = np.nan
                    continue

                if day_np[t] != day_np[entry_bar]:
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

            p = p_np[t]
            s1 = s1_np[t]
            r1 = r1_np[t]
            c = close_np[t]
            h = high_np[t]
            lo = low_np[t]
            if not (np.isfinite(p) and np.isfinite(s1) and np.isfinite(r1)):
                continue

            # Long: touch S1 and close back above it; P must be above entry.
            if lo <= s1 < c and p > c:
                tgt = p
                d = abs(tgt - c)
                if d <= 0.0:
                    continue
                stp = c - d / rr
                if not (np.isfinite(stp) and stp < c):
                    continue
                pos = 1
                entry_bar = t
                entry_stop = stp
                entry_target = tgt
                side[t] = 1
                stop_arr[t] = stp
                target_arr[t] = tgt
            elif h >= r1 > c and p < c:
                tgt = p
                d = abs(tgt - c)
                if d <= 0.0:
                    continue
                stp = c + d / rr
                if not (np.isfinite(stp) and stp > c):
                    continue
                pos = -1
                entry_bar = t
                entry_stop = stp
                entry_target = tgt
                side[t] = -1
                stop_arr[t] = stp
                target_arr[t] = tgt

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


__all__ = ["PivotTargetRrStrategy"]


# --- Registry self-registration -------------------------------------------
from kairos.strategies.registry import ParamField, StrategyEntry, register

register(
    StrategyEntry(
        name="pivot_target_rr",
        label="Pivot Target Reversion (R:R)",
        factory=lambda: PivotTargetRrStrategy(),
        default_params=PivotTargetRrStrategy.default_params,
        param_schema={
            "risk_reward": ParamField(
                type="float", default=2.0, min=0.5, max=10.0,
                help="고정 익절 대비 손절 배수(손익비). 높을수록 손절이 타이트해짐",
            ),
            "holding_bars": ParamField(
                type="int", default=48, min=1, max=500,
                help="최대 보유 봉 수",
            ),
        },
        supports_funding=False,
        supports_metrics=False,
        description=(
            "전일 OHLC 기반 데일리 피봇 S1/R1 터치 역진입, "
            "데일리 피봇 P를 고정 익절로 사용, 손익비로 손절 자동 산출."
        ),
    )
)
