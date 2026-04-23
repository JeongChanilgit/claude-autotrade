"""Unit tests for kairos.data.download_metrics."""
from __future__ import annotations

import hashlib
import io
import zipfile
from datetime import date
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from kairos.data.download_metrics import MetricsDownloader, _csv_bytes_to_table
from kairos.data.http import NotFoundError
from kairos.data.protocols import SCHEMA_METRICS, SchemaMismatchError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HEADER = (
    "create_time,symbol,sum_open_interest,sum_open_interest_value,"
    "count_toptrader_long_short_ratio,sum_toptrader_long_short_ratio,"
    "count_long_short_ratio,sum_taker_long_short_vol_ratio\n"
)

_ROWS_BTCUSDT = [
    "2024-03-15 00:05:00,BTCUSDT,84043.0430000000000000,6016044742.7647000000000000,1.76479256,1.53693400,1.61288163,2.17419200\n",
    "2024-03-15 00:10:00,BTCUSDT,84042.4920000000000000,6009744134.9328000000000000,1.76282825,1.53769600,1.61028785,0.80700400\n",
    "2024-03-15 23:55:00,BTCUSDT,84040.0000000000000000,6000000000.0000000000000000,1.70000000,1.50000000,1.60000000,1.00000000\n",
    "2024-03-16 00:00:00,BTCUSDT,84050.0000000000000000,6001000000.0000000000000000,1.71000000,1.51000000,1.61000000,1.10000000\n",
]


def _make_csv_bytes(rows: list[str] | None = None, symbol: str = "BTCUSDT") -> bytes:
    lines: list[str] = [_HEADER]
    lines.extend(rows or _ROWS_BTCUSDT)
    return "".join(lines).encode("utf-8")


def _make_zip(csv_filename: str, csv_bytes: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(csv_filename, csv_bytes)
    return buf.getvalue()


def _make_checksum(zip_bytes: bytes, zip_filename: str) -> bytes:
    h = hashlib.sha256(zip_bytes).hexdigest()
    return f"{h}  {zip_filename}\n".encode("ascii")


def _write_minimal_parquet(path: Path, symbol: str = "BTCUSDT") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    csv_bytes = _make_csv_bytes(symbol=symbol)
    table = _csv_bytes_to_table(csv_bytes, symbol)
    pq.write_table(table, path, compression="snappy", use_dictionary=["symbol"])


# ---------------------------------------------------------------------------
# Test 1: Timestamp parsing — known UTC-ms values
# ---------------------------------------------------------------------------

def test_csv_to_table_parses_timestamp_correctly() -> None:
    # "2024-03-15 00:05:00" UTC → midnight 2024-03-15 = 1710460800s + 300s = 1710461100s → 1710461100000 ms
    # "2024-03-15 23:55:00" UTC → 1710460800 + 86100 = 1710546900s → 1710546900000 ms
    # "2024-03-16 00:00:00" UTC → 1710460800 + 86400 = 1710547200s → 1710547200000 ms
    rows = [
        "2024-03-15 00:05:00,BTCUSDT,84043.0,6016044742.76,1.764,1.536,1.612,2.174\n",
        "2024-03-15 23:55:00,BTCUSDT,84040.0,6000000000.00,1.700,1.500,1.600,1.000\n",
        "2024-03-16 00:00:00,BTCUSDT,84050.0,6001000000.00,1.710,1.510,1.610,1.100\n",
    ]
    csv_bytes = _make_csv_bytes(rows=rows)
    table = _csv_bytes_to_table(csv_bytes, "BTCUSDT")

    timestamps = table.column("create_time").to_pylist()
    assert timestamps[0] == 1710461100000, f"Expected 1710461100000, got {timestamps[0]}"
    assert timestamps[1] == 1710546900000, f"Expected 1710546900000, got {timestamps[1]}"
    assert timestamps[2] == 1710547200000, f"Expected 1710547200000, got {timestamps[2]}"


# ---------------------------------------------------------------------------
# Test 2: Schema matches SCHEMA_METRICS exactly
# ---------------------------------------------------------------------------

def test_csv_to_table_schema_matches_metrics_schema() -> None:
    csv_bytes = _make_csv_bytes()
    table = _csv_bytes_to_table(csv_bytes, "BTCUSDT")

    assert table.schema.equals(SCHEMA_METRICS), (
        f"Schema mismatch.\nGot:      {table.schema}\nExpected: {SCHEMA_METRICS}"
    )
    assert len(table) == len(_ROWS_BTCUSDT)


# ---------------------------------------------------------------------------
# Test 3: Idempotency — skip when parquet already exists
# ---------------------------------------------------------------------------

def test_fetch_skips_when_parquet_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("kairos.data.paths.DATA_ROOT", tmp_path)

    period = date(2024, 3, 15)
    dl = MetricsDownloader()
    target = dl.expected_path("BTCUSDT", period)
    _write_minimal_parquet(target)

    monkeypatch.setattr(
        "kairos.data.download_metrics.http_get_bytes",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("http_get_bytes must not be called")),
    )

    result = dl.fetch("BTCUSDT", period)

    assert result.skipped is True
    assert result.rows >= 1
    assert result.checksum_ok is True
    assert result.path == target


