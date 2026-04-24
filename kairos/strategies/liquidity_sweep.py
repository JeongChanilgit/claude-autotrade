"""Strategy J — Liquidity Sweep Reversal.

FROZEN implementation conforming to ``docs/strategies/J-liquidity-sweep.md``.
Deterministic, no look-ahead, no risk/sizing (size_hint fixed to 1.0).

Thesis: when a bar's extreme punches through the prior N-bar extreme but
the CLOSE comes back inside, a stop-hunt liquidity sweep has occurred; fade
the failed breakout.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd

from kairos.backtest.protocols import BarsData, SignalFrame


class LiquiditySweepStrategy:
    name: str = "liquidity_sweep"
    default_params: Mapping[str, Any] = {
        "lookback": 20,
        "min_wick_pct": 0.002,
        "holding_bars": 8,
    }

    def generate_signals(
        self, data: BarsData, params: Mapping[str, Any]
    ) -> SignalFrame:
        merged: dict[str, Any] = {**self.default_params, **dict(params)}
        lookback = int(merged["lookback"])
        min_wick_pct = float(merged["min_wick_pct"])
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

        high = ohlcv["high"].astype(float)
        low = ohlcv["low"].astype(float)
        close = ohlcv["close"].astype(float)

        prior_high = high.rolling(lookback).max().shift(1)
        prior_low = low.rolling(lookback).min().shift(1)

        high_np = high.to_numpy()
        low_np = low.to_numpy()
        close_np = close.to_numpy()
        prior_high_np = prior_high.to_numpy()
        prior_low_np = prior_low.to_numpy()

        side = np.zeros(n, dtype=np.int8)
        pos = 0
        entry_bar = -1

        for t in range(n):
            # Determine per-bar sweep signals.
            bull_trap_short = False  # sweep of prior_high → short
            bear_trap_long = False   # sweep of prior_low → long
            if (
                np.isfinite(prior_high_np[t])
                and close_np[t] > 0
                and high_np[t] > prior_high_np[t]
                and close_np[t] < prior_high_np[t]
            ):
                wick_pct = (high_np[t] - close_np[t]) / close_np[t]
                if wick_pct >= min_wick_pct:
                    bull_trap_short = True
            if (
                np.isfinite(prior_low_np[t])
                and close_np[t] > 0
                and low_np[t] < prior_low_np[t]
                and close_np[t] > prior_low_np[t]
            ):
                wick_pct = (close_np[t] - low_np[t]) / close_np[t]
                if wick_pct >= min_wick_pct:
                    bear_trap_long = True

            if pos != 0:
                held = t - entry_bar
                flatten = False
                if held >= holding_bars:
                    flatten = True
                # Opposite-direction signal → flatten this bar (no same-bar reentry).
                elif pos == 1 and bull_trap_short:
                    flatten = True
                elif pos == -1 and bear_trap_long:
                    flatten = True

                if flatten:
                    pos = 0
                    entry_bar = -1
                    side[t] = 0
                    continue
                else:
                    side[t] = pos
                    continue

            # Flat → check entry.
            if bear_trap_long and not bull_trap_short:
                pos = 1
                entry_bar = t
                side[t] = 1
            elif bull_trap_short and not bear_trap_long:
                pos = -1
                entry_bar = t
                side[t] = -1
            # If both conditions fire on the same bar (rare), skip: ambiguous.

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


__all__ = ["LiquiditySweepStrategy"]


# --- Registry self-registration -------------------------------------------
from kairos.strategies.registry import ParamField, StrategyEntry, register

register(
    StrategyEntry(
        name="liquidity_sweep",
        label="Liquidity Sweep Reversal",
        factory=lambda: LiquiditySweepStrategy(),
        default_params=LiquiditySweepStrategy.default_params,
        param_schema={
            "lookback": ParamField(
                type="int", default=20, min=2, max=500,
                help="이전 고가/저가 집계 봉 수",
            ),
            "min_wick_pct": ParamField(
                type="float", default=0.002, min=0.0, max=0.1,
                help="최소 꼬리 비율 (close 대비)",
            ),
            "holding_bars": ParamField(
                type="int", default=8, min=1, max=200,
                help="최대 보유 봉 수",
            ),
        },
        supports_funding=False,
        supports_metrics=False,
        description=(
            "직전 N봉 극단을 돌파했다가 해당 봉 close가 되돌아오는 "
            "liquidity sweep 패턴을 역방향으로 진입."
        ),
    )
)
