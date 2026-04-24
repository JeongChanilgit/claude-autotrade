"""Strategy K — Session Momentum.

FROZEN implementation conforming to ``docs/strategies/K-session-momentum.md``.
Deterministic, no look-ahead, no risk/sizing (size_hint fixed to 1.0).

Thesis: the first 15m bar (or first ``first_bars`` bars) of the UTC day sets
the session tone; follow its direction for the rest of the day when the
session's opening volume is above a rolling baseline.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd

from kairos.backtest.protocols import BarsData, SignalFrame


class SessionMomentumStrategy:
    name: str = "session_momentum"
    default_params: Mapping[str, Any] = {
        "first_bars": 1,
        "volume_multiple": 1.2,
        "volume_lookback_days": 20,
        "holding_bars": 96,
    }

    def generate_signals(
        self, data: BarsData, params: Mapping[str, Any]
    ) -> SignalFrame:
        merged: dict[str, Any] = {**self.default_params, **dict(params)}
        first_bars = int(merged["first_bars"])
        volume_multiple = float(merged["volume_multiple"])
        volume_lookback_days = int(merged["volume_lookback_days"])
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

        open_ = ohlcv["open"].astype(float).to_numpy()
        close = ohlcv["close"].astype(float).to_numpy()
        volume = ohlcv["volume"].astype(float).to_numpy()

        day = idx.normalize()
        day_np = day.to_numpy()

        # Compute day start indices: the position in idx where each UTC day begins.
        # day_change[t] is True when day changes from t-1 to t (always True at t=0).
        day_change = np.empty(n, dtype=bool)
        day_change[0] = True
        if n > 1:
            day_change[1:] = day_np[1:] != day_np[:-1]
        day_starts = np.flatnonzero(day_change)  # positions of first bar of each day

        # For each bar index t, find that day's start index.
        day_id = np.cumsum(day_change) - 1  # 0..D-1 day index per bar
        day_start_of_bar = day_starts[day_id]

        # Aggregate first_bars volume and direction per day.
        num_days = len(day_starts)
        first_vol = np.full(num_days, np.nan, dtype=np.float64)
        direction = np.zeros(num_days, dtype=np.int8)
        has_enough = np.zeros(num_days, dtype=bool)
        entry_idx = np.full(num_days, -1, dtype=np.int64)  # bar index after first_bars
        next_day_start = np.empty(num_days, dtype=np.int64)
        for d in range(num_days):
            start = day_starts[d]
            end = day_starts[d + 1] if d + 1 < num_days else n  # exclusive
            bars_in_day = end - start
            next_day_start[d] = end
            if bars_in_day < first_bars + 1:
                # Not enough bars after the opening window to enter.
                continue
            has_enough[d] = True
            first_end = start + first_bars  # exclusive
            vsum = float(np.sum(volume[start:first_end]))
            first_vol[d] = vsum
            # Direction from the last of the first_bars bars.
            last_first = first_end - 1
            if close[last_first] > open_[last_first]:
                direction[d] = 1
            elif close[last_first] < open_[last_first]:
                direction[d] = -1
            else:
                direction[d] = 0
            entry_idx[d] = first_end  # bar AFTER the first_bars opening window

        # Rolling mean of prior `volume_lookback_days` first-bar volumes, per day.
        # Uses only PRIOR days (shift by 1) so no look-ahead.
        fv_series = pd.Series(first_vol, dtype=np.float64)
        prior_mean = fv_series.shift(1).rolling(
            volume_lookback_days, min_periods=1
        ).mean().to_numpy()

        side = np.zeros(n, dtype=np.int8)

        for d in range(num_days):
            if not has_enough[d]:
                continue
            if direction[d] == 0:
                continue
            pm = prior_mean[d]
            if not np.isfinite(pm) or pm <= 0.0:
                continue
            if first_vol[d] < volume_multiple * pm:
                continue
            enter_at = int(entry_idx[d])
            session_end = int(next_day_start[d])  # exclusive
            # Hold until last bar before next UTC day, respecting holding cap.
            last_held = min(enter_at + holding_bars - 1, session_end - 1)
            if enter_at > last_held:
                continue
            side[enter_at : last_held + 1] = direction[d]

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


__all__ = ["SessionMomentumStrategy"]


# --- Registry self-registration -------------------------------------------
from kairos.strategies.registry import ParamField, StrategyEntry, register

register(
    StrategyEntry(
        name="session_momentum",
        label="Session Momentum",
        factory=lambda: SessionMomentumStrategy(),
        default_params=SessionMomentumStrategy.default_params,
        param_schema={
            "first_bars": ParamField(
                type="int", default=1, min=1, max=20,
                help="세션 첫 기준 봉 수",
            ),
            "volume_multiple": ParamField(
                type="float", default=1.2, min=0.1, max=10.0,
                help="첫 봉 볼륨 / 이전 평균 배수",
            ),
            "volume_lookback_days": ParamField(
                type="int", default=20, min=1, max=200,
                help="볼륨 평균 계산 일수",
            ),
            "holding_bars": ParamField(
                type="int", default=96, min=1, max=1000,
                help="최대 보유 봉 수(세션 내 cap)",
            ),
        },
        supports_funding=False,
        supports_metrics=False,
        description=(
            "UTC 일자 첫 first_bars 봉의 방향과 볼륨 확장을 근거로 "
            "해당 세션 동안 추세를 추종."
        ),
    )
)
