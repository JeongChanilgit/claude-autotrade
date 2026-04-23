"""Tests for kairos.data.quality_report."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from kairos.data import paths
from kairos.data.protocols import SCHEMA_KLINES
from kairos.data.quality_report import generate_report


def _write_klines_1m(tmp_path: Path, day: date, n_rows: int, bad_low: bool = False) -> None:
    target = paths.parquet_path("klines_1m", "BTCUSDT", day)
    target = tmp_path / target.relative_to(paths.DATA_ROOT)
    target.parent.mkdir(parents=True, exist_ok=True)

    base = 1710460800000
    low_vals = [1.0] * n_rows
    if bad_low and n_rows > 0:
        low_vals[0] = 999.0  # low > high

    data = {
        "open_time": [base + 60_000 * i for i in range(n_rows)],
        "close_time": [base + 60_000 * i + 59_999 for i in range(n_rows)],
        "open": [1.0] * n_rows,
        "high": [1.0] * n_rows,
        "low": low_vals,
        "close": [1.0] * n_rows,
        "volume": [1.0] * n_rows,
        "quote_volume": [1.0] * n_rows,
        "trade_count": [1] * n_rows,
        "taker_buy_volume": [0.5] * n_rows,
        "taker_buy_quote_volume": [0.5] * n_rows,
        "symbol": ["BTCUSDT"] * n_rows,
        "interval": ["1m"] * n_rows,
    }
    table = pa.Table.from_pydict(data, schema=SCHEMA_KLINES)
    pq.write_table(table, target, compression="snappy")


def test_missing_periods_reported(tmp_path: Path) -> None:
    _write_klines_1m(tmp_path, date(2024, 3, 1), 1440)
    _write_klines_1m(tmp_path, date(2024, 3, 3), 1440)

    report = generate_report(
        ["BTCUSDT"], date(2024, 3, 1), date(2024, 3, 5), data_root=tmp_path
    )

    klines_1m = next(r for r in report.records if r.dataset == "klines_1m")
    assert klines_1m.expected_periods == 5
    assert klines_1m.present_periods == 2
    assert klines_1m.missing_periods == [date(2024, 3, 2), date(2024, 3, 4), date(2024, 3, 5)]


def test_row_count_anomaly_klines_1m(tmp_path: Path) -> None:
    _write_klines_1m(tmp_path, date(2024, 3, 1), 10)

    report = generate_report(
        ["BTCUSDT"], date(2024, 3, 1), date(2024, 3, 1), data_root=tmp_path
    )

    klines_1m = next(r for r in report.records if r.dataset == "klines_1m")
    assert len(klines_1m.row_count_anomalies) == 1
    period, rows, reason = klines_1m.row_count_anomalies[0]
    assert period == date(2024, 3, 1)
    assert rows == 10
    assert "row count" in reason


def test_ohlc_inconsistency_flagged(tmp_path: Path) -> None:
    _write_klines_1m(tmp_path, date(2024, 3, 1), 1440, bad_low=True)

    report = generate_report(
        ["BTCUSDT"], date(2024, 3, 1), date(2024, 3, 1), data_root=tmp_path
    )

    klines_1m = next(r for r in report.records if r.dataset == "klines_1m")
    assert len(klines_1m.price_anomalies) == 1
    period, count, reason = klines_1m.price_anomalies[0]
    assert period == date(2024, 3, 1)
    assert count >= 1
    assert "OHLC" in reason


def test_missing_overflow_truncates_preview(tmp_path: Path) -> None:
    # 30-day window, nothing written → 30 missing days, preview should cap at 20
    report = generate_report(
        ["BTCUSDT"], date(2024, 3, 1), date(2024, 3, 30), data_root=tmp_path
    )
    klines_1m = next(r for r in report.records if r.dataset == "klines_1m")
    assert len(klines_1m.missing_periods) == 20
    assert klines_1m.missing_overflow == 10
