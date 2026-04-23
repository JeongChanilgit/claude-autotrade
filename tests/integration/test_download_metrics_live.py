"""Integration test: live Binance Vision fetch for the metrics dataset."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from kairos.data.download_metrics import MetricsDownloader


@pytest.mark.integration
def test_fetch_btcusdt_metrics_live(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("kairos.data.paths.DATA_ROOT", tmp_path)

    dl = MetricsDownloader()
    period = date(2024, 3, 15)

    result = dl.fetch("BTCUSDT", period)

    assert result.rows > 250, f"Expected >250 rows for a full day at 5-min granularity, got {result.rows}"
    assert result.checksum_ok is True
    assert result.skipped is False
    assert result.path.exists()

    result2 = dl.fetch("BTCUSDT", period)

    assert result2.skipped is True
    assert result2.rows == result.rows
