"""Strategy G — Donchian Channel Breakout.

FROZEN implementation conforming to ``docs/strategies/G-donchian-breakout.md``.
Deterministic, no look-ahead, no risk/sizing (size_hint fixed to 1.0).

The channel at bar T is computed over the N bars strictly before T (via
``.rolling(N).max().shift(1)``), so the current bar cannot peek at itself.
Long on close > upper, short on close < lower; opposite-side flip or
holding cap flattens.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd

from kairos.backtest.protocols import BarsData, SignalFrame


class DonchianBreakoutStrategy:
    name: str = "donchian_breakout"
    default_params: Mapping[str, Any] = {
        "channel": 20,
        "holding_bars": 48,
    }

    def generate_signals(
        self, data: BarsData, params: Mapping[str, Any]
    ) -> SignalFrame:
        merged: dict[str, Any] = {**self.default_params, **dict(params)}
        channel = int(merged["channel"])
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

        # Rolling channel *excluding* the current bar.
        upper = high.rolling(channel).max().shift(1)
        lower = low.rolling(channel).min().shift(1)

        close_np = close.to_numpy()
        upper_np = upper.to_numpy()
        lower_np = lower.to_numpy()

        side = np.zeros(n, dtype=np.int8)
        pos = 0
        entry_bar = -1

        for t in range(n):
            up = upper_np[t]
            lo = lower_np[t]
            c = close_np[t]

            if pos != 0:
                held = t - entry_bar
                flatten = False
                if pos == 1 and np.isfinite(lo) and c < lo:
                    flatten = True
                elif pos == -1 and np.isfinite(up) and c > up:
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

            # Entry (flat). Channel must be defined.
            if not (np.isfinite(up) and np.isfinite(lo)):
                continue
            if c > up:
                pos = 1
                entry_bar = t
                side[t] = 1
            elif c < lo:
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


__all__ = ["DonchianBreakoutStrategy"]


# --- Registry self-registration -------------------------------------------
from kairos.strategies.registry import ParamField, StrategyEntry, register

register(
    StrategyEntry(
        name="donchian_breakout",
        label="Donchian Channel Breakout",
        factory=lambda: DonchianBreakoutStrategy(),
        default_params=DonchianBreakoutStrategy.default_params,
        param_schema={
            "channel": ParamField(
                type="int", default=20, min=2, max=500,
                help="Donchian 채널 길이 (봉)",
            ),
            "holding_bars": ParamField(
                type="int", default=48, min=1, max=1000,
                help="최대 보유 봉 수",
            ),
        },
        supports_funding=False,
        supports_metrics=False,
        description=(
            "직전 N봉 고점/저점 Donchian 채널 close 이탈 시 돌파 진입, "
            "반대 채널 이탈/보유 상한으로 청산."
        ),
    )
)
