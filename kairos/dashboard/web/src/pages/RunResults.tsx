import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import DrawdownChart from "../components/DrawdownChart";
import EquityChart from "../components/EquityChart";
import MetricCard from "../components/MetricCard";
import ProgressBar from "../components/ProgressBar";
import * as api from "../api";
import { useAppStore } from "../store";
import type {
  EquityResponsePerSymbol,
  EquityResponsePortfolio,
  RunDetail,
  RunSummary,
} from "../types";
import { useBacktestProgress } from "../ws";

type FormatKind = "pct" | "num3" | "int";

// Metric keys whose presence we surface as cards. Missing keys render as em-dash.
const METRIC_KEYS: Array<{ key: string; label: string; kind: FormatKind }> = [
  { key: "cagr", label: "CAGR", kind: "pct" },
  { key: "mdd", label: "MDD", kind: "pct" },
  { key: "sharpe", label: "Sharpe", kind: "num3" },
  { key: "calmar", label: "Calmar", kind: "num3" },
  { key: "profit_factor", label: "Profit Factor", kind: "num3" },
  { key: "win_rate", label: "Win %", kind: "pct" },
  { key: "total_trades", label: "Total Trades", kind: "int" },
];

// 10-color palette for per-symbol stacked area. Tailwind-derived hex codes.
const SYMBOL_COLORS: string[] = [
  "#10b981", // emerald-500
  "#0ea5e9", // sky-500
  "#f59e0b", // amber-500
  "#f43f5e", // rose-500
  "#8b5cf6", // violet-500
  "#84cc16", // lime-500
  "#f97316", // orange-500
  "#6366f1", // indigo-500
  "#d946ef", // fuchsia-500
  "#14b8a6", // teal-500
];

function formatMetric(
  metrics: Record<string, number> | null,
  key: string,
  kind: FormatKind,
): string {
  if (!metrics) return "—";
  const v = metrics[key];
  if (v == null || Number.isNaN(v)) return "—";
  if (kind === "pct") return `${(v * 100).toFixed(2)}%`;
  if (kind === "int") return String(Math.round(v));
  return v.toFixed(3);
}

function formatCellMetric(v: number | undefined, kind: FormatKind): string {
  if (v == null || Number.isNaN(v)) return "—";
  if (kind === "pct") return `${(v * 100).toFixed(2)}%`;
  if (kind === "int") return String(Math.round(v));
  return v.toFixed(3);
}

function arraysEqualUnordered(a: string[], b: string[]): boolean {
  if (a.length !== b.length) return false;
  const sa = [...a].sort();
  const sb = [...b].sort();
  for (let i = 0; i < sa.length; i += 1) {
    if (sa[i] !== sb[i]) return false;
  }
  return true;
}

function recordsEqual<V>(
  a: Record<string, V>,
  b: Record<string, V>,
): boolean {
  const ka = Object.keys(a).sort();
  const kb = Object.keys(b).sort();
  if (ka.length !== kb.length) return false;
  for (let i = 0; i < ka.length; i += 1) {
    if (ka[i] !== kb[i]) return false;
    if (a[ka[i]] !== b[kb[i]]) return false;
  }
  return true;
}

function siblingOf(runs: RunSummary[], me: RunSummary): RunSummary | null {
  const other = me.engine === "vectorbt" ? "backtrader" : "vectorbt";
  return (
    runs.find(
      (r) =>
        r.id !== me.id &&
        r.engine === other &&
        r.status === "done" &&
        r.interval === me.interval &&
        r.start === me.start &&
        r.end === me.end &&
        arraysEqualUnordered(r.symbols, me.symbols) &&
        recordsEqual(r.strategies, me.strategies) &&
        recordsEqual(r.weights, me.weights),
    ) ?? null
  );
}

function relDiff(a: number | undefined, b: number | undefined): number | null {
  if (a == null || b == null || !Number.isFinite(a) || !Number.isFinite(b)) {
    return null;
  }
  const denom = Math.max(Math.abs(a), Math.abs(b), 1e-9);
  return Math.abs(a - b) / denom;
}

