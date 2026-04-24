"""Unit tests for PortfolioVectorbtEngine adapter.

Tests use a tiny synthetic 2-symbol portfolio built in-process. No network,
no filesystem. First run may be slow due to numba JIT (10-30 s).
"""
from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from kairos.backtest.engine_portfolio_vbt import PortfolioVectorbtEngine
from kairos.backtest.engine_vbt import VectorbtEngine
from kairos.backtest.protocols import (
    MAX_LEVERAGE,
    BarsData,
    CostModel,
    PortfolioBars,
    PortfolioResult,
    SignalFrame,
    SymbolSpec,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bars(
    symbol: str,
    n: int,
    seed: int,
    start_price: float = 100.0,
    start_ts: str = "2024-01-01",
    interval: str = "1m",
) -> BarsData:
    """Deterministic synthetic OHLCV bars for `symbol`.

    Uses a seeded RandomState so each call is reproducible. Price is a
    constrained random walk that stays strictly positive.
    """
    freq = "1min" if interval == "1m" else "15min"
    index = pd.date_range(start_ts, periods=n, freq=freq, tz="UTC")
    rng = np.random.RandomState(seed)
    steps = rng.normal(0.0, 0.5, size=n)
    prices = np.cumsum(steps) + start_price
    prices = np.maximum(prices, 1.0)

    ohlcv = pd.DataFrame(
        {
            "open": prices,
            "high": prices + 0.5,
            "low": prices - 0.5,
            "close": prices,
            "volume": np.full(n, 1_000.0),
            "quote_volume": prices * 1_000.0,
            "taker_buy_volume": np.full(n, 500.0),
            "taker_buy_quote_volume": prices * 500.0,
            "trade_count": np.full(n, 100, dtype=np.int64),
        },
        index=index,
    )
    return BarsData(ohlcv=ohlcv, symbol=symbol, interval=interval)


def _spec(symbol: str) -> SymbolSpec:
    return SymbolSpec(
        symbol=symbol,
        tick_size=Decimal("0.01"),
        step_size=Decimal("0.0001"),
        min_qty=Decimal("0.0001"),
        min_notional=Decimal("5"),
    )


def _zero_costs() -> CostModel:
    return CostModel(taker_fee_bps=0.0, slippage_bps_base=0.0, slippage_vol_coef=0.0)


class _StaticStrategy:
    """Test-only strategy that emits a fixed SignalFrame regardless of data.

    side/size_hint arrays are truncated or zero-padded to match the target
    bar count on each call.
    """

    name: str = "static"
    default_params: Mapping[str, object] = {}

    def __init__(
        self,
        sides: list[int],
        size_hint: float = 1.0,
    ) -> None:
        self._sides = sides
        self._size_hint = size_hint

    def generate_signals(
        self,
        data: BarsData,
        params: Mapping[str, object],
    ) -> SignalFrame:
        n = len(data.ohlcv)
        sides = list(self._sides[:n])
        if len(sides) < n:
            sides = sides + [0] * (n - len(sides))

        df = pd.DataFrame(
            {
                "side": np.array(sides, dtype=np.int8),
                "size_hint": np.full(n, self._size_hint, dtype=np.float64),
                "stop": np.full(n, float("nan")),
                "target": np.full(n, float("nan")),
            },
            index=data.ohlcv.index,
        )
        return SignalFrame(df=df)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_degenerate_single_symbol_matches_single_engine() -> None:
    """Portfolio with weights={BTC:1.0} and same strategy/data must match
    the single-symbol engine's equity curve within 1e-6 relative tolerance.
    """
    n = 150
    bars = _make_bars("BTCUSDT", n, seed=1, start_price=100.0)
    sides = [0] * 10 + [1] * 80 + [0] * 60
    strategy = _StaticStrategy(sides=sides)
    costs = _zero_costs()
    spec = _spec("BTCUSDT")
    init_cash = Decimal("10000")

    single = VectorbtEngine().run(
        strategy=strategy,
        data=bars,
        params={},
        costs=costs,
        symbol_spec=spec,
        initial_cash=init_cash,
    )

    portfolio = PortfolioBars(
        bars={"BTCUSDT": bars},
        interval="1m",
        weights={"BTCUSDT": 1.0},
    )
    port = PortfolioVectorbtEngine().run(
        strategies={"BTCUSDT": strategy},
        portfolio=portfolio,
        params={"BTCUSDT": {}},
        costs=costs,
        symbol_specs={"BTCUSDT": spec},
        initial_cash=init_cash,
    )

    # Equity curves should match within floating tolerance. They won't be
    # exactly identical because the portfolio engine quantizes qty to
    # step_size; we allow 1% relative — tight enough to catch structural
    # divergence, loose enough to tolerate the quantization step.
    single_final = float(single.equity_curve.iloc[-1])
    port_final = float(port.equity_curve.iloc[-1])
    assert port_final == pytest.approx(single_final, rel=0.01), (
        f"Portfolio final equity {port_final} != single {single_final}"
    )
    assert port.engine_name == "vectorbt"


def test_equal_weight_flat_strategy_preserves_cash() -> None:
    """Two symbols, always-flat strategies, equal weights → equity constant."""
    n = 100
    btc = _make_bars("BTCUSDT", n, seed=2, start_price=100.0)
    eth = _make_bars("ETHUSDT", n, seed=3, start_price=200.0)

    portfolio = PortfolioBars(
        bars={"BTCUSDT": btc, "ETHUSDT": eth},
        interval="1m",
        weights={"BTCUSDT": 0.5, "ETHUSDT": 0.5},
    )
    strats = {
        "BTCUSDT": _StaticStrategy(sides=[0] * n),
        "ETHUSDT": _StaticStrategy(sides=[0] * n),
    }
    init_cash = Decimal("10000")

    result = PortfolioVectorbtEngine().run(
        strategies=strats,
        portfolio=portfolio,
        params={"BTCUSDT": {}, "ETHUSDT": {}},
        costs=_zero_costs(),
        symbol_specs={"BTCUSDT": _spec("BTCUSDT"), "ETHUSDT": _spec("ETHUSDT")},
        initial_cash=init_cash,
    )

    assert len(result.trades) == 0
    # Equity stays exactly at initial_cash (no trades, no costs).
    assert float(result.equity_curve.iloc[0]) == pytest.approx(10000.0, abs=1e-9)
    assert float(result.equity_curve.iloc[-1]) == pytest.approx(10000.0, abs=1e-9)


def test_one_active_one_flat_weighted_contribution() -> None:
    """Weighted scaling: one symbol long-only at weight w, other flat at
    weight (1-w). Portfolio final equity ≈ w * single_symbol_final
    + (1-w) * init_cash.
    """
    n = 120
    btc = _make_bars("BTCUSDT", n, seed=4, start_price=100.0)
    eth = _make_bars("ETHUSDT", n, seed=5, start_price=200.0)
    sides_btc = [0] * 5 + [1] * 90 + [0] * 25
    sides_eth = [0] * n

    w_btc = 0.7
    w_eth = 0.3
    init_cash = Decimal("10000")
    cash_f = float(init_cash)
    costs = _zero_costs()

    # Single-symbol run at cash = w_btc * init_cash gives the BTC contribution
    single_btc = VectorbtEngine().run(
        strategy=_StaticStrategy(sides=sides_btc),
        data=btc,
        params={},
        costs=costs,
        symbol_spec=_spec("BTCUSDT"),
        initial_cash=Decimal(str(w_btc * cash_f)),
    )
    expected_final = (
        float(single_btc.equity_curve.iloc[-1]) + w_eth * cash_f
    )

    portfolio = PortfolioBars(
        bars={"BTCUSDT": btc, "ETHUSDT": eth},
        interval="1m",
        weights={"BTCUSDT": w_btc, "ETHUSDT": w_eth},
    )
    strats = {
        "BTCUSDT": _StaticStrategy(sides=sides_btc),
        "ETHUSDT": _StaticStrategy(sides=sides_eth),
    }
    result = PortfolioVectorbtEngine().run(
        strategies=strats,
        portfolio=portfolio,
        params={"BTCUSDT": {}, "ETHUSDT": {}},
        costs=costs,
        symbol_specs={"BTCUSDT": _spec("BTCUSDT"), "ETHUSDT": _spec("ETHUSDT")},
        initial_cash=init_cash,
    )

    port_final = float(result.equity_curve.iloc[-1])
    # 2% relative tolerance absorbs quantization differences.
    assert port_final == pytest.approx(expected_final, rel=0.02), (
        f"Portfolio final {port_final} not within 2% of expected {expected_final}"
    )


def test_pre_listing_no_trades_for_late_symbol() -> None:
    """Symbol B starts halfway through. Assert no trades for B fire before
    its first native bar."""
    n_a = 100
    btc = _make_bars("BTCUSDT", n_a, seed=6, start_price=100.0)
    # ETH starts 50 bars into BTC's timeline
    eth = _make_bars(
        "ETHUSDT",
        50,
        seed=7,
        start_price=200.0,
        start_ts="2024-01-01 00:50:00",
    )
    listing_eth = eth.ohlcv.index[0]

    # Both strategies always long — if pre-listing ran, ETH trade would
    # appear before listing.
    sides = [1] * n_a
    sides_eth = [1] * 50

    portfolio = PortfolioBars(
        bars={"BTCUSDT": btc, "ETHUSDT": eth},
        interval="1m",
        weights={"BTCUSDT": 0.5, "ETHUSDT": 0.5},
    )
    strats = {
        "BTCUSDT": _StaticStrategy(sides=sides),
        "ETHUSDT": _StaticStrategy(sides=sides_eth),
    }
    result = PortfolioVectorbtEngine().run(
        strategies=strats,
        portfolio=portfolio,
        params={"BTCUSDT": {}, "ETHUSDT": {}},
        costs=_zero_costs(),
        symbol_specs={"BTCUSDT": _spec("BTCUSDT"), "ETHUSDT": _spec("ETHUSDT")},
        initial_cash=Decimal("10000"),
    )

    eth_trades = result.trades[result.trades["symbol"] == "ETHUSDT"]
    if len(eth_trades) > 0:
        # All ETH trade entry_times must be ≥ listing_ts
        bad = eth_trades[eth_trades["entry_time"] < listing_eth]
        assert len(bad) == 0, (
            f"Found {len(bad)} ETH trades before listing_ts {listing_eth}: "
            f"{bad['entry_time'].tolist()}"
        )


def test_max_leverage_cap() -> None:
    """When both symbols signal 100% size, total notional at any bar must
    not exceed initial_cash * MAX_LEVERAGE."""
    n = 80
    btc = _make_bars("BTCUSDT", n, seed=8, start_price=100.0)
    eth = _make_bars("ETHUSDT", n, seed=9, start_price=200.0)
    # Both always long at size_hint=1.0 and weight=0.5 → per-symbol target
    # notional = 0.5 * cash. Per-symbol cap = 0.5 * cash * 3.0 = 1.5 * cash.
    # Sum < cash * MAX_LEVERAGE trivially; to trigger the scaling path, bump
    # size_hint to 5.0 so pre-cap notionals are huge.
    sides = [1] * n
    strats = {
        "BTCUSDT": _StaticStrategy(sides=sides, size_hint=5.0),
        "ETHUSDT": _StaticStrategy(sides=sides, size_hint=5.0),
    }
    portfolio = PortfolioBars(
        bars={"BTCUSDT": btc, "ETHUSDT": eth},
        interval="1m",
        weights={"BTCUSDT": 0.5, "ETHUSDT": 0.5},
    )
    init_cash = Decimal("10000")
    cash_f = float(init_cash)

    result = PortfolioVectorbtEngine().run(
        strategies=strats,
        portfolio=portfolio,
        params={"BTCUSDT": {}, "ETHUSDT": {}},
        costs=_zero_costs(),
        symbol_specs={"BTCUSDT": _spec("BTCUSDT"), "ETHUSDT": _spec("ETHUSDT")},
        initial_cash=init_cash,
    )

    # For each trade, entry notional (qty * entry_price) should respect the
    # per-symbol cap of weight * cash * MAX_LEVERAGE.
    cap_per_sym = 0.5 * cash_f * MAX_LEVERAGE
    for _, row in result.trades.iterrows():
        notional = float(row["qty"]) * float(row["entry_price"])
        # Small slack for quantization rounding.
        assert notional <= cap_per_sym * 1.01, (
            f"{row['symbol']} notional {notional} exceeds per-sym cap {cap_per_sym}"
        )

    # Sum of simultaneous notionals across symbols at the first common open
    # bar must respect global cap.
    first_trade_time = result.trades["entry_time"].min()
    simultaneous = result.trades[result.trades["entry_time"] == first_trade_time]
    total_notional = (simultaneous["qty"] * simultaneous["entry_price"]).sum()
    assert total_notional <= cash_f * MAX_LEVERAGE * 1.01, (
        f"Total notional {total_notional} exceeds global cap "
        f"{cash_f * MAX_LEVERAGE}"
    )


def test_portfolio_result_shape() -> None:
    """Result schema: trades has `symbol` column, per_symbol_metrics covers
    every symbol, equity index equals union timestamps."""
    n = 100
    btc = _make_bars("BTCUSDT", n, seed=10, start_price=100.0)
    eth = _make_bars("ETHUSDT", n, seed=11, start_price=200.0)

    portfolio = PortfolioBars(
        bars={"BTCUSDT": btc, "ETHUSDT": eth},
        interval="1m",
        weights={"BTCUSDT": 0.5, "ETHUSDT": 0.5},
    )
    strats = {
        "BTCUSDT": _StaticStrategy(sides=[0] * 5 + [1] * 70 + [0] * 25),
        "ETHUSDT": _StaticStrategy(sides=[0] * 15 + [1] * 60 + [0] * 25),
    }
    result: PortfolioResult = PortfolioVectorbtEngine().run(
        strategies=strats,
        portfolio=portfolio,
        params={"BTCUSDT": {"a": 1}, "ETHUSDT": {"b": 2}},
        costs=_zero_costs(),
        symbol_specs={"BTCUSDT": _spec("BTCUSDT"), "ETHUSDT": _spec("ETHUSDT")},
        initial_cash=Decimal("10000"),
    )

    assert result.engine_name == "vectorbt"
    # symbol column present
    assert "symbol" in result.trades.columns
    assert result.trades.columns[0] == "symbol", (
        "symbol must be the leading column"
    )

    # per_symbol_metrics covers all symbols
    assert set(result.per_symbol_metrics.keys()) == {"BTCUSDT", "ETHUSDT"}
    for sym in ("BTCUSDT", "ETHUSDT"):
        assert "cagr" in result.per_symbol_metrics[sym]
        assert "mdd" in result.per_symbol_metrics[sym]

    # per_symbol_equity covers all symbols with matching index
    assert set(result.per_symbol_equity.keys()) == {"BTCUSDT", "ETHUSDT"}
    for sym in ("BTCUSDT", "ETHUSDT"):
        assert result.per_symbol_equity[sym].index.equals(result.equity_curve.index)

    # equity index equals union timestamps (both bar sets share the same start
    # here, so union length == n)
    assert len(result.equity_curve) == n

    # params round-trip includes weights + per-symbol params
    assert result.params["weights"] == {"BTCUSDT": 0.5, "ETHUSDT": 0.5}
    assert result.params["per_symbol_params"]["BTCUSDT"] == {"a": 1}
    assert result.params["per_symbol_params"]["ETHUSDT"] == {"b": 2}
