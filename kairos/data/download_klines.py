"""Binance Vision klines (OHLCV) downloader for klines_1m and klines_15m datasets."""
from __future__ import annotations

import io
import logging
from datetime import date
from pathlib import Path
from typing import Literal

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
    Dataset,
    FetchResult,
    SCHEMA_KLINES,
)

log = logging.getLogger("kairos.data.download_klines")

_CSV_NAMES = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "count",
    "taker_buy_volume",
    "taker_buy_quote_volume",
    "ignore",
]

_CSV_DTYPES: dict[str, str] = {
    "open_time": "int64",
    "open": "float64",
    "high": "float64",
    "low": "float64",
    "close": "float64",
    "volume": "float64",
    "close_time": "int64",
    "quote_volume": "float64",
    "count": "int64",
    "taker_buy_volume": "float64",
    "taker_buy_quote_volume": "float64",
    "ignore": "object",
}


def _csv_bytes_to_table(csv_bytes: bytes, symbol: str, interval: str) -> pa.Table:
    """Parse raw klines CSV bytes and return a PyArrow Table matching SCHEMA_KLINES.

    Detects whether the CSV has a header row by checking if the first byte is a digit.
    Older Binance Vision files (pre-~2021) sometimes omit the header.
    """
    first_byte = csv_bytes[:1]
    has_header = not (first_byte and first_byte[0:1].isdigit())

    if has_header:
        df = pd.read_csv(
            io.BytesIO(csv_bytes),
            header=0,
            dtype=_CSV_DTYPES,
        )
    else:
        df = pd.read_csv(
            io.BytesIO(csv_bytes),
            header=None,
            names=_CSV_NAMES,
            dtype=_CSV_DTYPES,
        )

    df = df.drop(columns=["ignore"])
    df = df.rename(columns={"count": "trade_count"})
    df["symbol"] = symbol
    df["interval"] = interval

    ordered = [f.name for f in SCHEMA_KLINES]
    df = df[ordered]

    table = pa.Table.from_pandas(df, schema=SCHEMA_KLINES, preserve_index=False)
    return table


class KlinesDownloader:
    """Downloads Binance Vision daily klines and writes Snappy-compressed Parquet."""

    def __init__(self, interval: Literal["1m", "15m"]) -> None:
        self._interval = interval
        self._dataset: Dataset = "klines_1m" if interval == "1m" else "klines_15m"

    @property
    def name(self) -> Dataset:
        return self._dataset

    def expected_path(self, symbol: str, period: date) -> Path:
        return paths.parquet_path(self._dataset, symbol, period)

    def fetch(self, symbol: str, period: date) -> FetchResult:
        target = self.expected_path(symbol, period)
        zip_url = paths.vision_zip_url(self._dataset, symbol, period)
        cksum_url = paths.checksum_url(zip_url)

        if target.exists():
            try:
                existing = pq.ParquetFile(target).read()
                if len(existing) >= 1:
                    log.debug(
                        "klines %s %s %s: skip (parquet exists, %d rows)",
                        self._interval,
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
                log.warning("404 %s", zip_url)
                return FetchResult(
                    path=target,
                    rows=0,
                    checksum_ok=False,
                    skipped=True,
                    source_url=zip_url,
                )

        inner_filename, csv_bytes = open_single_csv_from_zip(zip_bytes)
        zip_filename = zip_url.rsplit("/", 1)[-1]

        try:
            verify_sha256(zip_bytes, cksum_bytes.decode("ascii"), zip_filename)
        except ChecksumError:
            if target.exists():
                target.unlink()
            raise

        table = _csv_bytes_to_table(csv_bytes, symbol, self._interval)
        rows = len(table)

        ensure_parent_dir(target)
        pq.write_table(
            table,
            target,
            compression="snappy",
            use_dictionary=["symbol", "interval"],
        )

        log.info(
            "klines %s %s %s: %d rows → %s",
            self._interval,
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
