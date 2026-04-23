"""Unit tests for kairos.backtest.walkforward."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Literal

import pandas as pd
import pytest

from kairos.backtest.protocols import (
    BacktestResult,
    BarsData,
    CostModel,
    Engine,
    EngineName,
    SignalFrame,
    Strategy,
    SymbolSpec,
)
from kairos.backtest.walkforward import (
    WalkForwardRun,
    WalkForwardSpec,
    WalkForwardWindow,
    run_walkforward,
    windows,
)
from kairos.data.oos_gate import OosGate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def ts(s: str) -> pd.Timestamp:
    return pd.Timestamp(s, tz="UTC")


def _make_gate(oos_start: str = "2025-07-01", oos_end: str = "2026-01-01") -> OosGate:
    return OosGate(oos_start=ts(oos_start), oos_end=ts(oos_end))


def _make_spec(
    train_days: int = 90,
    validate_days: int = 30,
    step_days: int = 30,
) -> WalkForwardSpec:
    return WalkForwardSpec(
        train_days=train_days,
        validate_days=validate_days,
        step_days=step_days,
    )


# ---------------------------------------------------------------------------
# Fake engine and strategy for smoke tests
# ---------------------------------------------------------------------------

def _empty_bars(symbol: str = "BTCUSDT", interval: str = "1m") -> BarsData:
    idx = pd.DatetimeIndex([], tz="UTC")
    ohlcv = pd.DataFrame(
        columns=["open", "high", "low", "close", "volume",
                 "quote_volume", "taker_buy_volume", "taker_buy_quote_volume", "trade_count"],
        index=idx,
    )
    return BarsData(ohlcv=ohlcv, symbol=symbol, interval=interval)  # type: ignore[arg-type]


def _make_backtest_result(engine_name: EngineName = "vectorbt") -> BacktestResult:
    idx = pd.DatetimeIndex([], tz="UTC")
    return BacktestResult(
        equity_curve=pd.Series([], index=idx, dtype=float),
        trades=pd.DataFrame(),
        metrics={},
        engine_name=engine_name,
        params={},
    )


class FakeEngine:
    """Minimal Engine stub that records calls and returns a trivial BacktestResult."""

    name: EngineName = "vectorbt"

    def __init__(self) -> None:
        self.calls: list[tuple[pd.Timestamp | None, pd.Timestamp | None]] = []

    def run(
        self,
        strategy: Strategy,
        data: BarsData,
        params: Mapping[str, object],
        costs: CostModel,
        symbol_spec: SymbolSpec,
        initial_cash: Decimal,
    ) -> BacktestResult:
        start = data.ohlcv.index.min() if len(data.ohlcv) > 0 else None
        end = data.ohlcv.index.max() if len(data.ohlcv) > 0 else None
        self.calls.append((start, end))
        return _make_backtest_result()


class FakeStrategy:
    """Minimal Strategy stub."""

    name = "fake"
    default_params: Mapping[str, object] = {}

    def generate_signals(
        self, data: BarsData, params: Mapping[str, object]
    ) -> SignalFrame:
        idx = data.ohlcv.index
        df = pd.DataFrame(
            {"side": 0, "size_hint": 0.0, "stop": float("nan"), "target": float("nan")},
            index=idx,
        )
        return SignalFrame(df=df)


def _make_symbol_spec() -> SymbolSpec:
    return SymbolSpec(
        symbol="BTCUSDT",
        tick_size=Decimal("0.1"),
        step_size=Decimal("0.001"),
        min_qty=Decimal("0.001"),
        min_notional=Decimal("5"),
    )


# ---------------------------------------------------------------------------
# windows() tests
# ---------------------------------------------------------------------------

class TestWindowsGeneration:

    def test_windows_tile_the_range(self) -> None:
        """365-day range with 90/30/30 → exactly 9 windows.

        Analysis (2024 is a leap year — 30-day steps are calendar-based):
        - First validate window with full train history: validate_start=2024-03-31
          (train_start = 2024-01-01 = range_start, no partial training).
        - Steps of 30 days yield windows through validate_start=2024-11-26.
        - Next would have validate_end=2025-01-25 > range_end 2025-01-01 → dropped.
        - Total: 9 windows.
        """
        spec = _make_spec(90, 30, 30)
        start = ts("2024-01-01")
        end = ts("2025-01-01")  # exactly 366 days (2024 is leap year)

        ws = windows(spec, start, end)

        # All validate windows must be within [start, end).
        for w in ws:
            assert w.validate_start >= start
            assert w.validate_end <= end
            assert w.train_start >= start
            assert w.train_end == w.validate_start

        # Count: validate windows starting at 2024-04-01 through 2024-12-01 = 9
        assert len(ws) == 9

    def test_partial_train_window_dropped(self) -> None:
        """Windows with train_start before range_start are dropped."""
        spec = _make_spec(90, 30, 30)
        start = ts("2024-01-01")
        end = ts("2025-01-01")

        ws = windows(spec, start, end)

        # First window must have train_start >= range_start
        for w in ws:
            assert w.train_start >= start, (
                f"Window has train_start {w.train_start} before range_start {start}"
            )

        # The very first possible validate_start is 2024-01-01 (range_start).
        # Its train_start would be 2024-01-01 - 90 days = 2023-10-03 < range_start → dropped.
        # Advance by step (30 days) until train_start >= range_start.
        # 2024 is a leap year: 2024-01-01 + 90 days = 2024-03-31.
        # So the first valid validate_start where train_start == range_start is 2024-03-31.
        first_window = ws[0]
        assert first_window.validate_start == ts("2024-03-31")
        assert first_window.train_start == ts("2024-01-01")

    def test_partial_validate_window_dropped(self) -> None:
        """Windows where validate_end > range_end are dropped."""
        spec = _make_spec(90, 30, 30)
        start = ts("2024-01-01")
        # End on the 15th: a validate window ending on Jan 31 would be > Dec 15.
        end = ts("2024-12-15")

        ws = windows(spec, start, end)

        # All validate windows must fit within range_end.
        for w in ws:
            assert w.validate_end <= end, (
                f"Window validate_end {w.validate_end} exceeds range_end {end}"
            )

        # The last validate window that fits: validate_start=2024-11-01, end=2024-12-01.
        # validate_start=2024-12-01 → validate_end=2024-12-31 > 2024-12-15 → dropped.
        last_window = ws[-1]
        assert last_window.validate_end <= end

    def test_windows_are_contiguous_validate_blocks(self) -> None:
        """validate_start of window N+1 == validate_end of window N."""
        spec = _make_spec(90, 30, 30)
        start = ts("2024-01-01")
        end = ts("2025-01-01")

        ws = windows(spec, start, end)
        assert len(ws) >= 2, "Need at least 2 windows to test contiguity"

        for i in range(len(ws) - 1):
            assert ws[i].validate_end == ws[i + 1].validate_start, (
                f"Gap between window {i} and {i+1}: "
                f"{ws[i].validate_end} != {ws[i+1].validate_start}"
            )

    def test_empty_range_returns_no_windows(self) -> None:
        """If range is too small for any window, return empty list."""
        spec = _make_spec(90, 30, 30)
        # Only 10 days: can't fit even a single validate window (30 days needed).
        start = ts("2024-01-01")
        end = ts("2024-01-11")

        ws = windows(spec, start, end)
        assert ws == []

    def test_windows_dataclass_immutable(self) -> None:
        """WalkForwardWindow is a frozen dataclass."""
        spec = _make_spec(90, 30, 30)
        start = ts("2024-01-01")
        end = ts("2025-01-01")

        ws = windows(spec, start, end)
        w = ws[0]
        with pytest.raises((AttributeError, TypeError)):
            w.train_start = ts("2023-01-01")  # type: ignore[misc]

    def test_non_utc_range_raises(self) -> None:
        """tz-naive timestamps raise ValueError."""
        spec = _make_spec(90, 30, 30)
        with pytest.raises(ValueError):
            windows(
                spec,
                pd.Timestamp("2024-01-01"),  # tz-naive
                pd.Timestamp("2025-01-01"),
            )

    def test_step_equals_validate_produces_non_overlapping(self) -> None:
        """When step_days == validate_days, validate windows are non-overlapping and contiguous."""
        spec = _make_spec(train_days=60, validate_days=30, step_days=30)
        start = ts("2024-01-01")
        end = ts("2024-12-01")

        ws = windows(spec, start, end)
        for i in range(len(ws) - 1):
            # No overlap: next window's validate_start == this window's validate_end
            assert ws[i].validate_end == ws[i + 1].validate_start

    def test_train_end_equals_validate_start(self) -> None:
        """For every window: train_end == validate_start (no gap between train and validate)."""
        spec = _make_spec(90, 30, 30)
        start = ts("2024-01-01")
        end = ts("2025-01-01")

        ws = windows(spec, start, end)
        for w in ws:
            assert w.train_end == w.validate_start


# ---------------------------------------------------------------------------
# run_walkforward() smoke test with mocked load_bars
# ---------------------------------------------------------------------------

class TestRunWalkforward:

    def test_run_walkforward_calls_engine_twice_per_window(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Engine.run is called exactly 2× per window (once train, once validate)."""
        spec = _make_spec(90, 30, 30)
        start = ts("2024-01-01")
        end = ts("2025-01-01")
        gate = _make_gate()

        engine = FakeEngine()
        strategy = FakeStrategy()

        # Monkeypatch load_bars to return empty BarsData without any file I/O.
        import kairos.backtest.walkforward as wf_module

        def fake_load_bars(
            symbol: str,
            interval: str,
            start: pd.Timestamp,
            end: pd.Timestamp,
            gate: OosGate,
            allow_oos: bool = False,
            data_root: Path | None = None,
        ) -> BarsData:
            return _empty_bars(symbol=symbol, interval=interval)  # type: ignore[arg-type]

        monkeypatch.setattr(wf_module, "load_bars", fake_load_bars)

        runs = run_walkforward(
            strategy=strategy,
            spec=spec,
            engine=engine,
            range_start=start,
            range_end=end,
            symbol="BTCUSDT",
            interval="1m",
            params={},
            costs=CostModel(),
            symbol_spec=_make_symbol_spec(),
            initial_cash=Decimal("10000"),
            gate=gate,
            data_root=tmp_path,
        )

        expected_windows = windows(spec, start, end)
        assert len(runs) == len(expected_windows)
        # Engine called exactly 2 times per window.
        assert len(engine.calls) == 2 * len(expected_windows)

    def test_run_walkforward_returns_walk_forward_runs(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """run_walkforward returns list of WalkForwardRun with correct window references."""
        spec = _make_spec(90, 30, 30)
        start = ts("2024-01-01")
        end = ts("2025-01-01")
        gate = _make_gate()

        engine = FakeEngine()
        strategy = FakeStrategy()

        import kairos.backtest.walkforward as wf_module

        def fake_load_bars(
            symbol: str,
            interval: str,
            start: pd.Timestamp,
            end: pd.Timestamp,
            gate: OosGate,
            allow_oos: bool = False,
            data_root: Path | None = None,
        ) -> BarsData:
            return _empty_bars(symbol=symbol, interval=interval)  # type: ignore[arg-type]

        monkeypatch.setattr(wf_module, "load_bars", fake_load_bars)

        runs = run_walkforward(
            strategy=strategy,
            spec=spec,
            engine=engine,
            range_start=start,
            range_end=end,
            symbol="BTCUSDT",
            interval="15m",
            params={"fast": 10, "slow": 50},
            costs=CostModel(),
            symbol_spec=_make_symbol_spec(),
            initial_cash=Decimal("50000"),
            gate=gate,
            data_root=tmp_path,
        )

        assert all(isinstance(r, WalkForwardRun) for r in runs)
        assert all(isinstance(r.train_result, BacktestResult) for r in runs)
        assert all(isinstance(r.validate_result, BacktestResult) for r in runs)

        # Windows in runs must match windows() output.
        expected_ws = windows(spec, start, end)
        for run, expected_w in zip(runs, expected_ws):
            assert run.window == expected_w
