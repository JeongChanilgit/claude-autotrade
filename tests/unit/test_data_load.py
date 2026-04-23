"""Tests for kairos.data.load on real parquet partitions."""
from __future__ import annotations

import pandas as pd
import pytest

from kairos.data.load import load_bars, load_ohlcv


_DATA_ROOT_BTC_2024_03 = "data/klines/symbol=BTCUSDT/interval=1m/year=2024/month=03"


def _has_march_2024_data() -> bool:
    from pathlib import Path
    p = Path(_DATA_ROOT_BTC_2024_03)
    return p.exists() and any(p.glob("*.parquet"))


pytestmark = pytest.mark.skipif(
    not _has_march_2024_data(),
    reason="requires BTCUSDT 1m 2024-03 partitions (Phase 1 gate data)",
)


def test_load_ohlcv_returns_expected_shape() -> None:
    start = pd.Timestamp("2024-03-15", tz="UTC")
    end = pd.Timestamp("2024-03-22", tz="UTC")
    df = load_ohlcv("BTCUSDT", "1m", start, end)
    # 7 days × 1440 minutes
    assert len(df) == 7 * 1440
    assert list(df.columns) == [
        "open", "high", "low", "close", "volume",
        "quote_volume", "taker_buy_volume", "taker_buy_quote_volume",
        "trade_count",
    ]
    assert df["trade_count"].dtype == "int64"
    assert df.index.tz is not None
    assert df.index.is_monotonic_increasing


def test_load_bars_builds_barsdata_container() -> None:
    start = pd.Timestamp("2024-03-15", tz="UTC")
    end = pd.Timestamp("2024-03-16", tz="UTC")
    bars = load_bars("BTCUSDT", "1m", start, end)
    assert bars.symbol == "BTCUSDT"
    assert bars.interval == "1m"
    assert len(bars.ohlcv) == 1440
    # funding/metrics may be None if not downloaded — just check no crash.


def test_load_ohlcv_half_open_interval() -> None:
    """end is exclusive — last timestamp must be < end."""
    start = pd.Timestamp("2024-03-15", tz="UTC")
    end = pd.Timestamp("2024-03-16", tz="UTC")
    df = load_ohlcv("BTCUSDT", "1m", start, end)
    assert df.index[-1] < end
    assert df.index[0] >= start


def test_load_ohlcv_empty_for_unknown_symbol() -> None:
    start = pd.Timestamp("2024-03-15", tz="UTC")
    end = pd.Timestamp("2024-03-16", tz="UTC")
    df = load_ohlcv("ZZZZUSDT", "1m", start, end)
    assert df.empty


def test_load_ohlcv_rejects_naive_timestamps() -> None:
    with pytest.raises(ValueError, match="UTC-aware"):
        load_ohlcv(
            "BTCUSDT",
            "1m",
            pd.Timestamp("2024-03-15"),  # naive
            pd.Timestamp("2024-03-16", tz="UTC"),
        )
