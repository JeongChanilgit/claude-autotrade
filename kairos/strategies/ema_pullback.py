"""Strategy F — EMA Pullback Trend.

FROZEN implementation conforming to ``docs/strategies/F-ema-pullback.md``.
Deterministic, no look-ahead, no risk/sizing (size_hint fixed to 1.0).

Trend alignment is judged from the classic three-EMA stack. A long pullback
entry fires when prior bar's low touched the fast EMA and the current close
reclaims it; short entry is the mirror. Exit on stack break or holding cap.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd

from kairos.backtest.protocols import BarsData, SignalFrame


class EmaPullbackStrategy:
    name: str = "ema_pullback"
    default_params: Mapping[str, Any] = {
        "ema_fast": 20,
        "ema_mid": 50,
        "ema_slow": 200,
        "holding_bars": 32,
    }

    def generate_signals(
        self, data: BarsData, params: Mapping[str, Any]
    ) -> SignalFrame:
        merged: dict[str, Any] = {**self.default_params, **dict(params)}
        ema_fast_n = int(merged["ema_fast"])
        ema_mid_n = int(merged["ema_mid"])
        ema_slow_n = int(merged["ema_slow"])
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

        ema_fast = close.ewm(span=ema_fast_n, adjust=False).mean()
        ema_mid = close.ewm(span=ema_mid_n, adjust=False).mean()
        ema_slow = close.ewm(span=ema_slow_n, adjust=False).mean()

        close_np = close.to_numpy()
        high_np = high.to_numpy()
        low_np = low.to_numpy()
        ef_np = ema_fast.to_numpy()
        em_np = ema_mid.to_numpy()
        es_np = ema_slow.to_numpy()

        side = np.zeros(n, dtype=np.int8)
        pos = 0
        entry_bar = -1

        for t in range(n):
            long_trend = (
                np.isfinite(ef_np[t])
                and np.isfinite(em_np[t])
                and np.isfinite(es_np[t])
                and ef_np[t] > em_np[t] > es_np[t]
            )
            short_trend = (
                np.isfinite(ef_np[t])
                and np.isfinite(em_np[t])
                and np.isfinite(es_np[t])
                and ef_np[t] < em_np[t] < es_np[t]
            )

            # Exit / holding logic first: once flattened on this bar,
            # no re-entry is allowed on the same bar.
            if pos != 0:
                held = t - entry_bar
                flatten = False
                if pos == 1 and not long_trend:
                    flatten = True
                elif pos == -1 and not short_trend:
                    flatten = True
                elif held >= holding_bars:
                    flatten = True

                if flatten:
                    pos = 0
                    entry_bar = -1
                    side[t] = 0
                    continue
                side[t] = pos
                continue

            # Entry logic (only when flat). Requires t >= 1 for the pullback
            # lookback into bar T-1.
            if t < 1:
                continue
            prev_ef = ef_np[t - 1]
            if not np.isfinite(prev_ef):
                continue

            if long_trend and low_np[t - 1] <= prev_ef and close_np[t] > ef_np[t]:
                pos = 1
                entry_bar = t
                side[t] = 1
            elif short_trend and high_np[t - 1] >= prev_ef and close_np[t] < ef_np[t]:
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


__all__ = ["EmaPullbackStrategy"]


# --- Registry self-registration -------------------------------------------
from kairos.strategies.registry import ParamField, StrategyEntry, register

register(
    StrategyEntry(
        name="ema_pullback",
        label="EMA Pullback Trend",
        factory=lambda: EmaPullbackStrategy(),
        default_params=EmaPullbackStrategy.default_params,
        param_schema={
            "ema_fast": ParamField(
                type="int", default=20, min=2, max=200,
                help="빠른 EMA 길이",
            ),
            "ema_mid": ParamField(
                type="int", default=50, min=2, max=500,
                help="중간 EMA 길이",
            ),
            "ema_slow": ParamField(
                type="int", default=200, min=2, max=1000,
                help="느린 EMA 길이",
            ),
            "holding_bars": ParamField(
                type="int", default=32, min=1, max=500,
                help="최대 보유 봉 수",
            ),
        },
        supports_funding=False,
        supports_metrics=False,
        description=(
            "fast/mid/slow EMA 정렬 기반 추세추종; 빠른 EMA 풀백 후 "
            "재돌파 시 진입, 정렬 붕괴/보유 상한으로 청산."
        ),
    )
)
