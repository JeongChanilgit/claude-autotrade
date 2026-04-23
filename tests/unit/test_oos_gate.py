"""Unit tests for kairos.data.oos_gate."""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from kairos.data.oos_gate import OosGate, OosViolationError, load_bars


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def ts(s: str) -> pd.Timestamp:
    """Shorthand for a UTC-aware Timestamp."""
    return pd.Timestamp(s, tz="UTC")


OOS_START = ts("2024-07-01")
OOS_END = ts("2025-01-01")

GATE = OosGate(oos_start=OOS_START, oos_end=OOS_END)


# ---------------------------------------------------------------------------
# OosGate.check() tests
# ---------------------------------------------------------------------------

class TestOosGateCheck:
    """Core overlap logic tests."""

    def test_no_overlap_before_oos(self) -> None:
        """Range ends exactly at oos_start (half-open, touching) → no overlap → no raise."""
        # [2024-01-01, 2024-07-01) ends at oos_start — touching, not overlapping
        GATE.check(ts("2024-01-01"), OOS_START, allow_oos=False)

    def test_no_overlap_before_oos_strictly_inside_is(self) -> None:
        """Range ends well before oos_start → clearly no overlap → no raise."""
        GATE.check(ts("2024-01-01"), ts("2024-06-01"), allow_oos=False)

    def test_full_overlap_denied(self) -> None:
        """Range fully inside OOS, allow_oos=False → raises OosViolationError."""
        with pytest.raises(OosViolationError):
            GATE.check(ts("2024-08-01"), ts("2024-10-01"), allow_oos=False)

    def test_partial_overlap_start_before_end_after_oos_start(self) -> None:
        """Range ends after oos_start, allow_oos=False → raises."""
        # [2024-06-01, 2024-08-01) overlaps with [2024-07-01, 2025-01-01)
        with pytest.raises(OosViolationError):
            GATE.check(ts("2024-06-01"), ts("2024-08-01"), allow_oos=False)

    def test_overlap_allowed_when_flag_set(self) -> None:
        """Same partial overlap, allow_oos=True → no raise (for final OOS validation)."""
        # Must not raise even though there is an overlap
        GATE.check(ts("2024-06-01"), ts("2024-08-01"), allow_oos=True)

    def test_touching_boundary_oos_end_is_not_overlap(self) -> None:
        """Range starts exactly at oos_end (half-open) → touching, not overlapping → no raise."""
        # [2025-01-01, 2025-06-01) — range_start == oos_end, so start < oos_end is FALSE
        GATE.check(OOS_END, ts("2025-06-01"), allow_oos=False)

    def test_range_spanning_entire_oos_is_denied(self) -> None:
        """Range wraps around entire OOS window → raises."""
        with pytest.raises(OosViolationError):
            GATE.check(ts("2024-01-01"), ts("2025-06-01"), allow_oos=False)

    def test_range_starting_inside_oos_is_denied(self) -> None:
        """Range starts inside OOS and ends after → raises."""
        with pytest.raises(OosViolationError):
            GATE.check(ts("2024-09-01"), ts("2025-03-01"), allow_oos=False)


# ---------------------------------------------------------------------------
# OosGate construction validation
# ---------------------------------------------------------------------------

class TestOosGateConstruction:
    def test_tz_naive_start_raises(self) -> None:
        with pytest.raises(ValueError, match="UTC-aware"):
            OosGate(
                oos_start=pd.Timestamp("2024-07-01"),  # tz-naive
                oos_end=ts("2025-01-01"),
            )

    def test_tz_naive_end_raises(self) -> None:
        with pytest.raises(ValueError, match="UTC-aware"):
            OosGate(
                oos_start=ts("2024-07-01"),
                oos_end=pd.Timestamp("2025-01-01"),  # tz-naive
            )

    def test_start_after_end_raises(self) -> None:
        with pytest.raises(ValueError):
            OosGate(oos_start=ts("2025-01-01"), oos_end=ts("2024-07-01"))


