# Phase 4 — SQLite Schema

**FROZEN** — Sonnet-4B implements verbatim. File location: `data/dashboard.db`
(gitignored — rebuilt from filesystem on cold start).

```sql
CREATE TABLE IF NOT EXISTS backtest_runs (
    id               TEXT PRIMARY KEY,              -- "run_" + uuid4 hex
    strategy         TEXT NOT NULL,
    symbol           TEXT NOT NULL,
    interval         TEXT NOT NULL CHECK(interval IN ('1m','15m')),
    start_iso        TEXT NOT NULL,                 -- UTC ISO-8601
    end_iso          TEXT NOT NULL,                 -- UTC ISO-8601
    engine           TEXT NOT NULL CHECK(engine IN ('vectorbt','backtrader')),
    params_json      TEXT NOT NULL,                 -- JSON
    initial_cash     REAL NOT NULL,
    apply_risk       INTEGER NOT NULL DEFAULT 1,    -- 0|1
    status           TEXT NOT NULL CHECK(status IN ('pending','running','done','failed')),
    created_at       TEXT NOT NULL,                 -- UTC ISO-8601
    started_at       TEXT,
    finished_at      TEXT,
    metrics_json     TEXT,                          -- JSON, populated on 'done'
    error_message    TEXT,                          -- populated on 'failed'
    equity_path      TEXT,                          -- path to parquet with equity/trades
    trades_count     INTEGER
);

CREATE INDEX IF NOT EXISTS idx_runs_created_at ON backtest_runs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_status     ON backtest_runs(status);
```

## Persistence rules

- One SQLite row per run. Heavy artifacts (equity curve, trades DataFrame) live
  on disk as `data/run_artifacts/<run_id>/{equity.parquet, trades.parquet}`.
  `equity_path` points at the directory.
- `metrics_json` is the frozen Mapping from BacktestResult.metrics (JSON-serialized).
- Status transitions: `pending → running → done | failed`.
- DB connection is a single `sqlite3.Connection` with `check_same_thread=False`
  behind a process-wide `threading.Lock`. The worker processes speak to the DB
  through a lightweight IPC wrapper (no direct connection sharing across procs).

## Repository methods

`kairos/dashboard/api/repo.py` exposes:
```python
def create_run(req: CreateRunRequest) -> str: ...
def get_run(run_id: str) -> RunDetail | None: ...
def list_runs(limit: int = 100, status: str | None = None) -> list[RunSummary]: ...
def update_status(run_id: str, status: str, *, error: str | None = None) -> None: ...
def mark_running(run_id: str) -> None: ...
def mark_done(run_id: str, metrics: Mapping[str, float], equity_path: str, trades_count: int) -> None: ...
def mark_failed(run_id: str, error: str) -> None: ...
def load_equity(run_id: str) -> tuple[list[str], list[float]]: ...
def load_trades(run_id: str, offset: int, limit: int) -> tuple[list[Trade], int]: ...
```
