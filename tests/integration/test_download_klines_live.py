"""Integration tests for KlinesDownloader — require live Binance Vision access."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from kairos.data.download_klines import KlinesDownloader


@pytest.mark.integration
def test_fetch_btcusdt_1m_live(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("kairos.data.paths.DATA_ROOT", tmp_path)

    dl = KlinesDownloader("1m")
    period = date(2024, 3, 15)

    result = dl.fetch("BTCUSDT", period)

    assert result.rows > 1400, f"Expected >1400 rows for full day, got {result.rows}"
    assert result.checksum_ok is True
    assert result.skipped is False

    target = dl.expected_path("BTCUSDT", period)
    assert target.exists()

    written = pq.ParquetFile(target).read()
    assert len(written) == result.rows

    result2 = dl.fetch("BTCUSDT", period)
    assert result2.skipped is True
    assert result2.rows > 1400
