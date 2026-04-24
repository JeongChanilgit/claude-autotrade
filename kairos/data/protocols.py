"""Data layer interface contracts. **FROZEN — do not modify without Opus review.**

All Sonnet-delegated downloader implementations must conform to these protocols.
Schemas here define Parquet column types and partitioning; Sonnet must not rename
or drop columns.

Binance Vision source URLs (verified 2026-04-23):
  klines daily:    https://data.binance.vision/data/futures/um/daily/klines/{SYMBOL}/{INTERVAL}/{SYMBOL}-{INTERVAL}-{YYYY-MM-DD}.zip
  klines monthly:  https://data.binance.vision/data/futures/um/monthly/klines/{SYMBOL}/{INTERVAL}/{SYMBOL}-{INTERVAL}-{YYYY-MM}.zip
  funding monthly: https://data.binance.vision/data/futures/um/monthly/fundingRate/{SYMBOL}/{SYMBOL}-fundingRate-{YYYY-MM}.zip
  metrics daily:   https://data.binance.vision/data/futures/um/daily/metrics/{SYMBOL}/{SYMBOL}-metrics-{YYYY-MM-DD}.zip

Each archive has a sibling `<url>.CHECKSUM` containing "<sha256>  <filename>" ASCII.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal, Protocol

import pyarrow as pa

# ---- Shared types ---------------------------------------------------------

Dataset = Literal["klines_1m", "klines_15m", "funding", "metrics"]
"""Datasets downloaded in Phase 1. `liquidations` deferred to Phase 3."""


@dataclass(frozen=True, slots=True)
class FetchResult:
    """Return value of every downloader fetch.

    skipped=True means the target parquet already existed and was left untouched.
    rows is the number of records written (or pre-existing if skipped).
    """
    path: Path
    rows: int
    checksum_ok: bool
    skipped: bool
    source_url: str


class Downloader(Protocol):
    """Protocol every per-dataset downloader must satisfy.

    Implementations:
      - Must be idempotent: calling fetch() twice with same args returns skipped=True
        on the second call (no network hit if local parquet is present and non-empty).
      - Must verify SHA256 against the `.CHECKSUM` sidecar. Mismatch raises
        ChecksumError; the bad partial file must be deleted before raising.
      - Must use tenacity retry (3 attempts, exp backoff 1s/2s/4s) on transient
        HTTP errors (5xx, timeouts). 404 is NOT retried — return skipped=True with
        rows=0, source_url set, checksum_ok=False so caller can gap-log.
      - Must write parquet with Snappy compression, arrow dict-encoded for string cols.
      - Must partition per PARTITION_KEYS below (Hive-style directories).
      - Must NOT touch any dataset other than its own.
    """

    name: Dataset

    def fetch(self, symbol: str, period: date) -> FetchResult:
        """Download one file (daily period=date, monthly period=first of month).

        For monthly-only datasets (funding), period.day is ignored and
        period.replace(day=1) is used.
        """
        ...

    def expected_path(self, symbol: str, period: date) -> Path:
        """Where the output parquet will be written. Used for idempotency check."""
        ...


# ---- Partitioning --------------------------------------------------------

PARTITION_KEYS: dict[Dataset, tuple[str, ...]] = {
    "klines_1m": ("symbol", "interval", "year", "month"),
    "klines_15m": ("symbol", "interval", "year", "month"),
    "funding": ("symbol", "year", "month"),
    "metrics": ("symbol", "year", "month"),
}
"""Hive-partition keys (in order) for each dataset.

Parquet file path shape (rooted at data/):
  klines/symbol=BTCUSDT/interval=1m/year=2024/month=03/BTCUSDT-1m-2024-03-15.parquet
  funding/symbol=BTCUSDT/year=2024/month=03/BTCUSDT-funding-2024-03.parquet
  metrics/symbol=BTCUSDT/year=2024/month=03/BTCUSDT-metrics-2024-03-15.parquet
"""


# ---- Parquet schemas (FROZEN) --------------------------------------------

# All timestamps are int64 UTC milliseconds (matches Binance convention).
# Price/volume columns are float64; trade counts int64.

SCHEMA_KLINES: pa.Schema = pa.schema([
    pa.field("open_time", pa.int64(), nullable=False),           # UTC ms, bar open
    pa.field("close_time", pa.int64(), nullable=False),          # UTC ms, bar close (inclusive-1ms)
    pa.field("open", pa.float64(), nullable=False),
    pa.field("high", pa.float64(), nullable=False),
    pa.field("low", pa.float64(), nullable=False),
    pa.field("close", pa.float64(), nullable=False),
    pa.field("volume", pa.float64(), nullable=False),            # base asset volume
    pa.field("quote_volume", pa.float64(), nullable=False),
    pa.field("trade_count", pa.int64(), nullable=False),
    pa.field("taker_buy_volume", pa.float64(), nullable=False),
    pa.field("taker_buy_quote_volume", pa.float64(), nullable=False),
    pa.field("symbol", pa.string(), nullable=False),
    pa.field("interval", pa.string(), nullable=False),           # "1m" | "15m"
])
"""Source CSV cols (drop `ignore` col 11):
open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,taker_buy_quote_volume,ignore
"""

SCHEMA_FUNDING: pa.Schema = pa.schema([
    pa.field("funding_time", pa.int64(), nullable=False),            # UTC ms = source calc_time
    pa.field("funding_interval_hours", pa.int32(), nullable=False),  # usually 8
    pa.field("funding_rate", pa.float64(), nullable=False),          # source last_funding_rate
    pa.field("symbol", pa.string(), nullable=False),
])
"""Source CSV cols: calc_time,funding_interval_hours,last_funding_rate"""

SCHEMA_METRICS: pa.Schema = pa.schema([
    pa.field("create_time", pa.int64(), nullable=False),             # UTC ms (parsed from "YYYY-MM-DD HH:MM:SS")
    pa.field("symbol", pa.string(), nullable=False),
    # Binance occasionally emits NaN rows for ratio columns (source gaps).
    # Keep them nullable so the downloader can persist the raw bar rather
    # than drop the whole day. Strategies that use these columns must
    # .ffill() or drop-na themselves.
    pa.field("sum_open_interest", pa.float64(), nullable=True),
    pa.field("sum_open_interest_value", pa.float64(), nullable=True),
    pa.field("count_toptrader_long_short_ratio", pa.float64(), nullable=True),
    pa.field("sum_toptrader_long_short_ratio", pa.float64(), nullable=True),
    pa.field("count_long_short_ratio", pa.float64(), nullable=True),
    pa.field("sum_taker_long_short_vol_ratio", pa.float64(), nullable=True),
])
"""Source CSV cols: create_time,symbol,sum_open_interest,sum_open_interest_value,
count_toptrader_long_short_ratio,sum_toptrader_long_short_ratio,count_long_short_ratio,
sum_taker_long_short_vol_ratio
NOTE: source `create_time` is a string "YYYY-MM-DD HH:MM:SS" — must parse to UTC ms.
Sampling granularity is 5 minutes.
"""

SCHEMAS: dict[Dataset, pa.Schema] = {
    "klines_1m": SCHEMA_KLINES,
    "klines_15m": SCHEMA_KLINES,
    "funding": SCHEMA_FUNDING,
    "metrics": SCHEMA_METRICS,
}


class ChecksumError(RuntimeError):
    """Raised when downloaded ZIP SHA256 does not match sidecar CHECKSUM."""


class SchemaMismatchError(RuntimeError):
    """Raised when source CSV columns do not match expected schema."""
