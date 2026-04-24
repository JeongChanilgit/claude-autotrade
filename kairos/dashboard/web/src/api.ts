import type {
  ApiErrorBody,
  CompareReport,
  CreateRunRequest,
  CreateRunResponse,
  EquityResponsePerSymbol,
  EquityResponsePortfolio,
  GateReportsResponse,
  HealthResponse,
  RunDetail,
  RunsListResponse,
  StrategiesResponse,
  TradesResponse,
} from "./types";

const BASE: string = import.meta.env.VITE_API_BASE ?? "/api";

export class ApiError extends Error {
  code: string;
  status: number;
  constructor(code: string, message: string, status: number) {
    super(message);
    this.code = code;
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  if (!res.ok) {
    // Parse structured error envelope; fall back to status text on malformed bodies.
    let code = "INTERNAL_ERROR";
    let message = res.statusText || `HTTP ${res.status}`;
    try {
      const body = (await res.json()) as ApiErrorBody;
      if (body?.error) {
        code = body.error.code ?? code;
        message = body.error.message ?? message;
      }
    } catch {
      // ignore parse failure
    }
    throw new ApiError(code, message, res.status);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export function getHealth(): Promise<HealthResponse> {
  return request<HealthResponse>("/health");
}

export function listRuns(params?: {
  limit?: number;
  status?: "pending" | "running" | "done" | "failed";
}): Promise<RunsListResponse> {
  const q = new URLSearchParams();
  if (params?.limit != null) q.set("limit", String(params.limit));
  if (params?.status) q.set("status", params.status);
  const suffix = q.toString() ? `?${q.toString()}` : "";
  return request<RunsListResponse>(`/runs${suffix}`);
}

export function createRun(body: CreateRunRequest): Promise<CreateRunResponse> {
  return request<CreateRunResponse>("/runs", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function getRun(id: string): Promise<RunDetail> {
  return request<RunDetail>(`/runs/${encodeURIComponent(id)}`);
}

export function getRunTrades(
  id: string,
  params?: { offset?: number; limit?: number },
): Promise<TradesResponse> {
  const q = new URLSearchParams();
  if (params?.offset != null) q.set("offset", String(params.offset));
  if (params?.limit != null) q.set("limit", String(params.limit));
  const suffix = q.toString() ? `?${q.toString()}` : "";
  return request<TradesResponse>(
    `/runs/${encodeURIComponent(id)}/trades${suffix}`,
  );
}

export function getRunEquity(id: string): Promise<EquityResponsePortfolio> {
  return request<EquityResponsePortfolio>(
    `/runs/${encodeURIComponent(id)}/equity`,
  );
}

export function getRunEquityPerSymbol(
  id: string,
): Promise<EquityResponsePerSymbol> {
  return request<EquityResponsePerSymbol>(
    `/runs/${encodeURIComponent(id)}/equity?per_symbol=true`,
  );
}

export function compareRuns(runIds: [string, string]): Promise<CompareReport> {
  return request<CompareReport>("/compare", {
    method: "POST",
    body: JSON.stringify({ run_ids: runIds }),
  });
}

export function listStrategies(): Promise<StrategiesResponse> {
  return request<StrategiesResponse>("/strategies");
}

export function listGateReports(): Promise<GateReportsResponse> {
  return request<GateReportsResponse>("/gate-reports");
}

export function getGateReport(name: string): Promise<Record<string, unknown>> {
  return request<Record<string, unknown>>(
    `/gate-reports/${encodeURIComponent(name)}`,
  );
}

export const API_BASE = BASE;
