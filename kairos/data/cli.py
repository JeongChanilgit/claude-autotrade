"""CLI entrypoint: `python -m kairos.data.cli download --symbols BTCUSDT --start 2024-01-01 --end 2024-01-31`.

Phase 1 skeleton. Sonnet-1A/1B/1C populate each dataset's downloader;
Sonnet-1D wires the DuckDB query layer and quality report commands.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timedelta

from kairos.data.protocols import Dataset

log = logging.getLogger("kairos.data.cli")

ALL_DATASETS: tuple[Dataset, ...] = ("klines_1m", "klines_15m", "funding", "metrics")


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _daterange(start: date, end: date) -> list[date]:
    days = (end - start).days + 1
    return [start + timedelta(days=i) for i in range(days)]


def _months_in_range(start: date, end: date) -> list[date]:
    """First-of-month dates covering [start, end]."""
    out: list[date] = []
    cur = start.replace(day=1)
    end_anchor = end.replace(day=1)
    while cur <= end_anchor:
        out.append(cur)
        year, month = (cur.year + 1, 1) if cur.month == 12 else (cur.year, cur.month + 1)
        cur = date(year, month, 1)
    return out


def cmd_download(args: argparse.Namespace) -> int:
    """Dispatch to per-dataset downloaders. Populated by Sonnet-1D once individual
    downloaders land. Until then this just prints what would run.
    """
    symbols: list[str] = args.symbols
    start: date = _parse_date(args.start)
    end: date = _parse_date(args.end)
    datasets: list[Dataset] = args.datasets or list(ALL_DATASETS)

    # TODO(Sonnet-1D): wire to real downloaders from kairos.data.download_*
    log.warning("download CLI is a stub — individual downloaders land with Sonnet-1A/B/C")
    for ds in datasets:
        periods = _months_in_range(start, end) if ds == "funding" else _daterange(start, end)
        log.info("would download %s for %s: %d periods", ds, ",".join(symbols), len(periods))
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    log.warning("quality report CLI is a stub — Sonnet-1D will populate")
    _ = args
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    parser = argparse.ArgumentParser(prog="kairos.data.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)

    pd_ = sub.add_parser("download", help="Download Binance Vision archives")
    pd_.add_argument("--symbols", nargs="+", required=True)
    pd_.add_argument("--start", required=True, help="YYYY-MM-DD inclusive")
    pd_.add_argument("--end", required=True, help="YYYY-MM-DD inclusive")
    pd_.add_argument(
        "--datasets",
        nargs="+",
        choices=ALL_DATASETS,
        default=None,
        help="Subset to download (default: all Phase 1 datasets)",
    )
    pd_.set_defaults(func=cmd_download)

    pr = sub.add_parser("quality-report", help="Write data coverage + gap report")
    pr.add_argument("--symbols", nargs="+", required=True)
    pr.set_defaults(func=cmd_report)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
