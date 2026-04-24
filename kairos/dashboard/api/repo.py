"""SQLite v2 persistence layer for the dashboard backend — portfolio runs.

Schema and method signatures are frozen by docs/phase5/SCHEMA_V2.md — do not
deviate without a contract change. One process-wide connection guarded by a
threading.Lock; worker processes do not share this connection directly, they
re-open their own via ``_get_conn`` inside whichever process they are in.

Heavy artifacts (portfolio equity, per-symbol equity, trades) live on disk as
parquet files under ``<data_root>/run_artifacts/<run_id>/``.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from kairos.dashboard.api.dtos import (
    CreatePortfolioRunRequest,
    EquityPayload,
    EquityPayloadPerSymbol,
    EquityPayloadTotal,
    RunDetail,
    RunSummary,
)

# ---------------------------------------------------------------------------
# Paths / connection management
# ---------------------------------------------------------------------------

_V2_DDL = """
CREATE TABLE IF NOT EXISTS backtest_runs (
    id                      TEXT PRIMARY KEY,
    strategies_json         TEXT NOT NULL,
    weights_json            TEXT NOT NULL,
    symbols_json            TEXT NOT NULL,
    interval                TEXT NOT NULL CHECK(interval IN ('1m','15m')),
    start_iso               TEXT NOT NULL,
    end_iso                 TEXT NOT NULL,
    engine                  TEXT NOT NULL CHECK(engine IN ('vectorbt','backtrader')),
    initial_cash            REAL NOT NULL,
    apply_risk              INTEGER NOT NULL DEFAULT 1,
    status                  TEXT NOT NULL CHECK(status IN ('pending','running','done','failed')),
    created_at              TEXT NOT NULL,
    started_at              TEXT,
    finished_at             TEXT,
    metrics_json            TEXT,
    per_symbol_metrics_json TEXT,
    error_message           TEXT,
    artifacts_dir           TEXT,
    trades_count            INTEGER
);
CREATE INDEX IF NOT EXISTS idx_runs_created_at ON backtest_runs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_status     ON backtest_runs(status);
"""

_lock = threading.Lock()
# Cache connection per (pid, path) — sqlite connections cannot be shared across
# processes, so a forked/spawned child must open its own.
_conn_cache: dict[tuple[int, str], sqlite3.Connection] = {}


def _data_root() -> Path:
    # Tests override via KAIROS_DATA_ROOT so the on-disk db + parquets land in tmp_path.
    return Path(os.environ.get("KAIROS_DATA_ROOT", "data"))


def _db_path() -> Path:
    return _data_root() / "dashboard.db"


def _artifacts_dir(run_id: str) -> Path:
    return _data_root() / "run_artifacts" / run_id


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _migrate_if_legacy(conn: sqlite3.Connection) -> None:
    """Detect a Phase 4 single-symbol ``backtest_runs`` and rename it out of the way.

    Phase 4 data is prototype-only; renaming to ``backtest_runs_v1`` preserves
    it for forensic inspection while letting the v2 CREATE run on a clean slot.
    """
    cols = conn.execute("PRAGMA table_info(backtest_runs)").fetchall()
    if not cols:
        return
    col_names = {str(c[1]) for c in cols}
    if "strategies_json" in col_names:
        return  # Already v2.
    # Drop any prior rename target so repeated boots on legacy dbs are idempotent.
    conn.execute("DROP TABLE IF EXISTS backtest_runs_v1")
    conn.execute("ALTER TABLE backtest_runs RENAME TO backtest_runs_v1")


def _get_conn() -> sqlite3.Connection:
    """Return a process-local connection, creating the DB + DDL on first use."""
    key = (os.getpid(), str(_db_path()))
    conn = _conn_cache.get(key)
    if conn is None:
        path = _db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        _migrate_if_legacy(conn)
        conn.executescript(_V2_DDL)
        _conn_cache[key] = conn
    return conn


def reset_cache_for_tests() -> None:
    """Close + forget cached connections. Used by fixtures that rotate DB paths."""
    with _lock:
        for c in _conn_cache.values():
            try:
                c.close()
            except Exception:
                pass
        _conn_cache.clear()


# ---------------------------------------------------------------------------
# Row <-> DTO helpers
# ---------------------------------------------------------------------------


def _parse_json_obj(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    return json.loads(raw)


def _parse_metrics(raw: str | None) -> dict[str, float] | None:
    if not raw:
        return None
    obj = json.loads(raw)
    # Coerce to float so the DTO type holds; int keys (trades_count) stay numeric.
    return {str(k): float(v) for k, v in obj.items()}


def _parse_per_symbol_metrics(
    raw: str | None,
) -> dict[str, dict[str, float]] | None:
    if not raw:
        return None
    obj = json.loads(raw)
    return {
        str(sym): {str(k): float(v) for k, v in sub.items()}
        for sym, sub in obj.items()
    }


def _row_to_summary(row: sqlite3.Row) -> RunSummary:
    strategies_full = json.loads(row["strategies_json"])  # {sym: {name, params}}
    # RunSummary only carries symbol->name mapping; params live in RunDetail.
    strategies_names = {
        str(sym): str(spec.get("name", "")) for sym, spec in strategies_full.items()
    }
    weights = {str(k): float(v) for k, v in json.loads(row["weights_json"]).items()}
    symbols = list(json.loads(row["symbols_json"]))

    metrics = _parse_metrics(row["metrics_json"])
    kwargs: dict[str, Any] = {
        "id": row["id"],
        "symbols": symbols,
        "strategies": strategies_names,
        "weights": weights,
        "interval": row["interval"],
        "start": row["start_iso"],
        "end": row["end_iso"],
        "engine": row["engine"],
        "status": row["status"],
        "created_at": row["created_at"],
    }
    if metrics is not None:
        if "cagr" in metrics:
            kwargs["cagr"] = float(metrics["cagr"])
        if "mdd" in metrics:
            kwargs["mdd"] = float(metrics["mdd"])
        if "sharpe" in metrics:
            kwargs["sharpe"] = float(metrics["sharpe"])
        if "total_trades" in metrics:
            kwargs["total_trades"] = int(metrics["total_trades"])
        elif row["trades_count"] is not None:
            kwargs["total_trades"] = int(row["trades_count"])
    elif row["trades_count"] is not None:
        kwargs["total_trades"] = int(row["trades_count"])
    return RunSummary(**kwargs)


def _row_to_detail(row: sqlite3.Row) -> RunDetail:
    summary = _row_to_summary(row)
    strategies_full = json.loads(row["strategies_json"])  # {sym: {name, params}}
    strategy_params = {
        str(sym): dict(spec.get("params", {})) for sym, spec in strategies_full.items()
    }
    metrics = _parse_metrics(row["metrics_json"])
    per_symbol_metrics = _parse_per_symbol_metrics(row["per_symbol_metrics_json"])
    return RunDetail(
        **summary.model_dump(),
        strategy_params=strategy_params,
        initial_cash=float(row["initial_cash"]),
        apply_risk_simulator=bool(row["apply_risk"]),
        metrics=metrics,
        per_symbol_metrics=per_symbol_metrics,
        error=row["error_message"],
    )


# ---------------------------------------------------------------------------
# Repository API (signatures frozen — SCHEMA_V2.md §Repository methods)
# ---------------------------------------------------------------------------


def create_run(req: CreatePortfolioRunRequest) -> str:
    """Insert a pending portfolio run; return its ``run_<uuid>`` id."""
    run_id = "run_" + uuid.uuid4().hex
    strategies_payload = {
        sym: {"name": spec.name, "params": dict(spec.params)}
        for sym, spec in req.strategies.items()
    }
    weights_payload = {sym: float(w) for sym, w in req.weights.items()}
    # Denormalized sorted symbol list lets listing queries avoid decoding the full spec.
    symbols_payload = sorted(req.strategies.keys())
    with _lock:
        conn = _get_conn()
        conn.execute(
            """
            INSERT INTO backtest_runs (
                id, strategies_json, weights_json, symbols_json,
                interval, start_iso, end_iso, engine,
                initial_cash, apply_risk, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (
                run_id,
                json.dumps(strategies_payload),
                json.dumps(weights_payload),
                json.dumps(symbols_payload),
                req.interval,
                req.start,
                req.end,
                req.engine,
                float(req.initial_cash),
                1 if req.apply_risk_simulator else 0,
                _now_iso(),
            ),
        )
    return run_id


