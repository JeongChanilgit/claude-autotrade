# Phase 5 — SQLite Schema v2 (Portfolio, FROZEN)

The Phase 4 `backtest_runs` table is **dropped and recreated** (no row-preserving
migration — Phase 4 was prototype data, fine to wipe). On startup, repo creates
the v2 table if not present; existing v1 rows are deleted via `DROP TABLE IF EXISTS backtest_runs_v1;`
and `ALTER TABLE backtest_runs RENAME TO backtest_runs_v1;` if the legacy schema
is detected.

## DDL

```sql
CREATE TABLE IF NOT EXISTS backtest_runs (
    id                    TEXT PRIMARY KEY,
    strategies_json       TEXT NOT NULL,           -- {symbol: {name: str, params: dict}}
    weights_json          TEXT NOT NULL,           -- {symbol: float}
    symbols_json          TEXT NOT NULL,           -- sorted list[str], denormalized for quick listing
    interval              TEXT NOT NULL CHECK(interval IN ('1m','15m')),
    start_iso             TEXT NOT NULL,
    end_iso               TEXT NOT NULL,
    engine                TEXT NOT NULL CHECK(engine IN ('vectorbt','backtrader')),
    initial_cash          REAL NOT NULL,
    apply_risk            INTEGER NOT NULL DEFAULT 1,
    status                TEXT NOT NULL CHECK(status IN ('pending','running','done','failed')),
    created_at            TEXT NOT NULL,
    started_at            TEXT,
    finished_at           TEXT,
    metrics_json          TEXT,                    -- portfolio-level metrics
    per_symbol_metrics_json TEXT,                  -- {symbol: {metric: value}}
    error_message         TEXT,
    artifacts_dir         TEXT,                    -- data/run_artifacts/<run_id>/
    trades_count          INTEGER
);

CREATE INDEX IF NOT EXISTS idx_runs_created_at ON backtest_runs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_status     ON backtest_runs(status);
```

## Artifact layout

```
data/run_artifacts/<run_id>/
    equity.parquet              # columns: timestamp (str ISO), equity (float)
    equity_per_symbol.parquet   # columns: timestamp, <symbol_1>, <symbol_2>, ...
    trades.parquet              # full Trade schema with leading `symbol` column
```

## Repository methods (`kairos/dashboard/api/repo.py`) — v2 signatures

```python
def create_run(req: CreatePortfolioRunRequest) -> str: ...
def get_run(run_id: str) -> RunDetail | None: ...
def list_runs(limit: int = 100, status: str | None = None) -> list[RunSummary]: ...
def mark_running(run_id: str) -> None: ...
def mark_done(
    run_id: str,
    metrics: Mapping[str, float],
    per_symbol_metrics: Mapping[str, Mapping[str, float]],
    artifacts_dir: str,
    trades_count: int,
) -> None: ...
def mark_failed(run_id: str, error: str) -> None: ...
def load_equity(run_id: str, *, per_symbol: bool = False) -> EquityPayload: ...
def load_trades(run_id: str, offset: int, limit: int) -> tuple[list[TradeDict], int]: ...
```

`EquityPayload` = `{"timestamps": list[str], "equity": list[float]}` when per_symbol=False,
else `{"timestamps": list[str], "per_symbol": dict[str, list[float]]}`.

## Old single-symbol code paths

Deleted from repo, jobs, routes, dtos. Sonnet implementing Phase 5C migration MUST
remove rather than deprecate. Tests for old shapes are deleted; new tests replace.
