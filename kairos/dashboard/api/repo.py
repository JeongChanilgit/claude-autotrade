"""SQLite persistence layer for the dashboard backend.

Schema and method signatures are frozen by docs/phase4/SCHEMA.md — do not
deviate without a contract change. One process-wide connection guarded by a
threading.Lock; worker processes do not share this connection directly, they
re-open their own via ``_get_conn`` inside whichever process they are in.

Heavy artifacts (equity curve, trades frame) live on disk as parquet files
under ``<data_root>/run_artifacts/<run_id>/``. The ``equity_path`` column on
the row points at that directory.
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

import pandas as pd

from kairos.dashboard.api.dtos import (
    CreateRunRequest,
    EngineName,
    RunDetail,
    RunStatus,
    RunSummary,
    Trade,
)

# ---------------------------------------------------------------------------
# Paths / connection management
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS backtest_runs (
    id               TEXT PRIMARY KEY,
    strategy         TEXT NOT NULL,
    symbol           TEXT NOT NULL,
    interval         TEXT NOT NULL CHECK(interval IN ('1m','15m')),
    start_iso        TEXT NOT NULL,
    end_iso          TEXT NOT NULL,
    engine           TEXT NOT NULL CHECK(engine IN ('vectorbt','backtrader')),
    params_json      TEXT NOT NULL,
    initial_cash     REAL NOT NULL,
    apply_risk       INTEGER NOT NULL DEFAULT 1,
    status           TEXT NOT NULL CHECK(status IN ('pending','running','done','failed')),
    created_at       TEXT NOT NULL,
    started_at       TEXT,
    finished_at      TEXT,
    metrics_json     TEXT,
    error_message    TEXT,
    equity_path      TEXT,
    trades_count     INTEGER
);
CREATE INDEX IF NOT EXISTS idx_runs_created_at ON backtest_runs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_status     ON backtest_runs(status);
"""

_lock = threading.Lock()
# Cache connection per (pid, path) — sqlite connections cannot be shared across
# processes, so a forked/spawned child must open its own.
_conn_cache: dict[tuple[int, str], sqlite3.Connection] = {}


def _data_root() -> Path:
    """Resolve the data root from env (tests override via KAIROS_DATA_ROOT)."""
    return Path(os.environ.get("KAIROS_DATA_ROOT", "data"))


def _db_path() -> Path:
    return _data_root() / "dashboard.db"


def _artifacts_dir(run_id: str) -> Path:
    return _data_root() / "run_artifacts" / run_id


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get_conn() -> sqlite3.Connection:
    """Return a process-local connection, creating the DB + DDL on first use."""
    key = (os.getpid(), str(_db_path()))
    conn = _conn_cache.get(key)
    if conn is None:
        path = _db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.executescript(_DDL)
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


def _row_to_summary(row: sqlite3.Row) -> RunSummary:
    metrics = _parse_metrics(row["metrics_json"])
    summary_kwargs: dict[str, object] = {
        "id": row["id"],
        "strategy": row["strategy"],
        "symbol": row["symbol"],
        "interval": row["interval"],
        "start": row["start_iso"],
        "end": row["end_iso"],
        "engine": row["engine"],
        "status": row["status"],
        "created_at": row["created_at"],
    }
    if metrics is not None:
        if "cagr" in metrics:
            summary_kwargs["cagr"] = float(metrics["cagr"])
        if "mdd" in metrics:
            summary_kwargs["mdd"] = float(metrics["mdd"])
        if "sharpe" in metrics:
            summary_kwargs["sharpe"] = float(metrics["sharpe"])
        if "total_trades" in metrics:
            summary_kwargs["total_trades"] = int(metrics["total_trades"])
        elif row["trades_count"] is not None:
            summary_kwargs["total_trades"] = int(row["trades_count"])
    return RunSummary(**summary_kwargs)


def _row_to_detail(row: sqlite3.Row) -> RunDetail:
    summary = _row_to_summary(row)
    metrics = _parse_metrics(row["metrics_json"])
    params = json.loads(row["params_json"]) if row["params_json"] else {}
    return RunDetail(
        **summary.model_dump(),
        params=params,
        initial_cash=float(row["initial_cash"]),
        apply_risk_simulator=bool(row["apply_risk"]),
        metrics=metrics,
        error=row["error_message"],
    )


def _parse_metrics(raw: str | None) -> dict[str, float] | None:
    if not raw:
        return None
    obj = json.loads(raw)
    # Coerce everything to float so the DTO type holds.
    return {str(k): float(v) for k, v in obj.items()}


# ---------------------------------------------------------------------------
# Repository API (signatures frozen — SCHEMA.md §Repository methods)
# ---------------------------------------------------------------------------


