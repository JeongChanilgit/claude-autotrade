import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import MetricCard from "../components/MetricCard";
import PassFailBadge from "../components/PassFailBadge";
import RegimeTable from "../components/RegimeTable";
import type { RegimeRow } from "../components/RegimeTable";
import SharpeBarChart from "../components/SharpeBarChart";
import type { SharpeBarItem } from "../components/SharpeBarChart";
import StressTable from "../components/StressTable";
import type { StressRow } from "../components/StressTable";
import * as api from "../api";

// Mirrors the JSON shape emitted by kairos/backtest/gate_runner.py. Kept local
// because api.ts returns Record<string, unknown> for forward compatibility.
interface GateReportPayload {
  symbol: string;
  interval: string;
  window: { start: string; end: string };
  bars: number;
  strategy_sharpes: SharpeBarItem[];
  ensemble: { cagr: number; mdd: number; total_trades: number; sharpe?: number };
  regimes: RegimeRow[];
  stress: StressRow[];
  gate: { overall_pass: boolean };
  summary: string[];
}

function fmtPct(x: number | undefined): string {
  if (x == null || !Number.isFinite(x)) return "—";
  return `${(x * 100).toFixed(2)}%`;
}

function fmtNum(x: number | undefined, digits = 3): string {
  if (x == null || !Number.isFinite(x)) return "—";
  return x.toFixed(digits);
}

export default function GateDetail() {
  const { name = "" } = useParams();
  const [data, setData] = useState<GateReportPayload | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!name) return;
    let cancelled = false;
    api
      .getGateReport(name)
      .then((raw) => {
        if (!cancelled) setData(raw as unknown as GateReportPayload);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "load failed");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [name]);

  if (error) {
    return (
      <div className="rounded-lg border border-red-800 bg-red-950/40 p-4 text-red-300 text-sm">
        {error}
      </div>
    );
  }
  if (!data) {
    return <div className="text-slate-500 text-sm">Loading gate report…</div>;
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-1">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-semibold tracking-tight font-mono">
            {name}
          </h1>
          <PassFailBadge pass={data.gate.overall_pass} />
        </div>
        <div className="text-sm text-slate-400">
          <span className="font-mono">{data.symbol}</span>
          <span className="mx-2 text-slate-600">·</span>
          <span>{data.interval}</span>
          <span className="mx-2 text-slate-600">·</span>
          <span>
            {data.window.start} – {data.window.end}
          </span>
          <span className="mx-2 text-slate-600">·</span>
          <span>{data.bars} bars</span>
        </div>
        <div className="mt-1">
          <Link
            to="/gate-reports"
            className="text-xs text-slate-400 hover:underline"
          >
            ← All gate reports
          </Link>
        </div>
      </div>

      <section className="flex flex-col gap-3">
        <h2 className="text-sm uppercase tracking-wider text-slate-400">
          Summary
        </h2>
        <pre className="rounded-lg border border-slate-800 bg-slate-950 p-4 text-sm font-mono text-slate-200 whitespace-pre-wrap">
          {data.summary.join("\n")}
        </pre>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-sm uppercase tracking-wider text-slate-400">
          Strategy Sharpes
        </h2>
        <SharpeBarChart items={data.strategy_sharpes} />
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-sm uppercase tracking-wider text-slate-400">
          Ensemble Headline
        </h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <MetricCard label="CAGR" value={fmtPct(data.ensemble.cagr)} />
          <MetricCard label="MDD" value={fmtPct(data.ensemble.mdd)} />
          <MetricCard label="Sharpe" value={fmtNum(data.ensemble.sharpe)} />
          <MetricCard
            label="Total Trades"
            value={data.ensemble.total_trades ?? "—"}
          />
        </div>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-sm uppercase tracking-wider text-slate-400">
          Regimes
        </h2>
        <RegimeTable items={data.regimes} />
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-sm uppercase tracking-wider text-slate-400">
          Stress Windows
        </h2>
        <StressTable items={data.stress} />
      </section>
    </div>
  );
}
