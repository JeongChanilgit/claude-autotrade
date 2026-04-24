"""Strategy L — Bollinger %B Reversion.

FROZEN implementation conforming to ``docs/strategies/L-bollinger-reversion.md``.
Deterministic, no look-ahead, no risk/sizing (size_hint fixed to 1.0).

Enters long when the prior bar closed below the lower band (%B < 0) and the
current bar re-enters the band (close > lower). Symmetric short rule above
the upper band. Exits on a mid-band cross or holding cap.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd

from kairos.backtest.protocols import BarsData, SignalFrame


def _bollinger(
    close: pd.Series, window: int, num_std: float
) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    mid = close.rolling(window).mean()
    std = close.rolling(window).std(ddof=0)
    upper = mid + num_std * std
    lower = mid - num_std * std
    denom = (upper - lower).replace(0.0, np.nan)
    pct_b = (close - lower) / denom
    return mid, upper, lower, pct_b


class BollingerReversionStrategy:
    name: str = "bollinger_reversion"
    default_params: Mapping[str, Any] = {
        "bb_window": 20,
        "bb_std": 2.0,
        "holding_bars": 12,
    }

    def generate_signals(
        self, data: BarsData, params: Mapping[str, Any]
    ) -> SignalFrame:
        merged: dict[str, Any] = {**self.default_params, **dict(params)}
        bb_window = int(merged["bb_window"])
        bb_std = float(merged["bb_std"])
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
        mid, upper, lower, pct_b = _bollinger(close, bb_window, bb_std)

        close_np = close.to_numpy()
        mid_np = mid.to_numpy()
        upper_np = upper.to_numpy()
        lower_np = lower.to_numpy()
        pctb_np = pct_b.to_numpy()

        side = np.zeros(n, dtype=np.int8)
        pos = 0
        entry_bar = -1

        for t in range(n):
            if pos != 0:
                held = t - entry_bar
                flatten = False
                if pos == 1 and np.isfinite(mid_np[t]) and close_np[t] >= mid_np[t]:
                    flatten = True
                elif pos == -1 and np.isfinite(mid_np[t]) and close_np[t] <= mid_np[t]:
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

            if pos == 0:
                if t == 0:
                    continue
                prev_pb = pctb_np[t - 1]
                if not np.isfinite(prev_pb):
                    continue
                if not (np.isfinite(lower_np[t]) and np.isfinite(upper_np[t])):
                    continue
                if prev_pb < 0.0 and close_np[t] > lower_np[t]:
                    pos = 1
                    entry_bar = t
                    side[t] = 1
                elif prev_pb > 1.0 and close_np[t] < upper_np[t]:
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


__all__ = ["BollingerReversionStrategy"]


# --- Registry self-registration -------------------------------------------
from kairos.strategies.registry import ParamField, StrategyEntry, register

register(
    StrategyEntry(
        name="bollinger_reversion",
        label="Bollinger %B Reversion",
        factory=lambda: BollingerReversionStrategy(),
        default_params=BollingerReversionStrategy.default_params,
        param_schema={
            "bb_window": ParamField(
                type="int", default=20, min=2, max=500,
                help="BB 창 길이",
            ),
            "bb_std": ParamField(
                type="float", default=2.0, min=0.1, max=10.0,
                help="BB 표준편차 배수",
            ),
            "holding_bars": ParamField(
                type="int", default=12, min=1, max=200,
                help="최대 보유 봉 수",
            ),
        },
        supports_funding=False,
        supports_metrics=False,
        description=(
            "Bollinger %B 이탈 후 밴드 내부 복귀 시 평균회귀 진입, "
            "중단 SMA 복귀/보유 상한으로 청산."
        ),
    )
)
