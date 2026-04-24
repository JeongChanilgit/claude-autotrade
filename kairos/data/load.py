"""BarsData loader — reads parquet partitions via DuckDB and produces
the `BarsData` container that all Strategies / Engines expect.

Alignment policy:
- ohlcv keeps its native cadence (1m or 15m) as the master time axis.
- funding is returned at its native 8h cadence (strategies call
  `.reindex(ohlcv.index).ffill()` themselves, per BarsData docstring).
- metrics is returned at its native 5m cadence; same alignment rule applies.

This module does NOT mutate BarsData shape — it only builds it. Any strategy
that needs funding / metrics but receives None raises ValueError (its own
contract).
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

from kairos.backtest.protocols import BarsData, Interval
from kairos.data import paths, query


def _to_utc_index(df: pd.DataFrame, ts_col: str) -> pd.DataFrame:
    """Convert int64 ms column to DatetimeIndex (UTC) and sort."""
    idx = pd.to_datetime(df[ts_col], unit="ms", utc=True)
    df = df.drop(columns=[ts_col]).set_index(idx).sort_index()
    # keep="last": re-pulled rows appear later in the partition and carry corrected values (see d521c69)
    df = df[~df.index.duplicated(keep="last")]
    df.index.name = None
    return df


def load_ohlcv(
    symbol: str,
    interval: Interval,
    start: pd.Timestamp,
    end: pd.Timestamp,
    *,
    con: duckdb.DuckDBPyConnection | None = None,
    data_root: Path | None = None,
) -> pd.DataFrame:
    """Return ohlcv frame indexed by open_time (UTC), columns per BarsData spec.

    `start` inclusive, `end` exclusive (half-open). Timestamps must be UTC-aware.
    """
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("start/end must be UTC-aware")
    owns = con is None
    if owns:
        con = query.connect()
        query.create_views(con, data_root=data_root if data_root else paths.DATA_ROOT)

    try:
        view = f"v_klines_{interval}"
        start_ms = int(start.value // 1_000_000)
        end_ms = int(end.value // 1_000_000)
        sql = f"""
            SELECT open_time, open, high, low, close, volume,
                   quote_volume, taker_buy_volume, taker_buy_quote_volume,
                   trade_count
            FROM {view}
            WHERE symbol = ?
              AND open_time >= ?
              AND open_time < ?
            ORDER BY open_time
        """
        df = con.execute(sql, [symbol, start_ms, end_ms]).df()
    finally:
        if owns:
            con.close()

    if df.empty:
        return df

    df = _to_utc_index(df, "open_time")
    df["trade_count"] = df["trade_count"].astype("int64")
    for c in ("open", "high", "low", "close", "volume",
             "quote_volume", "taker_buy_volume", "taker_buy_quote_volume"):
        df[c] = df[c].astype("float64")
    return df


def load_funding(
    symbol: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    *,
    con: duckdb.DuckDBPyConnection | None = None,
    data_root: Path | None = None,
) -> pd.DataFrame:
    owns = con is None
    if owns:
        con = query.connect()
        query.create_views(con, data_root=data_root if data_root else paths.DATA_ROOT)
    try:
        start_ms = int(start.value // 1_000_000)
        end_ms = int(end.value // 1_000_000)
        df = con.execute(
            """
            SELECT funding_time, funding_rate, funding_interval_hours
            FROM v_funding
            WHERE symbol = ?
              AND funding_time >= ?
              AND funding_time < ?
            ORDER BY funding_time
            """,
            [symbol, start_ms, end_ms],
        ).df()
    finally:
        if owns:
            con.close()

    if df.empty:
        return df
    df = _to_utc_index(df, "funding_time")
    df["funding_rate"] = df["funding_rate"].astype("float64")
    df["funding_interval_hours"] = df["funding_interval_hours"].astype("float64")
    return df


def load_metrics(
    symbol: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    *,
    con: duckdb.DuckDBPyConnection | None = None,
    data_root: Path | None = None,
) -> pd.DataFrame:
    owns = con is None
    if owns:
        con = query.connect()
        query.create_views(con, data_root=data_root if data_root else paths.DATA_ROOT)
    try:
        start_ms = int(start.value // 1_000_000)
        end_ms = int(end.value // 1_000_000)
        df = con.execute(
            """
            SELECT create_time,
                   sum_open_interest,
                   sum_open_interest_value,
                   count_toptrader_long_short_ratio,
                   sum_toptrader_long_short_ratio,
                   count_long_short_ratio,
                   sum_taker_long_short_vol_ratio
            FROM v_metrics
            WHERE symbol = ?
              AND create_time >= ?
              AND create_time < ?
            ORDER BY create_time
            """,
            [symbol, start_ms, end_ms],
        ).df()
    finally:
        if owns:
            con.close()

    if df.empty:
        return df
    df = _to_utc_index(df, "create_time")
    for c in df.columns:
        df[c] = df[c].astype("float64")
    return df


def load_bars(
    symbol: str,
    interval: Interval,
    start: pd.Timestamp,
    end: pd.Timestamp,
    *,
    include_funding: bool = True,
    include_metrics: bool = True,
    data_root: Path | None = None,
) -> BarsData:
    """Single-call BarsData builder. Returns empty-frame BarsData if ohlcv is empty.

    Raises no errors on missing auxiliary data — strategies that require them
    will raise their own ValueError at generate_signals time (per their specs).
    """
    con = query.connect()
    try:
        query.create_views(con, data_root=data_root if data_root else paths.DATA_ROOT)
        ohlcv = load_ohlcv(symbol, interval, start, end, con=con)
        funding = load_funding(symbol, start, end, con=con) if include_funding else None
        metrics = load_metrics(symbol, start, end, con=con) if include_metrics else None
    finally:
        con.close()

    if funding is not None and funding.empty:
        funding = None
    if metrics is not None and metrics.empty:
        metrics = None

    return BarsData(
        ohlcv=ohlcv,
        symbol=symbol,
        interval=interval,
        funding=funding,
        metrics=metrics,
    )


__all__ = ["load_bars", "load_ohlcv", "load_funding", "load_metrics"]