def create_run(req: CreateRunRequest) -> str:
    """Insert a pending run; return its ``run_<uuid>`` id."""
    run_id = "run_" + uuid.uuid4().hex
    with _lock:
        conn = _get_conn()
        conn.execute(
            """
            INSERT INTO backtest_runs (
                id, strategy, symbol, interval, start_iso, end_iso,
                engine, params_json, initial_cash, apply_risk,
                status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (
                run_id,
                req.strategy,
                req.symbol,
                req.interval,
                req.start,
                req.end,
                req.engine,
                json.dumps(dict(req.params)),
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


def update_status(run_id: str, status: str, *, error: str | None = None) -> None:
    """Generic status setter — prefer the specific helpers below."""
    with _lock:
        conn = _get_conn()
        conn.execute(
            "UPDATE backtest_runs SET status = ?, error_message = COALESCE(?, error_message) "
            "WHERE id = ?",
            (status, error, run_id),
        )


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
    equity_path: str,
    trades_count: int,
) -> None:
    with _lock:
        conn = _get_conn()
        conn.execute(
            """
            UPDATE backtest_runs
               SET status       = 'done',
                   metrics_json = ?,
                   equity_path  = ?,
                   trades_count = ?,
                   finished_at  = ?
             WHERE id = ?
            """,
            (
                json.dumps({k: float(v) for k, v in metrics.items()}),
                equity_path,
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
    No numpy; pure Python is fast enough for the ~tens-of-thousands bars we
    hit in practice and avoids allocating parallel arrays.
    """
    n = len(values)
    if threshold >= n or threshold < 3:
        return list(ts), list(values)

    # Each of the middle (threshold - 2) points is picked from a bucket of
    # size (n - 2) / (threshold - 2).
    bucket_size = (n - 2) / (threshold - 2)

    out_ts: list[str] = [ts[0]]
    out_v: list[float] = [values[0]]

    a = 0  # index of the previously selected point
    for i in range(threshold - 2):
        # Range of the NEXT bucket (used for avg point).
        next_start = int((i + 1) * bucket_size) + 1
        next_end = min(int((i + 2) * bucket_size) + 1, n)
        if next_start >= next_end:
            avg_x = float(n - 1)
            avg_y = values[-1]
        else:
            avg_x = (next_start + next_end - 1) / 2.0
            avg_y = sum(values[next_start:next_end]) / float(next_end - next_start)

        # Range of the CURRENT bucket (we pick one point from here).
        cur_start = int(i * bucket_size) + 1
        cur_end = min(int((i + 1) * bucket_size) + 1, n)
        if cur_start >= cur_end:
            continue

        # Compute the triangle area for every candidate; choose argmax.
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


def load_equity(run_id: str) -> tuple[list[str], list[float]]:
    """Read the equity parquet for a run, LTTB-downsampled to ≤5000 points."""
    path = _artifacts_dir(run_id) / "equity.parquet"
    if not path.exists():
        return [], []
    df = pd.read_parquet(path)
    # Required columns per the worker spec.
    ts_col = "timestamp"
    eq_col = "equity"
    if ts_col not in df.columns or eq_col not in df.columns:
        raise ValueError(
            f"equity parquet at {path} missing required columns (expected "
            f"{ts_col!r} and {eq_col!r}, got {list(df.columns)})"
        )
    ts = [str(x) for x in df[ts_col].tolist()]
    eq = [float(x) for x in df[eq_col].tolist()]
    if len(eq) > 5000:
        ts, eq = _lttb_downsample(ts, eq, 5000)
    return ts, eq


def load_trades(
    run_id: str, offset: int, limit: int
) -> tuple[list[Trade], int]:
    """Page through the trades parquet."""
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
    trades: list[Trade] = []
    for rec in page.to_dict(orient="records"):
        trades.append(
            Trade(
                entry_time=_ts_to_iso(rec["entry_time"]),
                exit_time=_ts_to_iso(rec["exit_time"]),
                side=int(rec["side"]),  # type: ignore[arg-type]
                qty=float(rec["qty"]),
                entry_price=float(rec["entry_price"]),
                exit_price=float(rec["exit_price"]),
                gross_pnl=float(rec["gross_pnl"]),
                fees=float(rec["fees"]),
                slippage=float(rec["slippage"]),
                funding_cost=float(rec["funding_cost"]),
                net_pnl=float(rec["net_pnl"]),
                bars_held=int(rec["bars_held"]),
                exit_reason=str(rec["exit_reason"]),  # type: ignore[arg-type]
            )
        )
    return trades, total


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
    # Fall back to str(); pandas to_dict may hand us numpy datetime64 in some versions.
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
    "update_status",
]
