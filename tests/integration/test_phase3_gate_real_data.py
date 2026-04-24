"""Phase 3 gate integration test — runs the real gate_runner when data is present.

Skipped automatically in CI when BTCUSDT 15m parquet partitions are missing.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from kairos.backtest.gate_runner import run_gate


def _has_bars(symbol: str, interval: str) -> bool:
    root = Path(f"data/klines/symbol={symbol}/interval={interval}")
    if not root.exists():
        return False
    # require at least 30 daily files to run meaningful metrics
    return len(list(root.rglob("*.parquet"))) >= 30


pytestmark = pytest.mark.skipif(
    not _has_bars("BTCUSDT", "15m"),
    reason="requires full BTCUSDT 15m partitions — run `make download-data` first",
)


def test_phase3_gate_returns_structured_report_on_real_data() -> None:
    """End-to-end smoke test on whatever BTC 15m window is locally downloaded.

    We don't assert the gate passes — Phase 3 pass/fail is an ongoing empirical
    question. This test just verifies the driver completes without exception
    and produces the expected report shape.
    """
    start = pd.Timestamp("2023-01-01", tz="UTC")
    end = pd.Timestamp("2023-06-01", tz="UTC")
    out = run_gate(
        symbols=["BTCUSDT"],
        interval="15m",
        start=start,
        end=end,
        initial_cash=Decimal("10000"),
    )
    assert "error" not in out
    assert out["bars"] > 0
    assert len(out["strategy_sharpes"]) == 3
    assert {s["strategy"] for s in out["strategy_sharpes"]} == {
        "orb", "funding_counter", "vol_compression",
    }
    assert all(s["symbol"] == "BTCUSDT" for s in out["strategy_sharpes"])
    assert "cagr" in out["ensemble"]
    assert "mdd" in out["ensemble"]
    assert isinstance(out["regimes"], list)
    assert isinstance(out["stress"], list)
    assert isinstance(out["gate"]["overall_pass"], bool)
    assert isinstance(out["summary"], list) and len(out["summary"]) > 0