export default function RunResults() {
  const { id = "" } = useParams();
  const run = useAppStore((s) => s.currentRun);
  const runs = useAppStore((s) => s.runs);
  const runsLoaded = useAppStore((s) => s.runsLoaded);
  const loadRun = useAppStore((s) => s.loadRun);
  const refreshRuns = useAppStore((s) => s.refreshRuns);

  const [equity, setEquity] = useState<EquityResponsePortfolio | null>(null);
  const [equityPerSymbol, setEquityPerSymbol] =
    useState<EquityResponsePerSymbol | null>(null);
  const [equityError, setEquityError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    void loadRun(id);
  }, [id, loadRun]);

  // Load the full run list once so the A/B sibling lookup has candidates.
  useEffect(() => {
    if (!runsLoaded) void refreshRuns();
  }, [runsLoaded, refreshRuns]);

  // Only fetch equity once the run is known to be finished; avoids 404 storms
  // while the backtest is still pending/running.
  useEffect(() => {
    if (!id) return;
    if (run?.status !== "done") return;
    let cancelled = false;
    Promise.all([api.getRunEquity(id), api.getRunEquityPerSymbol(id)])
      .then(([total, perSym]) => {
        if (cancelled) return;
        setEquity(total);
        setEquityPerSymbol(perSym);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setEquityError(err instanceof Error ? err.message : "load failed");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [id, run?.status]);

  const sibling = useMemo(() => {
    if (!run || run.status !== "done") return null;
    return siblingOf(runs, run);
  }, [run, runs]);

  if (!id) {
    return <div className="text-red-400">Missing run id.</div>;
  }
  if (!run || run.id !== id) {
    return <div className="text-slate-500 text-sm">Loading run…</div>;
  }

  return (
    <div className="flex flex-col gap-6">
      <Header run={run} />

      {(run.status === "pending" || run.status === "running") && (
        <RunProgress runId={id} />
      )}

      {run.status === "failed" && (
        <div className="rounded-lg border border-red-800 bg-red-950/40 p-4 text-red-300">
          <div className="text-xs uppercase tracking-wide text-red-400 mb-1">
            Error
          </div>
          <div className="text-sm whitespace-pre-wrap">
            {run.error ?? "Run failed."}
          </div>
        </div>
      )}

      {run.status === "done" && (
        <>
          <section className="flex flex-col gap-3">
            <h2 className="text-sm uppercase tracking-wider text-slate-400">
              Portfolio metrics
            </h2>
            <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-3">
              {METRIC_KEYS.map((m) => (
                <MetricCard
                  key={m.key}
                  label={m.label}
                  value={formatMetric(run.metrics, m.key, m.kind)}
                />
              ))}
            </div>
          </section>

          {run.per_symbol_metrics && (
            <PerSymbolMetrics metrics={run.per_symbol_metrics} />
          )}

          {sibling && <SiblingCompare me={run} other={sibling} />}

          <section className="flex flex-col gap-3">
            <h2 className="text-sm uppercase tracking-wider text-slate-400">
              Portfolio equity curve
            </h2>
            {equityError && (
              <div className="text-sm text-red-400">{equityError}</div>
            )}
            {!equity && !equityError && (
              <div className="text-slate-500 text-sm">Loading equity…</div>
            )}
            {equity && (
              <EquityChart
                timestamps={equity.timestamps}
                equity={equity.equity}
              />
            )}
          </section>

          {equityPerSymbol && (
            <section className="flex flex-col gap-3">
              <h2 className="text-sm uppercase tracking-wider text-slate-400">
                Per-symbol equity (stacked)
              </h2>
              <PerSymbolEquityChart data={equityPerSymbol} />
            </section>
          )}

          {equity && (
            <section className="flex flex-col gap-3">
              <h2 className="text-sm uppercase tracking-wider text-slate-400">
                Drawdown
              </h2>
              <DrawdownChart
                timestamps={equity.timestamps}
                equity={equity.equity}
              />
            </section>
          )}

          <div>
            <Link
              to={`/trades/${encodeURIComponent(run.id)}`}
              className="inline-block px-4 py-2 rounded border border-slate-700 hover:bg-slate-800 text-slate-200 text-sm font-medium"
            >
              View trades
            </Link>
          </div>
        </>
      )}
    </div>
  );
}

function Header({ run }: { run: RunDetail }) {
  const symbolRows = run.symbols.map((sym) => ({
    symbol: sym,
    strategy: run.strategies[sym] ?? "—",
    weight: run.weights[sym],
  }));
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-3">
        <h1 className="text-2xl font-semibold tracking-tight">Portfolio run</h1>
        <StatusPill status={run.status} />
      </div>
      <div className="text-sm text-slate-400">
        <span className="font-mono">{run.symbols.join(", ")}</span>
        <span className="mx-2 text-slate-600">·</span>
        <span>{run.interval}</span>
        <span className="mx-2 text-slate-600">·</span>
        <span>
          {run.start} – {run.end}
        </span>
        <span className="mx-2 text-slate-600">·</span>
        <span>engine: {run.engine}</span>
      </div>
      <div className="border border-slate-800 rounded bg-slate-900 inline-block w-fit">
        <table className="text-sm font-mono">
          <tbody>
            {symbolRows.map((r) => (
              <tr key={r.symbol} className="border-b border-slate-800 last:border-0">
                <td className="px-3 py-1.5 text-slate-200">{r.symbol}</td>
                <td className="px-3 py-1.5 text-slate-300">{r.strategy}</td>
                <td className="px-3 py-1.5 text-slate-400">
                  weight {r.weight != null ? r.weight.toFixed(2) : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function StatusPill({ status }: { status: RunSummary["status"] }) {
  const cls =
    status === "done"
      ? "bg-emerald-900 text-emerald-300 border-emerald-700"
      : status === "failed"
        ? "bg-red-950 text-red-300 border-red-800"
        : "bg-slate-800 text-slate-300 border-slate-700";
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-semibold border ${cls}`}
    >
      {status}
    </span>
  );
}

function RunProgress({ runId }: { runId: string }) {
  const p = useBacktestProgress(runId);
  const lastLog = p.log.length > 0 ? p.log[p.log.length - 1] : null;
  return (
    <div className="flex flex-col gap-2">
      <ProgressBar pct={p.pct} stage={p.stage} lastLog={lastLog} />
      {p.error && <div className="text-sm text-red-400">{p.error}</div>}
    </div>
  );
}

const PER_SYMBOL_COLUMNS: Array<{
  key: string;
  label: string;
  kind: FormatKind;
}> = [
  { key: "cagr", label: "CAGR", kind: "pct" },
  { key: "mdd", label: "MDD", kind: "pct" },
  { key: "sharpe", label: "Sharpe", kind: "num3" },
  { key: "total_trades", label: "Trades", kind: "int" },
];

function PerSymbolMetrics({
  metrics,
}: {
  metrics: Record<string, Record<string, number>>;
}) {
  const symbols = Object.keys(metrics).sort();
  return (
    <section className="flex flex-col gap-3">
      <h2 className="text-sm uppercase tracking-wider text-slate-400">
        Per-symbol metrics
      </h2>
      <div className="border border-slate-800 rounded bg-slate-900 overflow-hidden">
        <table className="w-full text-sm font-mono">
          <thead className="text-xs text-slate-400 bg-slate-950">
            <tr>
              <th className="text-left px-4 py-2">Symbol</th>
              {PER_SYMBOL_COLUMNS.map((c) => (
                <th key={c.key} className="text-right px-4 py-2">
                  {c.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {symbols.map((sym) => {
              const row = metrics[sym] ?? {};
              return (
                <tr key={sym} className="border-t border-slate-800">
                  <td className="px-4 py-1.5 text-slate-200">{sym}</td>
                  {PER_SYMBOL_COLUMNS.map((c) => (
                    <td
                      key={c.key}
                      className="px-4 py-1.5 text-right text-slate-300"
                    >
                      {formatCellMetric(row[c.key], c.kind)}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function PerSymbolEquityChart({ data }: { data: EquityResponsePerSymbol }) {
  const symbols = Object.keys(data.per_symbol).sort();
  const rows = data.timestamps.map((t, i) => {
    const entry: Record<string, string | number> = { t };
    for (const sym of symbols) {
      const series = data.per_symbol[sym] ?? [];
      entry[sym] = series[i] ?? 0;
    }
    return entry;
  });
  return (
    <div style={{ width: "100%", height: 320 }}>
      <ResponsiveContainer>
        <AreaChart
          data={rows}
          margin={{ top: 8, right: 16, bottom: 8, left: 8 }}
        >
          <CartesianGrid stroke="#1e293b" strokeDasharray="3 3" />
          <XAxis
            dataKey="t"
            stroke="#64748b"
            tick={{ fontSize: 11 }}
            minTickGap={40}
          />
          <YAxis stroke="#64748b" tick={{ fontSize: 11 }} />
          <Tooltip
            contentStyle={{
              background: "#0f172a",
              border: "1px solid #1e293b",
              color: "#e2e8f0",
            }}
          />
          <Legend />
          {symbols.map((sym, i) => {
            const color = SYMBOL_COLORS[i % SYMBOL_COLORS.length];
            return (
              <Area
                key={sym}
                type="monotone"
                dataKey={sym}
                name={sym}
                stackId="1"
                stroke={color}
                fill={color}
                fillOpacity={0.55}
              />
            );
          })}
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

function SiblingCompare({
  me,
  other,
}: {
  me: RunSummary;
  other: RunSummary;
}) {
  const rows: Array<{ label: string; key: "cagr" | "mdd" | "sharpe" }> = [
    { label: "CAGR", key: "cagr" },
    { label: "MDD", key: "mdd" },
    { label: "Sharpe", key: "sharpe" },
  ];
  return (
    <section className="flex flex-col gap-3">
      <h2 className="text-sm uppercase tracking-wider text-slate-400">
        Engine A/B — this ({me.engine}) vs{" "}
        <Link
          to={`/run/${encodeURIComponent(other.id)}`}
          className="text-emerald-400 hover:underline"
        >
          sibling ({other.engine})
        </Link>
      </h2>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        {rows.map((r) => {
          const rd = relDiff(me[r.key], other[r.key]);
          const tone: "pos" | "neg" | "neutral" =
            rd == null ? "neutral" : rd < 0.01 ? "pos" : "neg";
          const delta =
            rd == null ? "n/a" : `rel-diff ${(rd * 100).toFixed(2)}%`;
          const mine = me[r.key];
          return (
            <MetricCard
              key={r.key}
              label={r.label}
              value={mine == null ? "—" : mine.toFixed(3)}
              delta={delta}
              deltaTone={tone}
            />
          );
        })}
      </div>
    </section>
  );
}
