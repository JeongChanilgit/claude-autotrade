# Phase 4 — REST + WebSocket API Contract

**FROZEN** — all Sonnet-4* work conforms to these shapes verbatim.

## REST Endpoints (prefix `/api`)

All responses JSON. Timestamps are ISO-8601 UTC strings (`2024-03-15T00:00:00Z`).
Monetary values are float (quote currency); percentages are decimals (0.35 = 35%).

### `GET /api/health`
Existing. Response: `{"status": "ok", "version": "<version>"}`.

### `GET /api/runs`
Query params: `limit` (int, default 100), `status` (optional: `pending|running|done|failed`).
Response: `{"runs": [RunSummary, ...]}`.

### `POST /api/runs`
Create a new backtest run.
Request body:
```json
{
  "strategy": "orb" | "funding_counter" | "vol_compression" | "ensemble",
  "symbol": "BTCUSDT",
  "interval": "1m" | "15m",
  "start": "2023-01-01T00:00:00Z",
  "end": "2023-06-01T00:00:00Z",
  "engine": "vectorbt" | "backtrader",
  "params": { "...": "..." },
  "initial_cash": 10000,
  "apply_risk_simulator": true
}
```
Response 202: `{"id": "run_<uuid>", "status": "pending"}`.
The server enqueues a background job and returns immediately. Clients subscribe
to progress via WebSocket `/ws/backtest/{id}`.

### `GET /api/runs/{id}`
Response: `RunDetail`.

### `GET /api/runs/{id}/trades`
Query params: `offset` (default 0), `limit` (default 1000).
Response: `{"trades": [Trade, ...], "total": int}`.

### `GET /api/runs/{id}/equity`
Response: `{"timestamps": [iso, ...], "equity": [float, ...]}`.
Down-sampled to ≤ 5000 points via LTTB.

### `POST /api/compare`
Request: `{"run_ids": ["run_a", "run_b"]}`.
Response: `CompareReport` — side-by-side metrics + pnl diff time series.

### `GET /api/gate-reports`
Response: `{"reports": [GateReportSummary, ...]}` — lists `reports/phase3_gate_*.json`.

### `GET /api/gate-reports/{name}`
Response: raw JSON content of `reports/{name}.json` (one of the output files
from `kairos.backtest.gate_runner`).

## WebSocket `/ws/backtest/{id}`

Messages server → client, each a single JSON object:
```json
{"type": "progress", "pct": 42.5, "stage": "engine_run", "log": "bar 5000/12000"}
{"type": "stage",    "stage": "risk_halts", "pct": 85.0}
{"type": "done",     "metrics": {...}}
{"type": "error",    "message": "..."}
```
Server closes socket after `done` or `error`.

## DTOs

```ts
interface RunSummary {
  id: string;
  strategy: string;
  symbol: string;
  interval: string;
  start: string;
  end: string;
  engine: "vectorbt" | "backtrader";
  status: "pending" | "running" | "done" | "failed";
  created_at: string;
  // present only when status === "done"
  cagr?: number;
  mdd?: number;
  sharpe?: number;
  total_trades?: number;
}

interface RunDetail extends RunSummary {
  params: Record<string, unknown>;
  initial_cash: number;
  apply_risk_simulator: boolean;
  metrics: Record<string, number> | null;  // null while pending/running
  error?: string;                           // present iff status==="failed"
}

interface Trade {
  entry_time: string;
  exit_time: string;
  side: 1 | -1;
  qty: number;
  entry_price: number;
  exit_price: number;
  gross_pnl: number;
  fees: number;
  slippage: number;
  funding_cost: number;
  net_pnl: number;
  bars_held: number;
  exit_reason: "signal" | "stop" | "target" | "eod_flatten" | "risk_halt";
}

interface CompareReport {
  a: RunSummary;
  b: RunSummary;
  metric_diff: Record<string, { a: number; b: number; rel_diff: number }>;
  equity_diff: { timestamps: string[]; a: number[]; b: number[] };
}

interface GateReportSummary {
  name: string;            // filename stem, no extension
  symbol: string;
  interval: string;
  start: string;
  end: string;
  overall_pass: boolean;
}
```

## Error format
All 4xx/5xx responses:
```json
{"error": {"code": "RUN_NOT_FOUND", "message": "..."}}
```
Codes: `RUN_NOT_FOUND`, `VALIDATION_ERROR`, `DATA_UNAVAILABLE`,
`ENGINE_ERROR`, `INTERNAL_ERROR`.
