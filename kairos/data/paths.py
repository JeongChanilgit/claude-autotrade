"""Path resolution for downloaded parquet files. One source of truth for all downloaders."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from kairos.data.protocols import Dataset

DATA_ROOT = Path(__file__).resolve().parents[2] / "data"


def parquet_path(dataset: Dataset, symbol: str, period: date) -> Path:
    """Return the parquet destination for a (dataset, symbol, period).

    Period semantics:
      - klines_1m / klines_15m / metrics: daily file (period is the specific day)
      - funding: monthly file (period.day is ignored, normalized to day=1)
    """
    year = period.year
    month = f"{period.month:02d}"

    if dataset in ("klines_1m", "klines_15m"):
        interval = "1m" if dataset == "klines_1m" else "15m"
        fname = f"{symbol}-{interval}-{period:%Y-%m-%d}.parquet"
        return (
            DATA_ROOT / "klines"
            / f"symbol={symbol}" / f"interval={interval}"
            / f"year={year}" / f"month={month}" / fname
        )
    if dataset == "funding":
        fname = f"{symbol}-funding-{year}-{month}.parquet"
        return (
            DATA_ROOT / "funding"
            / f"symbol={symbol}" / f"year={year}" / f"month={month}" / fname
        )
    if dataset == "metrics":
        fname = f"{symbol}-metrics-{period:%Y-%m-%d}.parquet"
        return (
            DATA_ROOT / "metrics"
            / f"symbol={symbol}" / f"year={year}" / f"month={month}" / fname
        )
    raise ValueError(f"unknown dataset: {dataset}")


def vision_zip_url(dataset: Dataset, symbol: str, period: date) -> str:
    """Canonical Binance Vision ZIP URL for a (dataset, symbol, period)."""
    base = "https://data.binance.vision/data/futures/um"
    if dataset == "klines_1m":
        return f"{base}/daily/klines/{symbol}/1m/{symbol}-1m-{period:%Y-%m-%d}.zip"
    if dataset == "klines_15m":
        return f"{base}/daily/klines/{symbol}/15m/{symbol}-15m-{period:%Y-%m-%d}.zip"
    if dataset == "funding":
        return f"{base}/monthly/fundingRate/{symbol}/{symbol}-fundingRate-{period:%Y-%m}.zip"
    if dataset == "metrics":
        return f"{base}/daily/metrics/{symbol}/{symbol}-metrics-{period:%Y-%m-%d}.zip"
    raise ValueError(f"unknown dataset: {dataset}")


def checksum_url(zip_url: str) -> str:
    return zip_url + ".CHECKSUM"
