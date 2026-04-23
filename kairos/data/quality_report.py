"""Coverage + sanity quality report for downloaded parquet partitions."""
from __future__ import annotations

import argparse
import calendar
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import duckdb

from kairos.data import paths
from kairos.data.protocols import Dataset

MISSING_PREVIEW = 20

_DAILY_DATASETS: tuple[Dataset, ...] = ("klines_1m", "klines_15m", "metrics")

_ROW_BOUNDS: dict[Dataset, tuple[int, int, int]] = {
    "klines_1m": (1440, 1430, 1450),
    "klines_15m": (96, 94, 98),
    "metrics": (288, 280, 295),
}


@dataclass(slots=True)
class DatasetReport:
    dataset: Dataset
    symbol: str
    expected_periods: int
    present_periods: int
    missing_periods: list[date] = field(default_factory=list)
    missing_overflow: int = 0
    row_count_anomalies: list[tuple[date, int, str]] = field(default_factory=list)
    price_anomalies: list[tuple[date, int, str]] = field(default_factory=list)


@dataclass(slots=True)
class QualityReport:
    records: list[DatasetReport] = field(default_factory=list)


def _daterange_days(start: date, end: date) -> list[date]:
    days: list[date] = []
    cur = start
    while cur <= end:
        days.append(cur)
        cur += timedelta(days=1)
    return days


def _month_starts(start: date, end: date) -> list[date]:
    months: list[date] = []
    cur = start.replace(day=1)
    last = end.replace(day=1)
    while cur <= last:
        months.append(cur)
        if cur.month == 12:
            cur = cur.replace(year=cur.year + 1, month=1)
        else:
            cur = cur.replace(month=cur.month + 1)
    return months


def _truncate_missing(missing: list[date]) -> tuple[list[date], int]:
    if len(missing) <= MISSING_PREVIEW:
        return missing, 0
    return missing[:MISSING_PREVIEW], len(missing) - MISSING_PREVIEW


def _data_root(data_root: Path | None) -> Path:
    return data_root if data_root is not None else paths.DATA_ROOT


def _parquet_path(dataset: Dataset, symbol: str, period: date, root: Path) -> Path:
    default = paths.parquet_path(dataset, symbol, period)
    return root / default.relative_to(paths.DATA_ROOT)


def _row_count(con: duckdb.DuckDBPyConnection, path: Path) -> int:
    return con.execute(
        f"SELECT count(*) FROM read_parquet('{path.as_posix()}')"
    ).fetchone()[0]  # type: ignore[index]


def _ohlc_anomaly_count(con: duckdb.DuckDBPyConnection, path: Path) -> int:
    sql = (
        f"SELECT count(*) FROM read_parquet('{path.as_posix()}') "
        "WHERE low > open OR low > close OR high < open OR high < close "
        "OR low <= 0 OR high <= 0 OR close < low OR close > high"
    )
    return con.execute(sql).fetchone()[0]  # type: ignore[index]


def _funding_anomaly_count(con: duckdb.DuckDBPyConnection, path: Path) -> int:
    sql = (
        f"SELECT count(*) FROM read_parquet('{path.as_posix()}') "
        "WHERE abs(funding_rate) > 0.01"
    )
    return con.execute(sql).fetchone()[0]  # type: ignore[index]


def _metrics_anomaly_count(con: duckdb.DuckDBPyConnection, path: Path) -> int:
    sql = (
        f"SELECT count(*) FROM read_parquet('{path.as_posix()}') "
        "WHERE sum_open_interest <= 0"
    )
    return con.execute(sql).fetchone()[0]  # type: ignore[index]


