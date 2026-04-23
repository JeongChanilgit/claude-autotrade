"""Binance Vision fundingRate (monthly) downloader for the funding dataset."""
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
    SCHEMA_FUNDING,
)

log = logging.getLogger("kairos.data.download_funding")

_CSV_DTYPES: dict[str, str] = {
    "calc_time": "int64",
    "funding_interval_hours": "int32",
    "last_funding_rate": "float64",
}


def _csv_bytes_to_table(csv_bytes: bytes, symbol: str) -> pa.Table:
    """Parse raw funding CSV bytes and return a PyArrow Table matching SCHEMA_FUNDING."""
    df = pd.read_csv(
        io.BytesIO(csv_bytes),
        header=0,
        dtype=_CSV_DTYPES,
    )
    df = df.rename(columns={
        "calc_time": "funding_time",
        "last_funding_rate": "funding_rate",
    })
    df["symbol"] = symbol

    ordered = [f.name for f in SCHEMA_FUNDING]
    df = df[ordered]

    return pa.Table.from_pandas(df, schema=SCHEMA_FUNDING, preserve_index=False)


class FundingDownloader:
    """Downloads Binance Vision monthly fundingRate and writes Snappy-compressed Parquet."""

    name = "funding"

    def expected_path(self, symbol: str, period: date) -> Path:
        return paths.parquet_path("funding", symbol, period.replace(day=1))

    def fetch(self, symbol: str, period: date) -> FetchResult:
        period_day1 = period.replace(day=1)
        target = paths.parquet_path("funding", symbol, period_day1)
        zip_url = paths.vision_zip_url("funding", symbol, period_day1)
        cksum_url = paths.checksum_url(zip_url)

        if target.exists():
            try:
                existing = pq.ParquetFile(str(target)).read()
                if len(existing) >= 1:
                    log.debug(
                        "funding %s %s: skip (parquet exists, %d rows)",
                        symbol,
                        period_day1,
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
                log.warning("404 funding %s %s: %s", symbol, period_day1, zip_url)
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

        if not (60 <= rows <= 200):
            log.warning(
                "funding row count %d out of expected [60,200] for %s %s",
                rows,
                symbol,
                period_day1,
            )

        ensure_parent_dir(target)
        pq.write_table(
            table,
            target,
            compression="snappy",
            use_dictionary=["symbol"],
        )

        log.info(
            "funding %s %s: %d rows → %s",
            symbol,
            period_day1,
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
