// DTOs copied verbatim from docs/phase5/API_V2.md (portfolio mode).

export type IntervalName = "1m" | "15m";
export type EngineName = "vectorbt" | "backtrader";
export type RunStatus = "pending" | "running" | "done" | "failed";
export type ExitReason =
  | "signal"
  | "stop"
  | "target"
  | "eod_flatten"
  | "risk_halt";
export type ParamFieldType = "int" | "float" | "bool" | "string" | "enum";

export interface RunSummary {
  id: string;
  symbols: string[];
  strategies: Record<string, string>;
  weights: Record<string, number>;
  interval: string;
  start: string;
  end: string;
  engine: EngineName;
  status: RunStatus;
  created_at: string;
  // Populated only when status === "done".
  cagr?: number;
  mdd?: number;
  sharpe?: number;
  total_trades?: number;
}

export interface RunDetail extends RunSummary {
  strategy_params: Record<string, Record<string, unknown>>;
  initial_cash: number;
  apply_risk_simulator: boolean;
  metrics: Record<string, number> | null;
  per_symbol_metrics: Record<string, Record<string, number>> | null;
  error?: string;
}

export interface Trade {
  symbol: string;
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
  exit_reason: ExitReason;
}

export interface CompareReport {
  a: RunSummary;
  b: RunSummary;
  metric_diff: Record<string, { a: number; b: number; rel_diff: number }>;
  equity_diff: { timestamps: string[]; a: number[]; b: number[] };
}

export interface GateReportSummary {
  // Filename stem without extension.
  name: string;
  // Portfolio reports expose a list of symbols; older single-symbol reports
  // expose one `symbol` string. Both are optional so pages can render either.
  symbols?: string[];
  symbol?: string;
  interval: string;
  start: string;
  end: string;
  overall_pass: boolean;
}

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

export interface EquityResponsePortfolio {
  timestamps: string[];
  equity: number[];
}

export interface EquityResponsePerSymbol {
  timestamps: string[];
  per_symbol: Record<string, number[]>;
}

export interface GateReportsResponse {
  reports: GateReportSummary[];
}

export interface ParamField {
  type: ParamFieldType;
  default: unknown;
  min?: number;
  max?: number;
  choices?: string[];
  help: string;
}

export interface StrategyEntry {
  name: string;
  label: string;
  default_params: Record<string, unknown>;
  param_schema: Record<string, ParamField>;
  supports_funding: boolean;
  supports_metrics: boolean;
  description: string;
}

export interface StrategiesResponse {
  strategies: StrategyEntry[];
}

export interface CreateRunRequest {
  strategies: Record<string, { name: string; params: Record<string, unknown> }>;
  weights: Record<string, number>;
  interval: IntervalName;
  start: string;
  end: string;
  engine: EngineName;
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