# ---------------------------------------------------------------------------
# Test 4: 404 → returns skipped with zero rows
# ---------------------------------------------------------------------------

def test_fetch_404_returns_skipped_with_zero_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("kairos.data.paths.DATA_ROOT", tmp_path)

    def _raise_not_found(url: str, client: object = None) -> bytes:
        raise NotFoundError(url)

    monkeypatch.setattr("kairos.data.download_metrics.http_get_bytes", _raise_not_found)

    dl = MetricsDownloader()
    result = dl.fetch("BTCUSDT", date(2024, 3, 15))

    assert result.rows == 0
    assert result.checksum_ok is False
    assert result.skipped is True
    assert not result.path.exists()


# ---------------------------------------------------------------------------
# Test 5: Symbol mismatch in CSV raises SchemaMismatchError
# ---------------------------------------------------------------------------

def test_fetch_symbol_mismatch_raises_schema_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("kairos.data.paths.DATA_ROOT", tmp_path)

    eth_rows = [
        "2024-03-15 00:05:00,ETHUSDT,5000.0,500000000.0,1.5,1.4,1.3,1.2\n",
        "2024-03-15 00:10:00,ETHUSDT,5001.0,500100000.0,1.5,1.4,1.3,1.2\n",
    ]
    csv_bytes = _make_csv_bytes(rows=eth_rows, symbol="ETHUSDT")
    zip_csv_name = "BTCUSDT-metrics-2024-03-15.csv"
    zip_filename = "BTCUSDT-metrics-2024-03-15.zip"
    zip_bytes = _make_zip(zip_csv_name, csv_bytes)
    checksum_bytes = _make_checksum(zip_bytes, zip_filename)

    call_count = 0

    def _fake_http(url: str, client: object = None) -> bytes:
        nonlocal call_count
        call_count += 1
        return zip_bytes if call_count == 1 else checksum_bytes

    monkeypatch.setattr("kairos.data.download_metrics.http_get_bytes", _fake_http)

    dl = MetricsDownloader()
    period = date(2024, 3, 15)
    target = dl.expected_path("BTCUSDT", period)

    with pytest.raises(SchemaMismatchError):
        dl.fetch("BTCUSDT", period)

    assert not target.exists(), "Parquet must not be written on SchemaMismatchError"


# ---------------------------------------------------------------------------
# Test 6: Happy path — writes parquet with correct schema and row count
# ---------------------------------------------------------------------------

def test_fetch_happy_path_writes_parquet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("kairos.data.paths.DATA_ROOT", tmp_path)

    csv_bytes = _make_csv_bytes()
    zip_csv_name = "BTCUSDT-metrics-2024-03-15.csv"
    zip_filename = "BTCUSDT-metrics-2024-03-15.zip"
    zip_bytes = _make_zip(zip_csv_name, csv_bytes)
    checksum_bytes = _make_checksum(zip_bytes, zip_filename)

    call_count = 0

    def _fake_http(url: str, client: object = None) -> bytes:
        nonlocal call_count
        call_count += 1
        return zip_bytes if call_count == 1 else checksum_bytes

    monkeypatch.setattr("kairos.data.download_metrics.http_get_bytes", _fake_http)

    dl = MetricsDownloader()
    period = date(2024, 3, 15)
    result = dl.fetch("BTCUSDT", period)

    assert result.skipped is False
    assert result.rows == len(_ROWS_BTCUSDT)
    assert result.checksum_ok is True

    target = dl.expected_path("BTCUSDT", period)
    assert target.exists()

    written = pq.ParquetFile(target).read()
    assert written.schema.equals(SCHEMA_METRICS)
    assert len(written) == len(_ROWS_BTCUSDT)
