"""Unit tests for kairos.data.download_funding."""
from __future__ import annotations

import hashlib
import io
import zipfile
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pyarrow.parquet as pq
import pytest

from kairos.data.download_funding import FundingDownloader, _csv_bytes_to_table
from kairos.data.protocols import ChecksumError, SCHEMA_FUNDING


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAMPLE_CSV = (
    b"calc_time,funding_interval_hours,last_funding_rate\n"
    b"1709251200000,8,0.00053322\n"
    b"1709280000000,8,0.00064824\n"
    b"1709308800000,8,0.00071500\n"
)


def _make_zip(inner_name: str, content: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(inner_name, content)
    return buf.getvalue()


def _checksum_text(data: bytes, filename: str) -> bytes:
    h = hashlib.sha256(data).hexdigest()
    return f"{h}  {filename}\n".encode("ascii")


# ---------------------------------------------------------------------------
# test_csv_to_table_transforms_columns
# ---------------------------------------------------------------------------

def test_csv_to_table_transforms_columns() -> None:
    table = _csv_bytes_to_table(_SAMPLE_CSV, "BTCUSDT")

    assert table.schema.equals(SCHEMA_FUNDING)
    assert table.num_rows == 3

    funding_times = table.column("funding_time").to_pylist()
    assert funding_times[0] == 1709251200000
    assert funding_times[1] == 1709280000000
    assert funding_times[2] == 1709308800000

    intervals = table.column("funding_interval_hours").to_pylist()
    assert all(v == 8 for v in intervals)

    rates = table.column("funding_rate").to_pylist()
    assert abs(rates[0] - 0.00053322) < 1e-10
    assert abs(rates[1] - 0.00064824) < 1e-10

    symbols = table.column("symbol").to_pylist()
    assert all(s == "BTCUSDT" for s in symbols)

    assert table.schema.field("funding_time").type.equals(SCHEMA_FUNDING.field("funding_time").type)
    assert table.schema.field("funding_interval_hours").type.equals(
        SCHEMA_FUNDING.field("funding_interval_hours").type
    )
    assert table.schema.field("funding_rate").type.equals(SCHEMA_FUNDING.field("funding_rate").type)


# ---------------------------------------------------------------------------
# test_fetch_normalizes_period_to_day_one
# ---------------------------------------------------------------------------

def test_fetch_normalizes_period_to_day_one(tmp_path: Path) -> None:
    inner_csv = _SAMPLE_CSV
    zip_name = "BTCUSDT-fundingRate-2024-03.zip"
    zip_bytes = _make_zip("BTCUSDT-fundingRate-2024-03.csv", inner_csv)
    cksum = _checksum_text(zip_bytes, zip_name)

    call_count = 0

    def fake_http_get_bytes(url: str, client: object = None) -> bytes:
        nonlocal call_count
        call_count += 1
        if url.endswith(".CHECKSUM"):
            return cksum
        return zip_bytes

    with (
        patch("kairos.data.download_funding.http_get_bytes", side_effect=fake_http_get_bytes),
        patch("kairos.data.paths.DATA_ROOT", tmp_path),
    ):
        downloader = FundingDownloader()

        r1 = downloader.fetch("BTCUSDT", date(2024, 3, 15))
        assert r1.skipped is False
        assert r1.rows == 3

        r2 = downloader.fetch("BTCUSDT", date(2024, 3, 1))
        assert r2.skipped is True
        assert r2.rows == 3

    assert call_count == 2


# ---------------------------------------------------------------------------
# test_fetch_404_returns_skipped_with_zero_rows
# ---------------------------------------------------------------------------

def test_fetch_404_returns_skipped_with_zero_rows(tmp_path: Path) -> None:
    from kairos.data.http import NotFoundError

    with (
        patch(
            "kairos.data.download_funding.http_get_bytes",
            side_effect=NotFoundError("http://example.com/test.zip"),
        ),
        patch("kairos.data.paths.DATA_ROOT", tmp_path),
    ):
        downloader = FundingDownloader()
        result = downloader.fetch("XYZUSDT", date(2024, 3, 1))

    assert result.rows == 0
    assert result.checksum_ok is False
    assert result.skipped is True
    assert "XYZUSDT" in result.source_url


# ---------------------------------------------------------------------------
# test_fetch_checksum_mismatch_raises_and_cleans_up
# ---------------------------------------------------------------------------

def test_fetch_checksum_mismatch_raises_and_cleans_up(tmp_path: Path) -> None:
    zip_bytes = _make_zip("BTCUSDT-fundingRate-2024-03.csv", _SAMPLE_CSV)
    bad_cksum = b"0" * 64 + b"  BTCUSDT-fundingRate-2024-03.zip\n"

    def fake_http_get_bytes(url: str, client: object = None) -> bytes:
        if url.endswith(".CHECKSUM"):
            return bad_cksum
        return zip_bytes

    with (
        patch("kairos.data.download_funding.http_get_bytes", side_effect=fake_http_get_bytes),
        patch("kairos.data.paths.DATA_ROOT", tmp_path),
    ):
        downloader = FundingDownloader()
        expected = downloader.expected_path("BTCUSDT", date(2024, 3, 1))

        with pytest.raises(ChecksumError):
            downloader.fetch("BTCUSDT", date(2024, 3, 1))

    assert not expected.exists()


# ---------------------------------------------------------------------------
# test_fetch_happy_path_writes_parquet
# ---------------------------------------------------------------------------

def test_fetch_happy_path_writes_parquet(tmp_path: Path) -> None:
    csv_content = (
        b"calc_time,funding_interval_hours,last_funding_rate\n"
        b"1709251200000,8,0.00053322\n"
        b"1709280000000,8,0.00064824\n"
        b"1709308800000,8,0.00071500\n"
        b"1709337600000,8,0.00060000\n"
        b"1709366400000,8,0.00055000\n"
    )
    zip_name = "BTCUSDT-fundingRate-2024-03.zip"
    zip_bytes = _make_zip("BTCUSDT-fundingRate-2024-03.csv", csv_content)
    cksum = _checksum_text(zip_bytes, zip_name)

    def fake_http_get_bytes(url: str, client: object = None) -> bytes:
        if url.endswith(".CHECKSUM"):
            return cksum
        return zip_bytes

    with (
        patch("kairos.data.download_funding.http_get_bytes", side_effect=fake_http_get_bytes),
        patch("kairos.data.paths.DATA_ROOT", tmp_path),
    ):
        downloader = FundingDownloader()
        result = downloader.fetch("BTCUSDT", date(2024, 3, 1))

    assert result.skipped is False
    assert result.checksum_ok is True
    assert result.rows == 5
    assert result.path.exists()

    written = pq.ParquetFile(str(result.path)).read()
    assert written.schema.equals(SCHEMA_FUNDING)
    assert written.num_rows == 5
