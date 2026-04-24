"""Unit tests for load_portfolio (Phase 5B multi-symbol loader).

Uses the existing BTCUSDT 2024-03 1m partition (Phase 1 gate data); the whole
module is skipped when that partition is absent. Missing-symbol handling is
exercised against a real gap (ETHUSDT has 15m but no 1m partition in repo).
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from kairos.backtest.protocols import PortfolioBars
from kairos.data.load import load_portfolio


_DATA_ROOT_BTC_2024_03 = Path("data/klines/symbol=BTCUSDT/interval=1m/year=2024/month=03")


def _has_march_2024_data() -> bool:
    return _DATA_ROOT_BTC_2024_03.exists() and any(
        _DATA_ROOT_BTC_2024_03.glob("*.parquet")
    )


_skip_no_data = pytest.mark.skipif(
    not _has_march_2024_data(),
    reason="requires BTCUSDT 1m 2024-03 partitions (Phase 1 gate data)",
)


@_skip_no_data
def test_load_portfolio_single_symbol_returns_valid_bars() -> None:
    start = pd.Timestamp("2024-03-15", tz="UTC")
    end = pd.Timestamp("2024-03-16", tz="UTC")
    pb = load_portfolio(
        symbols=["BTCUSDT"],
        interval="1m",
        start=start,
        end=end,
        weights={"BTCUSDT": 1.0},
    )
    assert isinstance(pb, PortfolioBars)
    assert pb.interval == "1m"
    assert list(pb.bars.keys()) == ["BTCUSDT"]
    assert len(pb.bars["BTCUSDT"].ohlcv) == 1440  # 1 day, 1m cadence
    assert pb.weights == {"BTCUSDT": 1.0}


@_skip_no_data
def test_load_portfolio_multi_symbol_aligns_interval() -> None:
    """Every returned BarsData carries the requested interval.

    Symbols with no 1m partition (e.g. ETHUSDT in the repo fixture) are
    returned with an empty ohlcv frame, not raised — pre-listing handling is
    the engine's job (PORTFOLIO.md §"Pre-listing handling").
    """
    start = pd.Timestamp("2024-03-15", tz="UTC")
    end = pd.Timestamp("2024-03-16", tz="UTC")
    pb = load_portfolio(
        symbols=["BTCUSDT", "ETHUSDT"],
        interval="1m",
        start=start,
        end=end,
        weights={"BTCUSDT": 0.7, "ETHUSDT": 0.3},
    )
    assert set(pb.bars.keys()) == {"BTCUSDT", "ETHUSDT"}
    for sym, bd in pb.bars.items():
        assert bd.interval == "1m"
        assert bd.symbol == sym
    # BTC has data; ensure the non-empty path was hit.
    assert len(pb.bars["BTCUSDT"].ohlcv) > 0


@_skip_no_data
def test_load_portfolio_missing_partition_yields_empty_ohlcv() -> None:
    start = pd.Timestamp("2024-03-15", tz="UTC")
    end = pd.Timestamp("2024-03-16", tz="UTC")
    pb = load_portfolio(
        symbols=["BTCUSDT", "NOPE_NOT_A_SYMBOL_USDT"],
        interval="1m",
        start=start,
        end=end,
        weights={"BTCUSDT": 0.5, "NOPE_NOT_A_SYMBOL_USDT": 0.5},
    )
    missing = pb.bars["NOPE_NOT_A_SYMBOL_USDT"]
    assert missing.ohlcv.empty
    # Empty frame must still carry the contract-mandated columns and dtypes.
    assert "close" in missing.ohlcv.columns
    assert missing.ohlcv["trade_count"].dtype == "int64"
    assert missing.ohlcv["close"].dtype == "float64"


def test_load_portfolio_rejects_weights_keys_mismatch() -> None:
    start = pd.Timestamp("2024-03-15", tz="UTC")
    end = pd.Timestamp("2024-03-16", tz="UTC")
    with pytest.raises(ValueError, match="weights keys"):
        load_portfolio(
            symbols=["BTCUSDT"],
            interval="1m",
            start=start,
            end=end,
            weights={"ETHUSDT": 1.0},
        )


def test_load_portfolio_rejects_weights_not_summing_to_one() -> None:
    start = pd.Timestamp("2024-03-15", tz="UTC")
    end = pd.Timestamp("2024-03-16", tz="UTC")
    with pytest.raises(ValueError, match="sum to 1.0"):
        load_portfolio(
            symbols=["BTCUSDT", "ETHUSDT"],
            interval="1m",
            start=start,
            end=end,
            weights={"BTCUSDT": 0.5, "ETHUSDT": 0.4},
        )


def test_load_portfolio_rejects_extra_weight_key() -> None:
    start = pd.Timestamp("2024-03-15", tz="UTC")
    end = pd.Timestamp("2024-03-16", tz="UTC")
    with pytest.raises(ValueError, match="weights keys"):
        load_portfolio(
            symbols=["BTCUSDT"],
            interval="1m",
            start=start,
            end=end,
            weights={"BTCUSDT": 0.5, "ETHUSDT": 0.5},
        )
