"""Binance Vision metrics (OI + long/short ratios) downloader for the metrics dataset."""
from __future__ import annotations

import io
import logging
from datetime import date
from pathlib import Path

import httpx
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from kairos.data import http as http_mod
from kairos.data import paths
from kairos.data.http import (
    NotFoundError,
    ensure_parent_dir,
    http_get_bytes,
    open_single_csv_from_zip,
    verify_sha256,
)
from kairos.data.protocols import (
    ChecksumError,
    FetchResult,
    SCHEMA_METRICS,
    SchemaMismatchError,
)

log = logging.getLogger("kairos.data.download_metrics")

_CSV_DTYPES: dict[str, str] = {
    "create_time": "str",
    "symbol": "str",
    "sum_open_interest": "float64",
    "sum_open_interest_value": "float64",
    "count_toptrader_long_short_ratio": "float64",
    "sum_toptrader_long_short_ratio": "float64",
    "count_long_short_ratio": "float64",
    "sum_taker_long_short_vol_ratio": "float64",
}


def _csv_bytes_to_table(csv_bytes: bytes, symbol: str) -> pa.Table:
    """Parse raw metrics CSV bytes and return a PyArrow Table matching SCHEMA_METRICS.

    Raises SchemaMismatchError if the embedded symbol column does not match `symbol`.
    """
    df = pd.read_csv(
        io.BytesIO(csv_bytes),
        header=0,
        dtype=_CSV_DTYPES,
    )

    if not (df["symbol"] == symbol).all():
        mismatched = df.loc[df["symbol"] != symbol, "symbol"].unique().tolist()
        raise SchemaMismatchError(
            f"symbol column mismatch: expected {symbol!r}, found {mismatched!r}"
        )

    dt_utc = pd.to_datetime(df["create_time"], format="%Y-%m-%d %H:%M:%S", utc=True)
    df["create_time"] = dt_utc.values.astype("datetime64[ms]").astype("int64")

    ordered = [f.name for f in SCHEMA_METRICS]
    df = df[ordered]

    return pa.Table.from_pandas(df, schema=SCHEMA_METRICS, preserve_index=False)


class MetricsDownloader:
    """Downloads Binance Vision daily metrics and writes Snappy-compressed Parquet."""

    name = "metrics"

    def expected_path(self, symbol: str, period: date) -> Path:
        return paths.parquet_path("metrics", symbol, period)

    def fetch(self, symbol: str, period: date) -> FetchResult:
        target = self.expected_path(symbol, period)
        zip_url = paths.vision_zip_url("metrics", symbol, period)
        cksum_url = paths.checksum_url(zip_url)

        if target.exists():
            try:
                existing = pq.ParquetFile(target).read()
                if len(existing) >= 1:
                    log.debug(
                        "metrics %s %s: skip (parquet exists, %d rows)",
                        symbol,
                        period,
                        len(existing),
                    )
                    return FetchResult(
                        path=target,
                        rows=len(existing),
                        checksum_ok=True,
                        skipped=True,
                        source_url=zip_url,
                    )
            except Exception:
                pass

        with httpx.Client(
            timeout=http_mod.DEFAULT_TIMEOUT,
            headers={"User-Agent": http_mod.USER_AGENT},
        ) as client:
            try:
                zip_bytes = http_get_bytes(zip_url, client=client)
                cksum_bytes = http_get_bytes(cksum_url, client=client)
            except NotFoundError:
                log.warning("404 metrics %s %s: %s", symbol, period, zip_url)
                return FetchResult(
                    path=target,
                    rows=0,
                    checksum_ok=False,
                    skipped=True,
                    source_url=zip_url,
                )

        zip_filename = zip_url.rsplit("/", 1)[-1]

        try:
            verify_sha256(zip_bytes, cksum_bytes.decode("ascii"), zip_filename)
        except ChecksumError:
            if target.exists():
                target.unlink()
            raise

        _inner_filename, csv_bytes = open_single_csv_from_zip(zip_bytes)
        table = _csv_bytes_to_table(csv_bytes, symbol)
        rows = len(table)

        ensure_parent_dir(target)
        pq.write_table(
            table,
            target,
            compression="snappy",
            use_dictionary=["symbol"],
        )

        log.info(
            "metrics %s %s: %d rows → %s",
            symbol,
            period,
            rows,
            target,
        )

        return FetchResult(
            path=target,
            rows=rows,
            checksum_ok=True,
            skipped=False,
            source_url=zip_url,
        )
