"""Strategy D — VWAP Reversion.

FROZEN implementation conforming to ``docs/strategies/D-vwap-reversion.md``.
Deterministic, no look-ahead, no risk/sizing (size_hint fixed to 1.0).

Per-UTC-day VWAP and session σ are computed via ``groupby(normalize()).cumsum()``
on typical-price × volume. Wilder RSI uses ``ewm(alpha=1/n, adjust=False)``.
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


class VwapReversionStrategy:
    name: str = "vwap_reversion"
    default_params: Mapping[str, Any] = {
        "sigma_mult": 2.0,
        "rsi_window": 14,
        "rsi_oversold": 30.0,
        "rsi_overbought": 70.0,
        "holding_bars": 12,
        "min_session_bars": 4,
    }

    def generate_signals(
        self, data: BarsData, params: Mapping[str, Any]
    ) -> SignalFrame:
        merged: dict[str, Any] = {**self.default_params, **dict(params)}
        sigma_mult = float(merged["sigma_mult"])
        rsi_window = int(merged["rsi_window"])
        rsi_os = float(merged["rsi_oversold"])
        rsi_ob = float(merged["rsi_overbought"])
        holding_bars = int(merged["holding_bars"])
        min_session_bars = int(merged["min_session_bars"])

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

        # Session-bar index (1-based) per day — for the min_session_bars gate.
        session_bar = day.groupby(day).cumcount() + 1

        rsi = _wilder_rsi(close, rsi_window)

        upper = vwap + sigma_mult * sigma
        lower = vwap - sigma_mult * sigma

        close_np = close.to_numpy()
        vwap_np = vwap.to_numpy()
        sigma_np = sigma.to_numpy()
        upper_np = upper.to_numpy()
        lower_np = lower.to_numpy()
        rsi_np = rsi.to_numpy()
        sb_np = session_bar.to_numpy()
        day_np = day.to_numpy()

        side = np.zeros(n, dtype=np.int8)
        pos = 0
        entry_bar = -1

        for t in range(n):
            # Exit / holding logic (evaluated before entry so we can re-enter
            # only on a later bar).
            if pos != 0:
                held = t - entry_bar
                flatten = False
                # Opposite-side VWAP touch
                if pos == 1 and np.isfinite(vwap_np[t]) and close_np[t] >= vwap_np[t]:
                    flatten = True
                elif pos == -1 and np.isfinite(vwap_np[t]) and close_np[t] <= vwap_np[t]:
                    flatten = True
                # Holding cap (bars_held includes entry bar; once held ==
                # holding_bars we flatten on the NEXT bar).
                elif held >= holding_bars:
                    flatten = True
                # End-of-day flatten: new day break -> pos resets
                elif day_np[t] != day_np[entry_bar]:
                    flatten = True

                if flatten:
                    pos = 0
                    entry_bar = -1
                    side[t] = 0
                else:
                    side[t] = pos
                    continue

            # Entry logic (only when flat).
            if pos == 0:
                if sb_np[t] < min_session_bars:
                    continue
                if not (np.isfinite(vwap_np[t]) and np.isfinite(sigma_np[t])):
                    continue
                if not np.isfinite(rsi_np[t]):
                    continue
                if close_np[t] < lower_np[t] and rsi_np[t] < rsi_os:
                    pos = 1
                    entry_bar = t
                    side[t] = 1
                elif close_np[t] > upper_np[t] and rsi_np[t] > rsi_ob:
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


__all__ = ["VwapReversionStrategy"]


# --- Registry self-registration -------------------------------------------
from kairos.strategies.registry import ParamField, StrategyEntry, register

register(
    StrategyEntry(
        name="vwap_reversion",
        label="VWAP Reversion",
        factory=lambda: VwapReversionStrategy(),
        default_params=VwapReversionStrategy.default_params,
        param_schema={
            "sigma_mult": ParamField(
                type="float", default=2.0, min=0.5, max=5.0,
                help="VWAP 편차 σ 배수 임계.",
            ),
            "rsi_window": ParamField(
                type="int", default=14, min=2, max=100,
                help="Wilder RSI 길이.",
            ),
            "rsi_oversold": ParamField(
                type="float", default=30.0, min=5.0, max=45.0,
                help="롱 진입 RSI 임계.",
            ),
            "rsi_overbought": ParamField(
                type="float", default=70.0, min=55.0, max=95.0,
                help="숏 진입 RSI 임계.",
            ),
            "holding_bars": ParamField(
                type="int", default=12, min=1, max=200,
                help="최대 보유 봉 수.",
            ),
            "min_session_bars": ParamField(
                type="int", default=4, min=1, max=50,
                help="σ 안정화 전 진입 금지 최소 누적 봉 수.",
            ),
        },
        supports_funding=False,
        supports_metrics=False,
        description=(
            "세션 VWAP 대비 N-σ 이탈 + RSI 극단치에서 평균회귀 진입, "
            "VWAP 복귀/보유 상한/세션 종료로 청산."
        ),
    )
)
