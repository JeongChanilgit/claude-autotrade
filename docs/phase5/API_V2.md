# Phase 5 — API v2 (Portfolio mode, FROZEN)

All single-symbol fields are removed. Every run is a portfolio.

## Modified endpoints

### `POST /api/runs` — request body (NEW shape)
```json
{
  "strategies": {
    "BTCUSDT": {"name": "orb", "params": {"atr_period": 14}},
    "ETHUSDT": {"name": "orb", "params": {"atr_period": 14}},
    "SOLUSDT": {"name": "vol_compression", "params": {}}
  },
  "weights": {"BTCUSDT": 0.5, "ETHUSDT": 0.3, "SOLUSDT": 0.2},
  "interval": "15m",
  "start": "2023-01-01T00:00:00Z",
  "end": "2024-08-01T00:00:00Z",
  "engine": "vectorbt",
  "initial_cash": 10000,
  "apply_risk_simulator": true
}
```

Validation:
- `set(strategies.keys()) == set(weights.keys())` (else 422 VALIDATION_ERROR)
- `abs(sum(weights.values()) - 1.0) < 1e-9` (else 422)
- Each `strategies[symbol].name` must exist in registry (else 422)
- ≥ 1 symbol required

Response 202 unchanged: `{"id": "run_<uuid>", "status": "pending"}`.

### New endpoint: `GET /api/strategies`
Returns the registry snapshot. See STRATEGY_REGISTRY.md.

## Modified DTOs

```ts
interface RunSummary {
  id: string;
  symbols: string[];                                  // sorted
  strategies: Record<string, string>;                 // symbol -> strategy name
  weights: Record<string, number>;
  interval: string;
  start: string;
  end: string;
  engine: "vectorbt" | "backtrader";
  status: "pending" | "running" | "done" | "failed";
  created_at: string;
  cagr?: number;
  mdd?: number;
  sharpe?: number;
  total_trades?: number;
}

interface RunDetail extends RunSummary {
  strategy_params: Record<string, Record<string, unknown>>;
  initial_cash: number;
  apply_risk_simulator: boolean;
  metrics: Record<string, number> | null;
  per_symbol_metrics: Record<string, Record<string, number>> | null;
  error?: string;
}

interface Trade {
  symbol: string;                                      // NEW leading column
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

interface StrategyEntry {
  name: string;
  label: string;
  default_params: Record<string, unknown>;
  param_schema: Record<string, ParamField>;
  supports_funding: boolean;
  supports_metrics: boolean;
  description: string;
}

interface ParamField {
  type: "int" | "float" | "bool" | "string" | "enum";
  default: unknown;
  min?: number;
  max?: number;
  choices?: string[];
  help: string;
}
```

`/api/runs/{id}/equity` response unchanged shape (timestamps + equity arrays = portfolio total).
New: `/api/runs/{id}/equity?per_symbol=true` returns `{"timestamps":[...], "per_symbol":{"BTCUSDT":[...], ...}}` for stacked area chart.

`/api/runs/{id}/trades` unchanged endpoint, but Trade rows now include `symbol`.

`/api/compare` unchanged shape; metric_diff still per metric key.

## Error codes
Unchanged: `RUN_NOT_FOUND`, `VALIDATION_ERROR`, `DATA_UNAVAILABLE`, `ENGINE_ERROR`, `INTERNAL_ERROR`.
New: `STRATEGY_NOT_FOUND` (when API request references unknown strategy name).