def get_run(run_id: str) -> RunDetail | None:
    with _lock:
        conn = _get_conn()
        row = conn.execute(
            "SELECT * FROM backtest_runs WHERE id = ?", (run_id,)
        ).fetchone()
    if row is None:
        return None
    return _row_to_detail(row)


def list_runs(limit: int = 100, status: str | None = None) -> list[RunSummary]:
    with _lock:
        conn = _get_conn()
        if status is None:
            rows = conn.execute(
                "SELECT * FROM backtest_runs ORDER BY created_at DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM backtest_runs WHERE status = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (status, int(limit)),
            ).fetchall()
    return [_row_to_summary(r) for r in rows]


def mark_running(run_id: str) -> None:
    with _lock:
        conn = _get_conn()
        conn.execute(
            "UPDATE backtest_runs SET status = 'running', started_at = ? WHERE id = ?",
            (_now_iso(), run_id),
        )


def mark_done(
    run_id: str,
    metrics: Mapping[str, float],
    per_symbol_metrics: Mapping[str, Mapping[str, float]],
    artifacts_dir: str,
    trades_count: int,
) -> None:
    metrics_json = json.dumps({str(k): float(v) for k, v in metrics.items()})
    per_symbol_json = json.dumps(
        {
            str(sym): {str(k): float(v) for k, v in sub.items()}
            for sym, sub in per_symbol_metrics.items()
        }
    )
    with _lock:
        conn = _get_conn()
        conn.execute(
            """
            UPDATE backtest_runs
               SET status                  = 'done',
                   metrics_json            = ?,
                   per_symbol_metrics_json = ?,
                   artifacts_dir           = ?,
                   trades_count            = ?,
                   finished_at             = ?
             WHERE id = ?
            """,
            (
                metrics_json,
                per_symbol_json,
                artifacts_dir,
                int(trades_count),
                _now_iso(),
                run_id,
            ),
        )


