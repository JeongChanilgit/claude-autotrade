"""Integration test: live download of BTCUSDT funding data from Binance Vision."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from kairos.data.download_funding import FundingDownloader


@pytest.mark.integration
def test_fetch_btcusdt_march_2024(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("kairos.data.paths.DATA_ROOT", tmp_path)

    downloader = FundingDownloader()
    result = downloader.fetch("BTCUSDT", date(2024, 3, 1))

    assert result.rows >= 60, f"Expected >=60 rows for March 2024, got {result.rows}"
    assert result.checksum_ok is True
    assert result.skipped is False
    assert result.path.exists()

    result2 = downloader.fetch("BTCUSDT", date(2024, 3, 1))
    assert result2.skipped is True
    assert result2.rows >= 60
