"""Strategy H — RSI(2) Pullback.

FROZEN implementation conforming to ``docs/strategies/H-rsi2-pullback.md``.
Deterministic, no look-ahead, no risk/sizing (size_hint fixed to 1.0).

Connors-style short-RSI mean reversion gated by a long trend SMA: buy
extreme dips only inside an uptrend, mirror for shorts. Exit when RSI
reverts through 50, the trend flips, or the holding cap is reached.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd

from kairos.backtest.protocols import BarsData, SignalFrame


def _wilder_rsi(close: pd.Series, n: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / n, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / n, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - 100.0 / (1.0 + rs)
    rsi = rsi.where(~((avg_loss == 0.0) & (avg_gain > 0.0)), 100.0)
    return rsi


class Rsi2PullbackStrategy:
    name: str = "rsi2_pullback"
    default_params: Mapping[str, Any] = {
        "rsi_window": 2,
        "trend_sma_window": 200,
        "rsi_long_thresh": 10.0,
        "rsi_short_thresh": 90.0,
        "holding_bars": 12,
    }

    def generate_signals(
        self, data: BarsData, params: Mapping[str, Any]
    ) -> SignalFrame:
        merged: dict[str, Any] = {**self.default_params, **dict(params)}
        rsi_window = int(merged["rsi_window"])
        trend_sma_window = int(merged["trend_sma_window"])
        rsi_long_thresh = float(merged["rsi_long_thresh"])
        rsi_short_thresh = float(merged["rsi_short_thresh"])
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
        rsi2 = _wilder_rsi(close, rsi_window)
        trend_sma = close.rolling(trend_sma_window).mean()

        close_np = close.to_numpy()
        rsi_np = rsi2.to_numpy()
        sma_np = trend_sma.to_numpy()

        side = np.zeros(n, dtype=np.int8)
        pos = 0
        entry_bar = -1

        for t in range(n):
            c = close_np[t]
            r = rsi_np[t]
            s = sma_np[t]

            if pos != 0:
                held = t - entry_bar
                flatten = False
                if pos == 1:
                    if np.isfinite(r) and r > 50.0:
                        flatten = True
                    elif np.isfinite(s) and c < s:
                        flatten = True
                    elif held >= holding_bars:
                        flatten = True
                else:  # pos == -1
                    if np.isfinite(r) and r < 50.0:
                        flatten = True
                    elif np.isfinite(s) and c > s:
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

            # Entry (flat). Trend SMA and RSI must be defined.
            if not (np.isfinite(s) and np.isfinite(r)):
                continue
            if c > s and r < rsi_long_thresh:
                pos = 1
                entry_bar = t
                side[t] = 1
            elif c < s and r > rsi_short_thresh:
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


__all__ = ["Rsi2PullbackStrategy"]


# --- Registry self-registration -------------------------------------------
from kairos.strategies.registry import ParamField, StrategyEntry, register

register(
    StrategyEntry(
        name="rsi2_pullback",
        label="RSI(2) Pullback",
        factory=lambda: Rsi2PullbackStrategy(),
        default_params=Rsi2PullbackStrategy.default_params,
        param_schema={
            "rsi_window": ParamField(
                type="int", default=2, min=2, max=50,
                help="Wilder RSI 길이 (극단용 짧게)",
            ),
            "trend_sma_window": ParamField(
                type="int", default=200, min=10, max=2000,
                help="장기 추세 SMA 길이",
            ),
            "rsi_long_thresh": ParamField(
                type="float", default=10.0, min=1.0, max=40.0,
                help="롱 진입 RSI 임계",
            ),
            "rsi_short_thresh": ParamField(
                type="float", default=90.0, min=60.0, max=99.0,
                help="숏 진입 RSI 임계",
            ),
            "holding_bars": ParamField(
                type="int", default=12, min=1, max=200,
                help="최대 보유 봉 수",
            ),
        },
        supports_funding=False,
        supports_metrics=False,
        description=(
            "장기 SMA 추세와 동일 방향의 RSI(2) 극단치에서 평균회귀 진입, "
            "RSI 50 복귀/추세 반전/보유 상한으로 청산."
        ),
    )
)