def mark_failed(run_id: str, error: str) -> None:
    with _lock:
        conn = _get_conn()
        conn.execute(
            """
            UPDATE backtest_runs
               SET status        = 'failed',
                   error_message = ?,
                   finished_at   = ?
             WHERE id = ?
            """,
            (error, _now_iso(), run_id),
        )


# ---------------------------------------------------------------------------
# Artifact readers — LTTB downsampling + trade pagination
# ---------------------------------------------------------------------------


def _lttb_downsample(
    ts: list[str], values: list[float], threshold: int
) -> tuple[list[str], list[float]]:
    """Largest-Triangle-Three-Buckets down-sampler.

    Preserves first + last points; returns at most ``threshold`` samples.
    Pure Python — fast enough for tens-of-thousands of bars and avoids a numpy
    conversion round-trip over string-typed timestamps.
    """
    n = len(values)
    if threshold >= n or threshold < 3:
        return list(ts), list(values)

    bucket_size = (n - 2) / (threshold - 2)

    out_ts: list[str] = [ts[0]]
    out_v: list[float] = [values[0]]

    a = 0  # index of the previously selected point
    for i in range(threshold - 2):
        next_start = int((i + 1) * bucket_size) + 1
        next_end = min(int((i + 2) * bucket_size) + 1, n)
        if next_start >= next_end:
            avg_x = float(n - 1)
            avg_y = values[-1]
        else:
            avg_x = (next_start + next_end - 1) / 2.0
            avg_y = sum(values[next_start:next_end]) / float(next_end - next_start)

        cur_start = int(i * bucket_size) + 1
        cur_end = min(int((i + 1) * bucket_size) + 1, n)
        if cur_start >= cur_end:
            continue

        a_x = float(a)
        a_y = values[a]
        max_area = -1.0
        max_idx = cur_start
        for j in range(cur_start, cur_end):
            area = abs(
                (a_x - avg_x) * (values[j] - a_y)
                - (a_x - float(j)) * (avg_y - a_y)
            ) * 0.5
            if area > max_area:
                max_area = area
                max_idx = j
        out_ts.append(ts[max_idx])
        out_v.append(values[max_idx])
        a = max_idx

    out_ts.append(ts[-1])
    out_v.append(values[-1])
    return out_ts, out_v


def _downsample_multi(
    ts: list[str], series: dict[str, list[float]], threshold: int
) -> tuple[list[str], dict[str, list[float]]]:
    """Downsample a family of curves on a shared time axis.

    We pick indices using the sum across symbols so every series is sampled at
    exactly the same timestamps (required for stacked-area rendering).
    """
    n = len(ts)
    if threshold >= n or threshold < 3 or not series:
        return list(ts), {sym: list(v) for sym, v in series.items()}

    totals = [sum(series[sym][i] for sym in series) for i in range(n)]
    reduced_ts, _ = _lttb_downsample(ts, totals, threshold)

    # Build an index from the sampled timestamps back into the full axis. The
    # timestamps are unique by construction (one per bar), so direct lookup works.
    index_of = {t: i for i, t in enumerate(ts)}
    picked = [index_of[t] for t in reduced_ts]
    reduced = {sym: [series[sym][i] for i in picked] for sym in series}
    return reduced_ts, reduced


