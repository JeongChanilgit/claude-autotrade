// DTOs copied verbatim from docs/phase4/API.md

export interface RunSummary {
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

export interface RunDetail extends RunSummary {
  params: Record<string, unknown>;
  initial_cash: number;
  apply_risk_simulator: boolean;
  metrics: Record<string, number> | null; // null while pending/running
  error?: string; // present iff status==="failed"
}

export interface Trade {
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

export interface CompareReport {
  a: RunSummary;
  b: RunSummary;
  metric_diff: Record<string, { a: number; b: number; rel_diff: number }>;
  equity_diff: { timestamps: string[]; a: number[]; b: number[] };
}

export interface GateReportSummary {
  name: string; // filename stem, no extension
  symbol: string;
  interval: string;
  start: string;
  end: string;
  overall_pass: boolean;
}

// Non-DTO auxiliary types used by api/ws clients.

export interface ApiErrorBody {
  error: { code: string; message: string };
}

export interface HealthResponse {
  status: string;
  version: string;
}

export interface RunsListResponse {
  runs: RunSummary[];
}

export interface TradesResponse {
  trades: Trade[];
  total: number;
}

export interface EquityResponse {
  timestamps: string[];
  equity: number[];
}

export interface GateReportsResponse {
  reports: GateReportSummary[];
}

export interface CreateRunRequest {
  strategy: "orb" | "funding_counter" | "vol_compression" | "ensemble";
  symbol: string;
  interval: "1m" | "15m";
  start: string;
  end: string;
  engine: "vectorbt" | "backtrader";
  params: Record<string, unknown>;
  initial_cash: number;
  apply_risk_simulator: boolean;
}

export interface CreateRunResponse {
  id: string;
  status: "pending";
}

export type WsMessage =
  | { type: "progress"; pct: number; stage: string; log?: string }
  | { type: "stage"; stage: string; pct: number }
  | { type: "done"; metrics: Record<string, number> }
  | { type: "error"; message: string };
