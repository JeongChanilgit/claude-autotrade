"""Out-of-sample gate: physical separation of in-sample vs OOS windows.

Strategies developed using this gate CANNOT read future data (OOS window)
because the loader refuses to return data that overlaps the sealed region.

Overlap definition (half-open intervals):
    [start, end) overlaps [oos_start, oos_end)  iff  start < oos_end AND end > oos_start
    Touching at a boundary is NOT overlap (e.g. end == oos_start → no overlap).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

import pandas as pd
import pyarrow.parquet as pq

from kairos.backtest.protocols import BarsData
from kairos.data import paths

log = logging.getLogger("kairos.data.oos_gate")


class OosViolationError(RuntimeError):
    """Raised when a load request overlaps the sealed OOS window."""


@dataclass(frozen=True)
class OosGate:
    """Sealed boundary between in-sample and out-of-sample.

    oos_start (inclusive) through oos_end (exclusive) is the reserved OOS window.
    Any attempt to load_bars() with a time range that overlaps OOS while
    allow_oos=False raises OosViolationError.

    Both timestamps must be UTC-aware pd.Timestamps.
    """

    oos_start: pd.Timestamp  # inclusive, must be UTC-aware
    oos_end: pd.Timestamp    # exclusive, must be UTC-aware

    def __post_init__(self) -> None:
        if self.oos_start.tzinfo is None or self.oos_end.tzinfo is None:
            raise ValueError("oos_start and oos_end must be UTC-aware Timestamps")
        if self.oos_start >= self.oos_end:
            raise ValueError("oos_start must be strictly before oos_end")

    def check(
        self,
        start: pd.Timestamp,
        end: pd.Timestamp,
        allow_oos: bool,
    ) -> None:
        """Raise OosViolationError if [start, end) overlaps [oos_start, oos_end)
        and allow_oos is False.

        Overlap is defined as: start < oos_end AND end > oos_start
        (half-open interval semantics; touching boundaries are NOT overlap).
        """
        overlaps = start < self.oos_end and end > self.oos_start
        if overlaps and not allow_oos:
            raise OosViolationError(
                f"Request [{start}, {end}) overlaps OOS window "
                f"[{self.oos_start}, {self.oos_end}). "
                "Pass allow_oos=True only for final OOS validation."
            )
        if overlaps and allow_oos:
            log.warning(
                "OOS window accessed with allow_oos=True: [%s, %s)", start, end
            )


def load_bars(
    symbol: str,
    interval: Literal["1m", "15m"],
    start: pd.Timestamp,
    end: pd.Timestamp,
    gate: OosGate,
    allow_oos: bool = False,
    data_root: Path | None = None,
) -> BarsData:
    """Load klines from Parquet, gated by `gate`.

    Raises OosViolationError if [start, end) overlaps [gate.oos_start, gate.oos_end)
    and allow_oos=False. The gate check happens BEFORE any file I/O.

    Returns BarsData with ohlcv populated (funding and metrics left None).
    ohlcv DatetimeIndex is UTC-aware, columns: open, high, low, close, volume,
    quote_volume, taker_buy_volume, taker_buy_quote_volume, trade_count.
    """
    # Gate check FIRST — before any filesystem access.
    gate.check(start, end, allow_oos)

    root = data_root if data_root is not None else paths.DATA_ROOT
    dataset: Literal["klines_1m", "klines_15m"] = (
        "klines_1m" if interval == "1m" else "klines_15m"
    )

    # Collect all daily parquet files whose date falls within [start.date, end.date).
    # We over-read by date and then filter by timestamp.
    frames: list[pd.DataFrame] = []
    current = date(start.year, start.month, start.day)
    end_date = date(end.year, end.month, end.day)
    # include the end date day if end is not exactly midnight (bars could start on that day)
    if end > pd.Timestamp(end_date, tz="UTC"):
        end_date_inclusive = end_date
    else:
        # end is at midnight: the end_date day itself has no bars in [start, end)
        from datetime import timedelta
        end_date_inclusive = end_date - timedelta(days=1)

    from datetime import timedelta as _td

    while current <= end_date_inclusive:
        # Build path relative to root using the same Hive partition scheme as paths.parquet_path,
        # but rooted at `root` (which may be a tmp_path override, not paths.DATA_ROOT).
        canonical = paths.parquet_path(dataset, symbol, current)
        # Replace the DATA_ROOT prefix with our effective root.
        try:
            rel = canonical.relative_to(paths.DATA_ROOT)
            fpath = root / rel
        except ValueError:
            # canonical doesn't start with DATA_ROOT (shouldn't happen); fall back.
            fpath = canonical
        if fpath.exists():
            try:
                # Use ParquetFile to read a single file without Hive partition inference.
                pf = pq.ParquetFile(fpath)
                df = pf.read().to_pandas()
                frames.append(df)
            except Exception as exc:
                log.warning("Failed to read parquet file: %s — %s", fpath, exc)
        current += _td(days=1)

    if not frames:
        log.warning(
            "No parquet files found for %s %s in [%s, %s)",
            symbol, interval, start, end,
        )
        ohlcv = _empty_ohlcv()
        return BarsData(ohlcv=ohlcv, symbol=symbol, interval=interval)

    raw = pd.concat(frames, ignore_index=True)

    # open_time is UTC milliseconds; convert to UTC DatetimeIndex.
    raw["_ts"] = pd.to_datetime(raw["open_time"], unit="ms", utc=True)
    raw = raw.set_index("_ts").sort_index()

    # Filter to [start, end) — half-open.
    mask = (raw.index >= start) & (raw.index < end)
    raw = raw.loc[mask]

    ohlcv_cols = [
        "open", "high", "low", "close", "volume",
        "quote_volume", "taker_buy_volume", "taker_buy_quote_volume", "trade_count",
    ]
    # Keep only columns that exist in this file.
    available = [c for c in ohlcv_cols if c in raw.columns]
    ohlcv = raw[available].copy()
    ohlcv.index.name = None

    log.debug(
        "Loaded %d bars for %s %s from [%s, %s)",
        len(ohlcv), symbol, interval, start, end,
    )
    return BarsData(ohlcv=ohlcv, symbol=symbol, interval=interval)


def _empty_ohlcv() -> pd.DataFrame:
    cols = [
        "open", "high", "low", "close", "volume",
        "quote_volume", "taker_buy_volume", "taker_buy_quote_volume", "trade_count",
    ]
    idx = pd.DatetimeIndex([], tz="UTC")
    return pd.DataFrame(columns=cols, index=idx)


__all__ = ["OosGate", "OosViolationError", "load_bars"]
