"""Unit tests for kairos.data.download_klines."""
from __future__ import annotations

import hashlib
import io
import zipfile
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from kairos.data.download_klines import KlinesDownloader, _csv_bytes_to_table
from kairos.data.http import NotFoundError
from kairos.data.protocols import SCHEMA_KLINES, ChecksumError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HEADER = (
    "open_time,open,high,low,close,volume,close_time,"
    "quote_volume,count,taker_buy_volume,taker_buy_quote_volume,ignore\n"
)

_ROWS = [
    "1710460800000,71455.60,71537.50,71440.10,71534.60,334.547,1710460859999,23913742.891,3967,260.797,18643498.1159,0\n",
    "1710460860000,71534.60,71600.00,71500.00,71580.00,200.000,1710460919999,14300000.000,2500,150.000,10725000.000,0\n",
    "1710460920000,71580.00,71650.00,71560.00,71620.00,150.000,1710460979999,10740000.000,1800,100.000,7160000.000,0\n",
]


def _make_csv_bytes(rows: list[str] | None = None, with_header: bool = True) -> bytes:
    lines: list[str] = []
    if with_header:
        lines.append(_HEADER)
    lines.extend(rows or _ROWS)
    return "".join(lines).encode("utf-8")


def _make_zip(csv_filename: str, csv_bytes: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(csv_filename, csv_bytes)
    return buf.getvalue()


def _make_checksum(zip_bytes: bytes, zip_filename: str) -> bytes:
    h = hashlib.sha256(zip_bytes).hexdigest()
    return f"{h}  {zip_filename}\n".encode("ascii")


def _write_minimal_parquet(path: Path, symbol: str = "BTCUSDT", interval: str = "1m") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    csv_bytes = _make_csv_bytes()
    table = _csv_bytes_to_table(csv_bytes, symbol, interval)
    pq.write_table(table, path, compression="snappy", use_dictionary=["symbol", "interval"])


# ---------------------------------------------------------------------------
# Test 1: CSV → Arrow transform
# ---------------------------------------------------------------------------

def test_csv_to_table_drops_ignore_and_adds_symbol_interval() -> None:
    csv_bytes = _make_csv_bytes()
    table = _csv_bytes_to_table(csv_bytes, "BTCUSDT", "1m")

    assert table.schema.equals(SCHEMA_KLINES), (
        f"Schema mismatch.\nGot:      {table.schema}\nExpected: {SCHEMA_KLINES}"
    )
    assert len(table) == 3
    assert "ignore" not in table.schema.names

    assert table.column("symbol").to_pylist() == ["BTCUSDT", "BTCUSDT", "BTCUSDT"]
    assert table.column("interval").to_pylist() == ["1m", "1m", "1m"]
    assert table.column("open_time").to_pylist()[0] == 1710460800000
    assert table.column("trade_count").to_pylist()[0] == 3967


def test_csv_to_table_without_header() -> None:
    csv_bytes = _make_csv_bytes(with_header=False)
    table = _csv_bytes_to_table(csv_bytes, "ETHUSDT", "15m")

    assert table.schema.equals(SCHEMA_KLINES)
    assert len(table) == 3
    assert table.column("symbol").to_pylist()[0] == "ETHUSDT"
    assert table.column("interval").to_pylist()[0] == "15m"


# ---------------------------------------------------------------------------
# Test 2: Idempotency — skip when parquet already exists
# ---------------------------------------------------------------------------

def test_fetch_skips_when_parquet_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("kairos.data.paths.DATA_ROOT", tmp_path)

    period = date(2024, 3, 15)
    dl = KlinesDownloader("1m")
    target = dl.expected_path("BTCUSDT", period)
    _write_minimal_parquet(target)

    monkeypatch.setattr(
        "kairos.data.download_klines.http_get_bytes",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("http_get_bytes must not be called")),
    )

    result = dl.fetch("BTCUSDT", period)

    assert result.skipped is True
    assert result.rows >= 1
    assert result.checksum_ok is True
    assert result.path == target


# ---------------------------------------------------------------------------
# Test 3: 404 → returns skipped with zero rows
# ---------------------------------------------------------------------------

