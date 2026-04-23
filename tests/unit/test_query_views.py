"""Tests for kairos.data.query DuckDB view layer."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from kairos.data import paths
from kairos.data.protocols import SCHEMA_KLINES
from kairos.data.query import connect, create_views, views_present


def test_create_views_with_no_data_creates_empty_views(tmp_path: Path) -> None:
    con = connect()
    create_views(con, tmp_path)

    for view in ("v_klines_1m", "v_klines_15m", "v_funding", "v_metrics"):
        count = con.execute(f"SELECT count(*) FROM {view}").fetchone()
        assert count is not None and count[0] == 0

    cols = {r[0] for r in con.execute("DESCRIBE v_klines_1m").fetchall()}
    expected = {f.name for f in SCHEMA_KLINES}
    assert cols == expected


def test_views_present_returns_all_four(tmp_path: Path) -> None:
    con = connect()
    create_views(con, tmp_path)
    assert views_present(con) == {"v_klines_1m", "v_klines_15m", "v_funding", "v_metrics"}


def test_create_views_with_sample_parquet_reads_rows(tmp_path: Path) -> None:
    target = paths.parquet_path("klines_1m", "BTCUSDT", date(2024, 3, 15))
    target = tmp_path / target.relative_to(paths.DATA_ROOT)
    target.parent.mkdir(parents=True, exist_ok=True)

    rows = {
        "open_time": [1710460800000, 1710460860000],
        "close_time": [1710460859999, 1710460919999],
        "open": [70000.0, 70010.0],
        "high": [70050.0, 70020.0],
        "low": [69990.0, 70005.0],
        "close": [70010.0, 70015.0],
        "volume": [1.5, 2.5],
        "quote_volume": [105000.0, 175000.0],
        "trade_count": [100, 150],
        "taker_buy_volume": [0.8, 1.3],
        "taker_buy_quote_volume": [56000.0, 91000.0],
        "symbol": ["BTCUSDT", "BTCUSDT"],
        "interval": ["1m", "1m"],
    }
    table = pa.Table.from_pydict(rows, schema=SCHEMA_KLINES)
    pq.write_table(table, target, compression="snappy")

    con = connect()
    create_views(con, tmp_path)
    result = con.execute("SELECT count(*) FROM v_klines_1m").fetchone()
    assert result is not None and result[0] == 2

    # 15m view should be empty (no 15m parquet present)
    result_15m = con.execute("SELECT count(*) FROM v_klines_15m").fetchone()
    assert result_15m is not None and result_15m[0] == 0


def test_interval_filter_separates_1m_and_15m(tmp_path: Path) -> None:
    for interval in ("1m", "15m"):
        dataset = "klines_1m" if interval == "1m" else "klines_15m"
        target = paths.parquet_path(dataset, "BTCUSDT", date(2024, 3, 15))
        target = tmp_path / target.relative_to(paths.DATA_ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        rows = {
            "open_time": [1710460800000],
            "close_time": [1710460859999],
            "open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0],
            "volume": [1.0], "quote_volume": [1.0], "trade_count": [1],
            "taker_buy_volume": [0.5], "taker_buy_quote_volume": [0.5],
            "symbol": ["BTCUSDT"], "interval": [interval],
        }
        pq.write_table(pa.Table.from_pydict(rows, schema=SCHEMA_KLINES), target, compression="snappy")

    con = connect()
    create_views(con, tmp_path)
    r1 = con.execute("SELECT count(*) FROM v_klines_1m").fetchone()
    r15 = con.execute("SELECT count(*) FROM v_klines_15m").fetchone()
    assert r1 is not None and r1[0] == 1
    assert r15 is not None and r15[0] == 1