# ---------------------------------------------------------------------------
# load_bars gate enforcement (no actual file I/O needed for gate raise)
# ---------------------------------------------------------------------------

class TestLoadBarsGate:
    def test_load_bars_raises_before_file_io(self, tmp_path: Path) -> None:
        """load_bars into gated region raises OosViolationError before touching disk."""
        gate = OosGate(oos_start=ts("2024-07-01"), oos_end=ts("2025-01-01"))
        # tmp_path is an empty directory → no parquet files exist.
        # The raise must come from the gate check, not from missing files.
        with pytest.raises(OosViolationError):
            load_bars(
                symbol="BTCUSDT",
                interval="1m",
                start=ts("2024-08-01"),
                end=ts("2024-09-01"),
                gate=gate,
                allow_oos=False,
                data_root=tmp_path,
            )

    def test_load_bars_with_allow_oos_reads_data(self, tmp_path: Path) -> None:
        """load_bars with allow_oos=True passes the gate check (then proceeds to I/O)."""
        gate = OosGate(oos_start=ts("2024-07-01"), oos_end=ts("2025-01-01"))
        # No files in tmp_path, but the gate must NOT raise — it should return empty BarsData.
        result = load_bars(
            symbol="BTCUSDT",
            interval="1m",
            start=ts("2024-08-01"),
            end=ts("2024-09-01"),
            gate=gate,
            allow_oos=True,
            data_root=tmp_path,
        )
        assert result.symbol == "BTCUSDT"
        assert result.interval == "1m"
        assert len(result.ohlcv) == 0  # no files → empty frame

    def test_load_bars_returns_bars_data_from_parquet(self, tmp_path: Path) -> None:
        """Integration smoke: load_bars reads synthetic parquet data correctly."""
        import pyarrow as pa
        import pyarrow.parquet as pq
        import numpy as np

        symbol = "BTCUSDT"
        interval = "1m"
        gate = OosGate(oos_start=ts("2024-07-01"), oos_end=ts("2025-01-01"))

        # Create synthetic parquet file matching SCHEMA_KLINES layout.
        day = pd.Timestamp("2024-01-15", tz="UTC")
        open_times = [int(day.timestamp() * 1000) + i * 60_000 for i in range(5)]
        close_times = [ot + 59_999 for ot in open_times]

        table = pa.table({
            "open_time": pa.array(open_times, type=pa.int64()),
            "close_time": pa.array(close_times, type=pa.int64()),
            "open": pa.array([100.0] * 5, type=pa.float64()),
            "high": pa.array([101.0] * 5, type=pa.float64()),
            "low": pa.array([99.0] * 5, type=pa.float64()),
            "close": pa.array([100.5] * 5, type=pa.float64()),
            "volume": pa.array([10.0] * 5, type=pa.float64()),
            "quote_volume": pa.array([1000.0] * 5, type=pa.float64()),
            "trade_count": pa.array([50] * 5, type=pa.int64()),
            "taker_buy_volume": pa.array([5.0] * 5, type=pa.float64()),
            "taker_buy_quote_volume": pa.array([500.0] * 5, type=pa.float64()),
            "symbol": pa.array([symbol] * 5, type=pa.string()),
            "interval": pa.array([interval] * 5, type=pa.string()),
        })

        # Write to the expected path structure.
        from kairos.data import paths as kpaths
        # Override DATA_ROOT by using data_root param; build path manually.
        from datetime import date
        fpath = (
            tmp_path / "klines"
            / f"symbol={symbol}" / f"interval={interval}"
            / "year=2024" / "month=01"
            / f"{symbol}-{interval}-2024-01-15.parquet"
        )
        fpath.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(table, fpath)

        result = load_bars(
            symbol=symbol,
            interval=interval,
            start=ts("2024-01-15"),
            end=ts("2024-01-16"),
            gate=gate,
            allow_oos=False,
            data_root=tmp_path,
        )

        assert len(result.ohlcv) == 5
        assert result.symbol == symbol
        assert result.interval == interval
        assert result.ohlcv.index.tz is not None  # UTC-aware
        assert result.funding is None
        assert result.metrics is None