def test_fetch_404_returns_skipped_with_zero_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("kairos.data.paths.DATA_ROOT", tmp_path)

    def _raise_not_found(url: str, client: object = None) -> bytes:
        raise NotFoundError(url)

    monkeypatch.setattr("kairos.data.download_klines.http_get_bytes", _raise_not_found)

    dl = KlinesDownloader("15m")
    result = dl.fetch("BTCUSDT", date(2024, 3, 15))

    assert result.rows == 0
    assert result.checksum_ok is False
    assert result.skipped is True


# ---------------------------------------------------------------------------
# Test 4: Checksum mismatch raises ChecksumError and cleans up
# ---------------------------------------------------------------------------

def test_fetch_checksum_mismatch_raises_and_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("kairos.data.paths.DATA_ROOT", tmp_path)

    csv_bytes = _make_csv_bytes()
    zip_filename = "BTCUSDT-1m-2024-03-15.zip"
    zip_bytes = _make_zip(zip_filename.replace(".zip", ".csv"), csv_bytes)
    bad_checksum = ("0" * 64 + "  " + zip_filename + "\n").encode("ascii")

    call_count = 0

    def _fake_http(url: str, client: object = None) -> bytes:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return zip_bytes
        return bad_checksum

    monkeypatch.setattr("kairos.data.download_klines.http_get_bytes", _fake_http)

    dl = KlinesDownloader("1m")
    target = dl.expected_path("BTCUSDT", date(2024, 3, 15))

    with pytest.raises(ChecksumError):
        dl.fetch("BTCUSDT", date(2024, 3, 15))

    assert not target.exists(), "Partial parquet must be deleted on ChecksumError"


# ---------------------------------------------------------------------------
# Test 5: Happy path — writes parquet with correct schema and row count
# ---------------------------------------------------------------------------

def test_fetch_happy_path_writes_parquet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("kairos.data.paths.DATA_ROOT", tmp_path)

    csv_bytes = _make_csv_bytes()
    zip_csv_name = "BTCUSDT-1m-2024-03-15.csv"
    zip_filename = "BTCUSDT-1m-2024-03-15.zip"
    zip_bytes = _make_zip(zip_csv_name, csv_bytes)
    checksum_bytes = _make_checksum(zip_bytes, zip_filename)

    call_count = 0

    def _fake_http(url: str, client: object = None) -> bytes:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return zip_bytes
        return checksum_bytes

    monkeypatch.setattr("kairos.data.download_klines.http_get_bytes", _fake_http)

    dl = KlinesDownloader("1m")
    period = date(2024, 3, 15)
    result = dl.fetch("BTCUSDT", period)

    assert result.skipped is False
    assert result.rows == 3
    assert result.checksum_ok is True

    target = dl.expected_path("BTCUSDT", period)
    assert target.exists()

    written = pq.ParquetFile(target).read()
    assert len(written) == 3
    assert set(written.schema.names) == set(SCHEMA_KLINES.names)


# ---------------------------------------------------------------------------
# Test 6: expected_path returns correct path for both intervals
# ---------------------------------------------------------------------------

def test_expected_path_delegates_to_parquet_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("kairos.data.paths.DATA_ROOT", tmp_path)

    period = date(2024, 3, 15)
    dl_1m = KlinesDownloader("1m")
    dl_15m = KlinesDownloader("15m")

    p1 = dl_1m.expected_path("BTCUSDT", period)
    p15 = dl_15m.expected_path("BTCUSDT", period)

    assert "interval=1m" in p1.as_posix()
    assert "BTCUSDT-1m-2024-03-15.parquet" in p1.name
    assert "interval=15m" in p15.as_posix()
    assert "BTCUSDT-15m-2024-03-15.parquet" in p15.name


# ---------------------------------------------------------------------------
# Test 7: name attribute
# ---------------------------------------------------------------------------

def test_name_attribute_matches_interval() -> None:
    assert KlinesDownloader("1m").name == "klines_1m"
    assert KlinesDownloader("15m").name == "klines_15m"