def _report_daily(
    con: duckdb.DuckDBPyConnection,
    dataset: Dataset,
    symbol: str,
    days: list[date],
    root: Path,
) -> DatasetReport:
    expected, lo, hi = _ROW_BOUNDS[dataset]
    missing: list[date] = []
    row_anom: list[tuple[date, int, str]] = []
    price_anom: list[tuple[date, int, str]] = []
    present = 0

    for day in days:
        path = _parquet_path(dataset, symbol, day, root)
        if not path.exists():
            missing.append(day)
            continue
        present += 1
        rows = _row_count(con, path)
        if rows < lo or rows > hi:
            row_anom.append((day, rows, f"row count {rows} outside [{lo},{hi}] (expected ~{expected})"))
        if dataset in ("klines_1m", "klines_15m"):
            bad = _ohlc_anomaly_count(con, path)
            if bad > 0:
                price_anom.append((day, bad, "OHLC inconsistency"))
        if dataset == "metrics":
            bad = _metrics_anomaly_count(con, path)
            if bad > 0:
                row_anom.append((day, bad, "sum_open_interest <= 0"))

    truncated, overflow = _truncate_missing(missing)
    return DatasetReport(
        dataset=dataset,
        symbol=symbol,
        expected_periods=len(days),
        present_periods=present,
        missing_periods=truncated,
        missing_overflow=overflow,
        row_count_anomalies=row_anom,
        price_anomalies=price_anom,
    )


def _report_funding(
    con: duckdb.DuckDBPyConnection,
    symbol: str,
    months: list[date],
    root: Path,
) -> DatasetReport:
    missing: list[date] = []
    row_anom: list[tuple[date, int, str]] = []
    present = 0

    for month_start in months:
        path = _parquet_path("funding", symbol, month_start, root)
        if not path.exists():
            missing.append(month_start)
            continue
        present += 1
        rows = _row_count(con, path)
        days_in_month = calendar.monthrange(month_start.year, month_start.month)[1]
        expected = 3 * days_in_month
        lo = int(expected * 0.9)
        hi = int(expected * 1.1)
        if rows < lo or rows > hi:
            row_anom.append((month_start, rows, f"row count {rows} outside [{lo},{hi}] (expected ~{expected})"))
        bad = _funding_anomaly_count(con, path)
        if bad > 0:
            row_anom.append((month_start, bad, "|funding_rate| > 0.01"))

    truncated, overflow = _truncate_missing(missing)
    return DatasetReport(
        dataset="funding",
        symbol=symbol,
        expected_periods=len(months),
        present_periods=present,
        missing_periods=truncated,
        missing_overflow=overflow,
        row_count_anomalies=row_anom,
        price_anomalies=[],
    )


def generate_report(
    symbols: list[str],
    start: date,
    end: date,
    data_root: Path | None = None,
) -> QualityReport:
    root = _data_root(data_root)
    days = _daterange_days(start, end)
    months = _month_starts(start, end)
    con = duckdb.connect(":memory:")

    records: list[DatasetReport] = []
    for symbol in symbols:
        for dataset in _DAILY_DATASETS:
            records.append(_report_daily(con, dataset, symbol, days, root))
        records.append(_report_funding(con, symbol, months, root))

    return QualityReport(records=records)


def _format_report(report: QualityReport) -> str:
    lines: list[str] = []
    for r in report.records:
        lines.append(
            f"[{r.dataset}] {r.symbol}: {r.present_periods}/{r.expected_periods} periods present"
        )
        if r.missing_periods:
            shown = ", ".join(d.isoformat() for d in r.missing_periods)
            tail = f" ...and {r.missing_overflow} more" if r.missing_overflow else ""
            lines.append(f"  missing: {shown}{tail}")
        for period, count, reason in r.row_count_anomalies:
            lines.append(f"  ! {period.isoformat()}  rows={count}  {reason}")
        for period, count, reason in r.price_anomalies:
            lines.append(f"  ! {period.isoformat()}  bad={count}  {reason}")
    return "\n".join(lines) if lines else "(no records)"


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kairos.data.quality_report")
    parser.add_argument("--symbols", nargs="+", required=True)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--data-root", type=Path, default=None)
    args = parser.parse_args(argv)

    report = generate_report(args.symbols, args.start, args.end, args.data_root)
    print(_format_report(report))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
