"""Strategy I — MACD Zero-Cross Momentum.

FROZEN implementation conforming to ``docs/strategies/I-macd-zero-cross.md``.
Deterministic, no look-ahead, no risk/sizing (size_hint fixed to 1.0).

Thesis: momentum flips are signalled by the MACD line crossing zero. A
volatility floor (Wilder ATR / close) filters out flat/noisy regimes.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd

from kairos.backtest.protocols import BarsData, SignalFrame


def _wilder_atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int) -> pd.Series:
    """Per-bar Wilder ATR. alpha = 1/n EWM on True Range.

    First bar's TR uses (high - low) since prev_close is NaN.
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
    return tr.ewm(alpha=1.0 / n, adjust=False).mean()


class MacdZeroCrossStrategy:
    name: str = "macd_zero_cross"
    default_params: Mapping[str, Any] = {
        "fast": 12,
        "slow": 26,
        "signal_span": 9,
        "atr_window": 14,
        "atr_min_pct": 0.003,
        "holding_bars": 24,
    }

    def generate_signals(
        self, data: BarsData, params: Mapping[str, Any]
    ) -> SignalFrame:
        merged: dict[str, Any] = {**self.default_params, **dict(params)}
        fast = int(merged["fast"])
        slow = int(merged["slow"])
        signal_span = int(merged["signal_span"])
        atr_window = int(merged["atr_window"])
        atr_min_pct = float(merged["atr_min_pct"])
        holding_bars = int(merged["holding_bars"])

        ohlcv = data.ohlcv
        idx = ohlcv.index
        n = len(idx)

        if n == 0:
            df = pd.DataFrame(
                {
                    "side": np.zeros(0, dtype=np.int8),
                    "size_hint": np.ones(0, dtype=np.float64),
                    "stop": np.full(0, np.nan, dtype=np.float64),
                    "target": np.full(0, np.nan, dtype=np.float64),
                },
                index=idx,
            )
            return SignalFrame(df=df)

        close = ohlcv["close"].astype(float)
        high = ohlcv["high"].astype(float)
        low = ohlcv["low"].astype(float)

        ema_fast = close.ewm(span=fast, adjust=False).mean()
        ema_slow = close.ewm(span=slow, adjust=False).mean()
        macd = ema_fast - ema_slow
        # Signal EMA included for spec completeness; not used for gating.
        _ = macd.ewm(span=signal_span, adjust=False).mean()

        atr = _wilder_atr(high, low, close, atr_window)

        close_np = close.to_numpy()
        macd_np = macd.to_numpy()
        atr_np = atr.to_numpy()

        side = np.zeros(n, dtype=np.int8)
        pos = 0
        entry_bar = -1

        for t in range(n):
            # Exit logic first.
            if pos != 0:
                held = t - entry_bar
                flatten = False
                # Opposite zero cross.
                if pos == 1 and np.isfinite(macd_np[t]) and macd_np[t] < 0:
                    flatten = True
                elif pos == -1 and np.isfinite(macd_np[t]) and macd_np[t] > 0:
                    flatten = True
                # Holding cap: last held bar is entry_bar + holding_bars - 1;
                # flatten on the next bar.
                elif held >= holding_bars:
                    flatten = True

                if flatten:
                    pos = 0
                    entry_bar = -1
                    side[t] = 0
                    # No same-bar re-entry: the flattening bar stays flat even
                    # if an opposite-cross condition would itself qualify as a
                    # fresh entry. This keeps position transitions unambiguous.
                    continue
                else:
                    side[t] = pos
                    continue

            # Entry logic (only when flat).
            if pos == 0 and t >= 1:
                prev_macd = macd_np[t - 1]
                cur_macd = macd_np[t]
                if not (np.isfinite(prev_macd) and np.isfinite(cur_macd)):
                    continue
                if not np.isfinite(atr_np[t]) or close_np[t] <= 0:
                    continue
                atr_pct = atr_np[t] / close_np[t]
                if atr_pct < atr_min_pct:
                    continue
                if prev_macd <= 0.0 and cur_macd > 0.0:
                    pos = 1
                    entry_bar = t
                    side[t] = 1
                elif prev_macd >= 0.0 and cur_macd < 0.0:
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


__all__ = ["MacdZeroCrossStrategy"]


# --- Registry self-registration -------------------------------------------
from kairos.strategies.registry import ParamField, StrategyEntry, register

register(
    StrategyEntry(
        name="macd_zero_cross",
        label="MACD Zero-Cross Momentum",
        factory=lambda: MacdZeroCrossStrategy(),
        default_params=MacdZeroCrossStrategy.default_params,
        param_schema={
            "fast": ParamField(
                type="int", default=12, min=2, max=100,
                help="MACD 빠른 EMA",
            ),
            "slow": ParamField(
                type="int", default=26, min=2, max=500,
                help="MACD 느린 EMA",
            ),
            "signal_span": ParamField(
                type="int", default=9, min=2, max=200,
                help="MACD 시그널 EMA",
            ),
            "atr_window": ParamField(
                type="int", default=14, min=2, max=200,
                help="Wilder ATR 길이",
            ),
            "atr_min_pct": ParamField(
                type="float", default=0.003, min=0.0, max=0.1,
                help="최소 ATR/close (변동성 필터)",
            ),
            "holding_bars": ParamField(
                type="int", default=24, min=1, max=500,
                help="최대 보유 봉 수",
            ),
        },
        supports_funding=False,
        supports_metrics=False,
        description=(
            "MACD(fast/slow) 제로라인 교차를 방향성 트리거로 삼고, "
            "Wilder ATR/close 하한으로 저변동성 구간을 필터링한다."
        ),
    )
)
