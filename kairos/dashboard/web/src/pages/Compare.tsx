import { useEffect, useMemo, useState } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { compareRuns } from "../api";
import PassFailBadge from "../components/PassFailBadge";
import { useAppStore } from "../store";
import type { CompareReport, RunSummary } from "../types";

const TOLERANCE = 0.01;

export default function Compare() {
  const runs = useAppStore((s) => s.runs);
  const runsLoaded = useAppStore((s) => s.runsLoaded);
  const refreshRuns = useAppStore((s) => s.refreshRuns);
  const compareIds = useAppStore((s) => s.compareIds);
  const setCompareA = useAppStore((s) => s.setCompareA);
  const setCompareB = useAppStore((s) => s.setCompareB);

  const [report, setReport] = useState<CompareReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!runsLoaded) void refreshRuns();
  }, [runsLoaded, refreshRuns]);

  const [a, b] = compareIds;

  useEffect(() => {
    if (!a || !b) {
      setReport(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    compareRuns([a, b])
      .then((r) => {
        if (!cancelled) setReport(r);
      })
      .catch((e: Error) => {
        if (!cancelled) setError(e.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [a, b]);

  const allPass = useMemo(() => {
    if (!report) return false;
    return Object.values(report.metric_diff).every(
      (m) => Math.abs(m.rel_diff) < TOLERANCE,
    );
  }, [report]);

  const equityData = useMemo(() => {
    if (!report) return [];
    return report.equity_diff.timestamps.map((t, i) => ({
      t,
      a: report.equity_diff.a[i] ?? 0,
      b: report.equity_diff.b[i] ?? 0,
      diff: (report.equity_diff.b[i] ?? 0) - (report.equity_diff.a[i] ?? 0),
    }));
  }, [report]);

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Compare Runs</h1>
        {report ? (
          <PassFailBadge
            pass={allPass}
            label={`±1% engine agreement: ${allPass ? "PASS" : "FAIL"}`}
          />
        ) : null}
      </div>

      <div className="flex gap-4">
        <RunSelect
          label="Run A"
          runs={runs}
          value={a}
          onChange={setCompareA}
        />
        <RunSelect
          label="Run B"
          runs={runs}
          value={b}
          onChange={setCompareB}
        />
      </div>

      {loading ? (
        <div className="text-slate-400 text-sm">Loading comparison…</div>
      ) : null}
      {error ? <div className="text-red-400 text-sm">{error}</div> : null}

      {report ? (
        <>
          <section className="border border-slate-800 rounded bg-slate-900">
            <div className="px-4 py-2 border-b border-slate-800 text-sm font-semibold">
              Metric Diff
            </div>
            <table className="w-full text-sm font-mono">
              <thead className="text-xs text-slate-400">
                <tr>
                  <th className="text-left px-4 py-2">Metric</th>
                  <th className="text-right px-4 py-2">A</th>
                  <th className="text-right px-4 py-2">B</th>
                  <th className="text-right px-4 py-2">rel_diff</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(report.metric_diff).map(([name, m]) => {
                  const ok = Math.abs(m.rel_diff) < TOLERANCE;
                  return (
                    <tr key={name} className="border-t border-slate-800">
                      <td className="px-4 py-1.5 text-slate-200">{name}</td>
                      <td className="px-4 py-1.5 text-right text-slate-300">
                        {formatNum(m.a)}
                      </td>
                      <td className="px-4 py-1.5 text-right text-slate-300">
                        {formatNum(m.b)}
                      </td>
                      <td
                        className={`px-4 py-1.5 text-right ${ok ? "text-emerald-400" : "text-red-400"}`}
                      >
                        {(m.rel_diff * 100).toFixed(2)}%
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </section>

          <section className="border border-slate-800 rounded bg-slate-900 p-4">
            <div className="text-sm font-semibold mb-2">Equity Curves</div>
            <div style={{ width: "100%", height: 320 }}>
              <ResponsiveContainer>
                <LineChart
                  data={equityData}
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
                  <Line
                    type="monotone"
                    dataKey="a"
                    name="A"
                    stroke="#34d399"
                    dot={false}
                    strokeWidth={1.5}
                  />
                  <Line
                    type="monotone"
                    dataKey="b"
                    name="B"
                    stroke="#60a5fa"
                    dot={false}
                    strokeWidth={1.5}
                  />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </section>

          <section className="border border-slate-800 rounded bg-slate-900 p-4">
            <div className="text-sm font-semibold mb-2">PnL Diff (B − A)</div>
            <div style={{ width: "100%", height: 140 }}>
              <ResponsiveContainer>
                <AreaChart
                  data={equityData}
                  margin={{ top: 8, right: 16, bottom: 8, left: 8 }}
                >
                  <CartesianGrid stroke="#1e293b" strokeDasharray="3 3" />
                  <XAxis
                    dataKey="t"
                    stroke="#64748b"
                    tick={{ fontSize: 10 }}
                    minTickGap={40}
                  />
                  <YAxis stroke="#64748b" tick={{ fontSize: 10 }} />
                  <Tooltip
                    contentStyle={{
                      background: "#0f172a",
                      border: "1px solid #1e293b",
                      color: "#e2e8f0",
                    }}
                  />
                  <Area
                    type="monotone"
                    dataKey="diff"
                    stroke="#f59e0b"
                    fill="#f59e0b"
                    fillOpacity={0.25}
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </section>
        </>
      ) : (
        !loading && (
          <div className="text-slate-400 text-sm">
            Select two runs to compare.
          </div>
        )
      )}
    </div>
  );
}

interface RunSelectProps {
  label: string;
  runs: RunSummary[];
  value: string | null;
  onChange: (id: string | null) => void;
}

function RunSelect({ label, runs, value, onChange }: RunSelectProps) {
  return (
    <label className="flex flex-col gap-1 text-sm flex-1">
      <span className="text-slate-400 text-xs">{label}</span>
      <select
        className="bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-slate-100"
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value || null)}
      >
        <option value="">— select run —</option>
        {runs.map((r) => {
          const symbols = r.symbols.join(",");
          const strategies = Array.from(
            new Set(Object.values(r.strategies)),
          ).join(",");
          return (
            <option key={r.id} value={r.id}>
              {r.id.slice(0, 8)} · {strategies} · {symbols} · {r.engine}
            </option>
          );
        })}
      </select>
    </label>
  );
}

function formatNum(n: number): string {
  if (!Number.isFinite(n)) return String(n);
  return n.toLocaleString(undefined, {
    minimumFractionDigits: 4,
    maximumFractionDigits: 4,
  });
}