def load_equity(run_id: str, *, per_symbol: bool = False) -> EquityPayload:
    """Read equity parquet for a run, LTTB-downsampled to ≤ 5000 points.

    ``per_symbol=False`` returns the portfolio total from ``equity.parquet``;
    ``per_symbol=True`` returns one curve per symbol from ``equity_per_symbol.parquet``.
    Missing artifact files produce an empty payload (not an error) so callers
    can distinguish "run still pending" from "run failed".
    """
    artifacts = _artifacts_dir(run_id)
    if per_symbol:
        path = artifacts / "equity_per_symbol.parquet"
        if not path.exists():
            return EquityPayloadPerSymbol(timestamps=[], per_symbol={})
        df = pd.read_parquet(path)
        if "timestamp" not in df.columns:
            raise ValueError(
                f"per-symbol equity parquet at {path} missing 'timestamp' column "
                f"(got {list(df.columns)})"
            )
        ts = [str(x) for x in df["timestamp"].tolist()]
        symbol_cols = [c for c in df.columns if c != "timestamp"]
        series = {sym: [float(x) for x in df[sym].tolist()] for sym in symbol_cols}
        if ts and len(ts) > 5000:
            ts, series = _downsample_multi(ts, series, 5000)
        return EquityPayloadPerSymbol(timestamps=ts, per_symbol=series)

    path = artifacts / "equity.parquet"
    if not path.exists():
        return EquityPayloadTotal(timestamps=[], equity=[])
    df = pd.read_parquet(path)
    if "timestamp" not in df.columns or "equity" not in df.columns:
        raise ValueError(
            f"equity parquet at {path} missing required columns "
            f"(expected 'timestamp' and 'equity', got {list(df.columns)})"
        )
    ts = [str(x) for x in df["timestamp"].tolist()]
    eq = [float(x) for x in df["equity"].tolist()]
    if len(eq) > 5000:
        ts, eq = _lttb_downsample(ts, eq, 5000)
    return EquityPayloadTotal(timestamps=ts, equity=eq)


def load_trades(
    run_id: str, offset: int, limit: int
) -> tuple[list[dict[str, Any]], int]:
    """Page through the trades parquet; returns plain dicts + total count.

    Returning dicts (not Trade instances) keeps this layer cheap — routes can
    materialize Pydantic models where they actually need validation.
    """
    path = _artifacts_dir(run_id) / "trades.parquet"
    if not path.exists():
        return [], 0
    df = pd.read_parquet(path)
    total = int(len(df))
    if total == 0 or limit <= 0:
        return [], total
    lo = max(0, int(offset))
    hi = min(total, lo + int(limit))
    page = df.iloc[lo:hi]

    out: list[dict[str, Any]] = []
    for rec in page.to_dict(orient="records"):
        out.append(
            {
                "symbol": str(rec["symbol"]),
                "entry_time": _ts_to_iso(rec["entry_time"]),
                "exit_time": _ts_to_iso(rec["exit_time"]),
                "side": int(rec["side"]),
                "qty": float(rec["qty"]),
                "entry_price": float(rec["entry_price"]),
                "exit_price": float(rec["exit_price"]),
                "gross_pnl": float(rec["gross_pnl"]),
                "fees": float(rec["fees"]),
                "slippage": float(rec["slippage"]),
                "funding_cost": float(rec["funding_cost"]),
                "net_pnl": float(rec["net_pnl"]),
                "bars_held": int(rec["bars_held"]),
                "exit_reason": str(rec["exit_reason"]),
            }
        )
    return out, total


def _ts_to_iso(ts: object) -> str:
    """Accept pandas Timestamp, datetime, or pre-formatted string."""
    if isinstance(ts, str):
        return ts
    if isinstance(ts, pd.Timestamp):
        t = ts.tz_convert("UTC") if ts.tzinfo is not None else ts.tz_localize("UTC")
        return t.strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(ts, datetime):
        t = ts.astimezone(timezone.utc) if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        return t.strftime("%Y-%m-%dT%H:%M:%SZ")
    # Fall back to str(); pandas to_dict can hand us numpy datetime64 in some versions.
    return str(ts)


__all__ = [
    "create_run",
    "get_run",
    "list_runs",
    "load_equity",
    "load_trades",
    "mark_done",
    "mark_failed",
    "mark_running",
    "reset_cache_for_tests",
]
