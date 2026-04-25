"""Strategy O — Swing Trend Pullback with fixed R:R.

FROZEN implementation conforming to ``docs/strategies/O-swing-trend-rr.md``.
Deterministic, no look-ahead. EMA trend filter + pullback/reclaim entry,
recent swing low/high as fixed stop, risk_reward-multiple target. Strategy
fills stop/target columns and flips side to 0 on stop/target/holding-cap.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd

from kairos.backtest.protocols import BarsData, SignalFrame


class SwingTrendRrStrategy:
    name: str = "swing_trend_rr"
    default_params: Mapping[str, Any] = {
        "ema_fast": 20,
        "ema_slow": 50,
        "swing_window": 10,
        "risk_reward": 2.5,
        "holding_bars": 60,
    }

    def generate_signals(
        self, data: BarsData, params: Mapping[str, Any]
    ) -> SignalFrame:
        merged: dict[str, Any] = {**self.default_params, **dict(params)}
        ema_fast_n = int(merged["ema_fast"])
        ema_slow_n = int(merged["ema_slow"])
        swing_window = int(merged["swing_window"])
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

        ema_fast = close.ewm(span=ema_fast_n, adjust=False).mean()
        ema_slow = close.ewm(span=ema_slow_n, adjust=False).mean()

        swing_low = low.rolling(swing_window).min()
        swing_high = high.rolling(swing_window).max()

        close_np = close.to_numpy()
        high_np = high.to_numpy()
        low_np = low.to_numpy()
        ef_np = ema_fast.to_numpy()
        es_np = ema_slow.to_numpy()
        swing_lo_np = swing_low.to_numpy()
        swing_hi_np = swing_high.to_numpy()

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

                side[t] = pos
                stop_arr[t] = entry_stop
                target_arr[t] = entry_target
                continue

            # Entry — require prior bar for pullback/reclaim logic.
            if t == 0:
                continue
            ef = ef_np[t]
            es = es_np[t]
            ef_prev = ef_np[t - 1]
            c = close_np[t]
            c_prev = close_np[t - 1]
            sl = swing_lo_np[t]
            sh = swing_hi_np[t]
            if not (
                np.isfinite(ef) and np.isfinite(es)
                and np.isfinite(ef_prev) and np.isfinite(sl) and np.isfinite(sh)
            ):
                continue

            if ef > es and c_prev < ef_prev and c > ef:
                stop_px = sl
                if not (stop_px < c):
                    continue
                target_px = c + rr * (c - stop_px)
                pos = 1
                entry_bar = t
                entry_stop = stop_px
                entry_target = target_px
                side[t] = 1
                stop_arr[t] = stop_px
                target_arr[t] = target_px
            elif ef < es and c_prev > ef_prev and c < ef:
                stop_px = sh
                if not (stop_px > c):
                    continue
                target_px = c - rr * (stop_px - c)
                pos = -1
                entry_bar = t
                entry_stop = stop_px
                entry_target = target_px
                side[t] = -1
                stop_arr[t] = stop_px
                target_arr[t] = target_px

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


__all__ = ["SwingTrendRrStrategy"]


# --- Registry self-registration -------------------------------------------
from kairos.strategies.registry import ParamField, StrategyEntry, register

register(
    StrategyEntry(
        name="swing_trend_rr",
        label="Swing Trend Pullback (R:R)",
        factory=lambda: SwingTrendRrStrategy(),
        default_params=SwingTrendRrStrategy.default_params,
        param_schema={
            "ema_fast": ParamField(
                type="int", default=20, min=2, max=200,
                help="단기 EMA 길이",
            ),
            "ema_slow": ParamField(
                type="int", default=50, min=5, max=500,
                help="장기 EMA 길이",
            ),
            "swing_window": ParamField(
                type="int", default=10, min=2, max=100,
                help="손절용 스윙 룩백 봉 수",
            ),
            "risk_reward": ParamField(
                type="float", default=2.5, min=0.5, max=10.0,
                help="손절 대비 익절 배수(손익비)",
            ),
            "holding_bars": ParamField(
                type="int", default=60, min=1, max=500,
                help="최대 보유 봉 수",
            ),
        },
        supports_funding=False,
        supports_metrics=False,
        description=(
            "EMA 추세 필터 + 눌림 반전 진입, 스윙 저가/고가 고정 손절, "
            "손익비로 익절 자동 산출."
        ),
    )
)
