"""Golden cases for EnsembleStrategy per docs/strategies/E-ensemble.md."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd
import pytest

from kairos.backtest.protocols import BarsData, SignalFrame
from kairos.strategies.ensemble import EnsembleStrategy


class _MockStrategy:
    """Returns a pre-built signed-size series as (side, size_hint)."""

    def __init__(self, name: str, signed: pd.Series) -> None:
        self.name = name
        self.default_params = {}
        self._signed = signed.astype(float)

    def generate_signals(self, data: BarsData, params: Mapping[str, Any]) -> SignalFrame:
        side = pd.Series(np.sign(self._signed).astype(np.int8), index=self._signed.index)
        size = self._signed.abs().clip(upper=1.0)
        df = pd.DataFrame(
            {
                "side": side,
                "size_hint": size.astype(float),
                "stop": np.full(len(self._signed), np.nan, dtype=float),
                "target": np.full(len(self._signed), np.nan, dtype=float),
            },
            index=self._signed.index,
        )
        return SignalFrame(df=df)


def _bars(n: int = 50, start: str = "2024-01-01") -> BarsData:
    idx = pd.date_range(start, periods=n, freq="1min", tz="UTC")
    ohlcv = pd.DataFrame(
        {
            "open": 100.0,
            "high": 100.0,
            "low": 100.0,
            "close": 100.0,
            "volume": 1.0,
            "quote_volume": 100.0,
            "taker_buy_volume": 0.5,
            "taker_buy_quote_volume": 50.0,
            "trade_count": 1,
        },
        index=idx,
    )
    ohlcv["trade_count"] = ohlcv["trade_count"].astype(np.int64)
    return BarsData(ohlcv=ohlcv, symbol="TESTUSDT", interval="1m")


def _build_ensemble(signed_map: dict[str, pd.Series]) -> EnsembleStrategy:
    strats = {name: _MockStrategy(name, s) for name, s in signed_map.items()}
    return EnsembleStrategy(strats)


def test_gc_e1_single_strategy_active() -> None:
    """A only +1/1.0; B,C = 0. weighted = 0.4 > 0.15 → side=+1, size_hint≈0.4."""
    data = _bars(20)
    idx = data.ohlcv.index
    ens = _build_ensemble(
        {
            "orb": pd.Series(1.0, index=idx),
            "funding_counter": pd.Series(0.0, index=idx),
            "vol_compression": pd.Series(0.0, index=idx),
        }
    )
    # corr_window longer than data so shrink stays 1.0 everywhere
    sf = ens.generate_signals(data, {"corr_window": 100})
    assert (sf.df["side"] == 1).all()
    np.testing.assert_allclose(sf.df["size_hint"].to_numpy(), 0.4)


def test_gc_e2_two_aligned_strategies() -> None:
    """A=+1, B=+1, C=0 → weighted 0.7 → side=+1, size≈0.7."""
    data = _bars(20)
    idx = data.ohlcv.index
    ens = _build_ensemble(
        {
            "orb": pd.Series(1.0, index=idx),
            "funding_counter": pd.Series(1.0, index=idx),
            "vol_compression": pd.Series(0.0, index=idx),
        }
    )
    sf = ens.generate_signals(data, {"corr_window": 100})
    assert (sf.df["side"] == 1).all()
    np.testing.assert_allclose(sf.df["size_hint"].to_numpy(), 0.7)


def test_gc_e3_opposing_strategies_cancel_below_threshold() -> None:
    """A=+1, B=-1, C=0 → weighted 0.1 < threshold 0.15 → flat."""
    data = _bars(20)
    idx = data.ohlcv.index
    ens = _build_ensemble(
        {
            "orb": pd.Series(1.0, index=idx),
            "funding_counter": pd.Series(-1.0, index=idx),
            "vol_compression": pd.Series(0.0, index=idx),
        }
    )
    sf = ens.generate_signals(data, {"corr_window": 100})
    assert (sf.df["side"] == 0).all()
    np.testing.assert_allclose(sf.df["size_hint"].to_numpy(), 0.0)


def test_gc_e4_all_flat() -> None:
    data = _bars(20)
    idx = data.ohlcv.index
    ens = _build_ensemble(
        {
            "orb": pd.Series(0.0, index=idx),
            "funding_counter": pd.Series(0.0, index=idx),
            "vol_compression": pd.Series(0.0, index=idx),
        }
    )
    sf = ens.generate_signals(data, {"corr_window": 100})
    assert (sf.df["side"] == 0).all()


def test_gc_e5_correlation_shrink() -> None:
    """A and B perfectly correlated (alternating ±1); C flat.

    ρ̄ = 1.0 after corr_window bars → shrink = corr_shrink_min = 0.5.
    raw weighted = 0.7 (when aligned +1). scaled = 0.35 > 0.15 → side=+1 size=0.35.
    """
    n = 40
    data = _bars(n)
    idx = data.ohlcv.index
    alt = pd.Series(np.where(np.arange(n) % 2 == 0, 1.0, -1.0), index=idx)
    ens = _build_ensemble(
        {
            "orb": alt,
            "funding_counter": alt,  # perfectly correlated
            "vol_compression": pd.Series(0.0, index=idx),
        }
    )
    sf = ens.generate_signals(data, {"corr_window": 10, "corr_shrink_min": 0.5})
    # After warmup (bar 10+), shrink should apply on bars where alt=+1:
    # weighted_raw = 0.4 + 0.3 = 0.7; shrink=0.5 → 0.35
    post_warm = sf.df.iloc[10:]
    longs = post_warm[post_warm["side"] == 1]
    shorts = post_warm[post_warm["side"] == -1]
    assert len(longs) > 0
    assert len(shorts) > 0
    np.testing.assert_allclose(longs["size_hint"].to_numpy(), 0.35, atol=1e-9)
    np.testing.assert_allclose(shorts["size_hint"].to_numpy(), 0.35, atol=1e-9)


def test_weights_must_sum_to_one() -> None:
    data = _bars(10)
    idx = data.ohlcv.index
    ens = _build_ensemble(
        {
            "orb": pd.Series(0.0, index=idx),
            "funding_counter": pd.Series(0.0, index=idx),
            "vol_compression": pd.Series(0.0, index=idx),
        }
    )
    with pytest.raises(ValueError, match="weights must sum to 1.0"):
        ens.generate_signals(
            data,
            {"weights": {"orb": 0.5, "funding_counter": 0.3, "vol_compression": 0.3}},
        )


def test_missing_sub_strategy_raises() -> None:
    data = _bars(10)
    idx = data.ohlcv.index
    strats = {"orb": _MockStrategy("orb", pd.Series(0.0, index=idx))}
    ens = EnsembleStrategy(strats)
    with pytest.raises(ValueError, match="missing strategies"):
        ens.generate_signals(data, {})


def test_idempotency() -> None:
    data = _bars(30)
    idx = data.ohlcv.index
    rng = np.random.default_rng(42)
    a = pd.Series(rng.choice([-1.0, 0.0, 1.0], size=30), index=idx)
    b = pd.Series(rng.choice([-1.0, 0.0, 1.0], size=30), index=idx)
    c = pd.Series(rng.choice([-1.0, 0.0, 1.0], size=30), index=idx)
    ens = _build_ensemble({"orb": a, "funding_counter": b, "vol_compression": c})
    sf1 = ens.generate_signals(data, {"corr_window": 10})
    sf2 = ens.generate_signals(data, {"corr_window": 10})
    pd.testing.assert_frame_equal(sf1.df, sf2.df)


def test_output_frame_conforms_to_protocol() -> None:
    data = _bars(20)
    idx = data.ohlcv.index
    ens = _build_ensemble(
        {
            "orb": pd.Series(1.0, index=idx),
            "funding_counter": pd.Series(0.0, index=idx),
            "vol_compression": pd.Series(0.0, index=idx),
        }
    )
    sf = ens.generate_signals(data, {"corr_window": 100})
    assert list(sf.df.columns) == ["side", "size_hint", "stop", "target"]
    assert sf.df["side"].dtype == np.int8
    assert sf.df["size_hint"].dtype == float
    assert sf.df["stop"].isna().all()
    assert sf.df["target"].isna().all()
    assert sf.df.index.equals(idx)
