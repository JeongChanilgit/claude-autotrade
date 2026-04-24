import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import DrawdownChart from "../components/DrawdownChart";
import EquityChart from "../components/EquityChart";
import MetricCard from "../components/MetricCard";
import ProgressBar from "../components/ProgressBar";
import * as api from "../api";
import { useAppStore } from "../store";
import type { EquityResponse, RunSummary } from "../types";
import { useBacktestProgress } from "../ws";

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

type FormatKind = "pct" | "num3" | "int";

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

function siblingOf(runs: RunSummary[], me: RunSummary): RunSummary | null {
  const other = me.engine === "vectorbt" ? "backtrader" : "vectorbt";
  return (
    runs.find(
      (r) =>
        r.id !== me.id &&
        r.engine === other &&
        r.status === "done" &&
        r.strategy === me.strategy &&
        r.symbol === me.symbol &&
        r.interval === me.interval &&
        r.start === me.start &&
        r.end === me.end,
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

  const [equity, setEquity] = useState<EquityResponse | null>(null);
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
    api
      .getRunEquity(id)
      .then((res) => {
        if (!cancelled) setEquity(res);
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
              Metrics
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

          {sibling && <SiblingCompare me={run} other={sibling} />}

          <section className="flex flex-col gap-3">
            <h2 className="text-sm uppercase tracking-wider text-slate-400">
              Equity Curve
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

function Header({ run }: { run: RunSummary }) {
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-3">
        <h1 className="text-2xl font-semibold tracking-tight">
          {run.strategy}
        </h1>
        <StatusPill status={run.status} />
      </div>
      <div className="text-sm text-slate-400">
        <span className="font-mono">{run.symbol}</span>
        <span className="mx-2 text-slate-600">·</span>
        <span>{run.interval}</span>
        <span className="mx-2 text-slate-600">·</span>
        <span>
          {run.start} – {run.end}
        </span>
        <span className="mx-2 text-slate-600">·</span>
        <span>engine: {run.engine}</span>
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
