"""Strategy P — VWAP Target Reversion with fixed R:R.

FROZEN implementation conforming to ``docs/strategies/P-vwap-target-rr.md``.
Deterministic, no look-ahead. Strategy fills stop/target columns and flips
``side`` to 0 on the bar a stop/target is touched (engine-agnostic exits).

Session VWAP and σ are computed per-UTC-day via cumulative sums on
``typical × volume``. Entry is an N-σ overshoot; the VWAP at entry is
captured as the FIXED take-profit target. Stop is derived from the
target distance divided by ``risk_reward``.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd

from kairos.backtest.protocols import BarsData, SignalFrame


class VwapTargetRrStrategy:
    name: str = "vwap_target_rr"
    default_params: Mapping[str, Any] = {
        "sigma_mult": 2.0,
        "risk_reward": 1.5,
        "holding_bars": 24,
        "min_session_bars": 4,
    }

    def generate_signals(
        self, data: BarsData, params: Mapping[str, Any]
    ) -> SignalFrame:
        merged: dict[str, Any] = {**self.default_params, **dict(params)}
        sigma_mult = float(merged["sigma_mult"])
        rr = float(merged["risk_reward"])
        holding_bars = int(merged["holding_bars"])
        min_session_bars = int(merged["min_session_bars"])

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
        volume = ohlcv["volume"].astype(float)
        typical = (high + low + close) / 3.0

        day = pd.Series(idx.normalize(), index=idx)
        tp_vol = typical * volume
        cum_vol = volume.groupby(day).cumsum()
        cum_tpv = tp_vol.groupby(day).cumsum()
        vwap = cum_tpv / cum_vol.replace(0.0, np.nan)
        dev_sq = ((typical - vwap) ** 2) * volume
        cum_devsq = dev_sq.groupby(day).cumsum()
        var = cum_devsq / cum_vol.replace(0.0, np.nan)
        sigma = np.sqrt(var)

        session_bar = day.groupby(day).cumcount() + 1

        close_np = close.to_numpy()
        high_np = high.to_numpy()
        low_np = low.to_numpy()
        vwap_np = vwap.to_numpy()
        sigma_np = sigma.to_numpy()
        sb_np = session_bar.to_numpy()
        day_np = day.to_numpy()

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
                # Stop wins on same-bar tie.
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

                # Day-change flatten.
                if day_np[t] != day_np[entry_bar]:
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

                side[t] = pos
                stop_arr[t] = entry_stop
                target_arr[t] = entry_target
                continue

            # Entry gate.
            if sb_np[t] < min_session_bars:
                continue
            v = vwap_np[t]
            s = sigma_np[t]
            c = close_np[t]
            if not (np.isfinite(v) and np.isfinite(s) and s > 0.0):
                continue

            upper = v + sigma_mult * s
            lower = v - sigma_mult * s

            if c < lower:
                # Long overshoot: target = VWAP (revert up).
                tgt = v
                d = abs(tgt - c)
                if d <= 0.0:
                    continue
                stp = c - d / rr
                if not (np.isfinite(stp) and stp < c and tgt > c):
                    continue
                pos = 1
                entry_bar = t
                entry_stop = stp
                entry_target = tgt
                side[t] = 1
                stop_arr[t] = stp
                target_arr[t] = tgt
            elif c > upper:
                tgt = v
                d = abs(tgt - c)
                if d <= 0.0:
                    continue
                stp = c + d / rr
                if not (np.isfinite(stp) and stp > c and tgt < c):
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


__all__ = ["VwapTargetRrStrategy"]


# --- Registry self-registration -------------------------------------------
from kairos.strategies.registry import ParamField, StrategyEntry, register

register(
    StrategyEntry(
        name="vwap_target_rr",
        label="VWAP Target Reversion (R:R)",
        factory=lambda: VwapTargetRrStrategy(),
        default_params=VwapTargetRrStrategy.default_params,
        param_schema={
            "sigma_mult": ParamField(
                type="float", default=2.0, min=0.5, max=5.0,
                help="VWAP 편차 σ 배수 임계",
            ),
            "risk_reward": ParamField(
                type="float", default=1.5, min=0.5, max=10.0,
                help="고정 익절 대비 손절 배수(손익비). 높을수록 손절이 타이트해짐",
            ),
            "holding_bars": ParamField(
                type="int", default=24, min=1, max=200,
                help="최대 보유 봉 수",
            ),
            "min_session_bars": ParamField(
                type="int", default=4, min=1, max=50,
                help="σ 안정화 전 진입 금지 최소 누적 봉 수",
            ),
        },
        supports_funding=False,
        supports_metrics=False,
        description=(
            "세션 VWAP 대비 N-σ 이탈 진입, VWAP 복귀를 고정 익절로 사용, "
            "손익비로 손절 자동 산출."
        ),
    )
)
