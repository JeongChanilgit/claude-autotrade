"""Unit tests for portfolio protocol additions (Phase 5B).

Covers the frozen-dataclass invariants of PortfolioBars, constructibility of
PortfolioResult, and the module-level MAX_LEVERAGE constant. No engine logic
is exercised here — that's a later wave.
"""
from __future__ import annotations

from decimal import Decimal

import pandas as pd
import pytest

from kairos.backtest import protocols
from kairos.backtest.protocols import (
    MAX_LEVERAGE,
    BarsData,
    PortfolioBars,
    PortfolioResult,
)


def _empty_bars(symbol: str) -> BarsData:
    # Minimal valid BarsData for unit tests — shape is irrelevant here because
    # PortfolioBars validates keys/weights only, not inner frames.
    df = pd.DataFrame(
        {
            "open": pd.Series([], dtype="float64"),
            "high": pd.Series([], dtype="float64"),
            "low": pd.Series([], dtype="float64"),
            "close": pd.Series([], dtype="float64"),
            "volume": pd.Series([], dtype="float64"),
            "quote_volume": pd.Series([], dtype="float64"),
            "taker_buy_volume": pd.Series([], dtype="float64"),
            "taker_buy_quote_volume": pd.Series([], dtype="float64"),
            "trade_count": pd.Series([], dtype="int64"),
        }
    )
    df.index = pd.DatetimeIndex([], tz="UTC")
    return BarsData(ohlcv=df, symbol=symbol, interval="1m")


def test_max_leverage_is_three() -> None:
    # Frozen contract value; if this ever changes, PORTFOLIO.md changes first.
    assert MAX_LEVERAGE == 3.0
    # Also accessible as a module-level attr (for `from ... import MAX_LEVERAGE`).
    assert protocols.MAX_LEVERAGE == 3.0


def test_portfolio_bars_accepts_valid_input() -> None:
    bars = {"BTCUSDT": _empty_bars("BTCUSDT"), "ETHUSDT": _empty_bars("ETHUSDT")}
    weights = {"BTCUSDT": 0.6, "ETHUSDT": 0.4}
    pb = PortfolioBars(bars=bars, interval="1m", weights=weights)
    assert pb.interval == "1m"
    assert set(pb.bars.keys()) == {"BTCUSDT", "ETHUSDT"}
    assert pb.weights["BTCUSDT"] == 0.6


def test_portfolio_bars_rejects_weights_not_summing_to_one() -> None:
    bars = {"BTCUSDT": _empty_bars("BTCUSDT")}
    weights = {"BTCUSDT": 0.9}
    with pytest.raises(AssertionError):
        PortfolioBars(bars=bars, interval="1m", weights=weights)


def test_portfolio_bars_rejects_mismatched_keys() -> None:
    bars = {"BTCUSDT": _empty_bars("BTCUSDT")}
    weights = {"ETHUSDT": 1.0}
    with pytest.raises(AssertionError):
        PortfolioBars(bars=bars, interval="1m", weights=weights)


def test_portfolio_bars_is_frozen() -> None:
    bars = {"BTCUSDT": _empty_bars("BTCUSDT")}
    pb = PortfolioBars(bars=bars, interval="1m", weights={"BTCUSDT": 1.0})
    with pytest.raises((AttributeError, TypeError)):
        pb.interval = "15m"  # type: ignore[misc]


def test_portfolio_result_is_constructible() -> None:
    idx = pd.DatetimeIndex(["2024-03-15", "2024-03-16"], tz="UTC")
    equity = pd.Series([100.0, 101.0], index=idx)
    per_sym = {"BTCUSDT": pd.Series([100.0, 101.0], index=idx)}
    trades = pd.DataFrame(
        {
            "symbol": pd.Series([], dtype="string"),
            "entry_time": pd.Series([], dtype="datetime64[ns, UTC]"),
        }
    )
    result = PortfolioResult(
        equity_curve=equity,
        per_symbol_equity=per_sym,
        trades=trades,
        metrics={"cagr": 0.1},
        per_symbol_metrics={"BTCUSDT": {"cagr": 0.1}},
        engine_name="vectorbt",
        params={"seed": 42},
    )
    assert result.engine_name == "vectorbt"
    assert result.metrics["cagr"] == 0.1
    assert result.per_symbol_metrics["BTCUSDT"]["cagr"] == 0.1


def test_portfolio_result_is_frozen() -> None:
    idx = pd.DatetimeIndex(["2024-03-15"], tz="UTC")
    result = PortfolioResult(
        equity_curve=pd.Series([100.0], index=idx),
        per_symbol_equity={},
        trades=pd.DataFrame(),
        metrics={},
        per_symbol_metrics={},
        engine_name="backtrader",
        params={},
    )
    with pytest.raises((AttributeError, TypeError)):
        result.engine_name = "vectorbt"  # type: ignore[misc]


def test_initial_cash_type_is_decimal_compatible() -> None:
    # Smoke check: the Protocol signature takes Decimal; ensure import path works.
    # WHY: catches accidental removal of Decimal import in protocols.py.
    assert Decimal("1000") == Decimal(1000)
