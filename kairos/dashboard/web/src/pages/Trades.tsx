import { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { getRunTrades } from "../api";
import TradesTable from "../components/TradesTable";
import type { Trade } from "../types";

type ExitReasonFilter = Trade["exit_reason"] | "all";

const EXIT_REASONS: ExitReasonFilter[] = [
  "all",
  "signal",
  "stop",
  "target",
  "eod_flatten",
  "risk_halt",
];

const PAGE_LIMIT = 5000;

export default function Trades() {
  const { id } = useParams<{ id: string }>();
  const [trades, setTrades] = useState<Trade[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<ExitReasonFilter>("all");

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    getRunTrades(id, { offset: 0, limit: PAGE_LIMIT })
      .then((res) => {
        if (cancelled) return;
        setTrades(res.trades);
        setTotal(res.total);
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
  }, [id]);

  const filtered = useMemo(
    () =>
      filter === "all" ? trades : trades.filter((t) => t.exit_reason === filter),
    [trades, filter],
  );

  const stats = useMemo(() => {
    const wins = filtered.filter((t) => t.net_pnl > 0);
    const losses = filtered.filter((t) => t.net_pnl < 0);
    const totalPnl = filtered.reduce((acc, t) => acc + t.net_pnl, 0);
    const winRate = filtered.length ? wins.length / filtered.length : 0;
    const avgWin = wins.length
      ? wins.reduce((a, t) => a + t.net_pnl, 0) / wins.length
      : 0;
    const avgLoss = losses.length
      ? losses.reduce((a, t) => a + t.net_pnl, 0) / losses.length
      : 0;
    return { totalPnl, winRate, avgWin, avgLoss };
  }, [filtered]);

  if (!id) return <div className="text-red-400">Missing run id.</div>;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Trades · {id.slice(0, 8)}</h1>
        <label className="flex items-center gap-2 text-sm">
          <span className="text-slate-400 text-xs">Exit reason</span>
          <select
            className="bg-slate-900 border border-slate-700 rounded px-2 py-1 text-slate-100"
            value={filter}
            onChange={(e) => setFilter(e.target.value as ExitReasonFilter)}
          >
            {EXIT_REASONS.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        </label>
      </div>

      {total > PAGE_LIMIT ? (
        <div className="text-xs text-amber-400">
          Showing {PAGE_LIMIT} of {total} trades.
        </div>
      ) : null}

      <div className="grid grid-cols-4 gap-3">
        <Stat label="Total PnL" value={stats.totalPnl} tone />
        <Stat
          label="Win rate"
          value={stats.winRate}
          format={(v) => `${(v * 100).toFixed(1)}%`}
        />
        <Stat label="Avg win" value={stats.avgWin} tone />
        <Stat label="Avg loss" value={stats.avgLoss} tone />
      </div>

      {loading ? (
        <div className="text-slate-400 text-sm">Loading trades…</div>
      ) : null}
      {error ? <div className="text-red-400 text-sm">{error}</div> : null}

      {!loading && !error ? <TradesTable rows={filtered} /> : null}
    </div>
  );
}

interface StatProps {
  label: string;
  value: number;
  tone?: boolean;
  format?: (v: number) => string;
}

function Stat({ label, value, tone, format }: StatProps) {
  const text = format
    ? format(value)
    : value.toLocaleString(undefined, {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      });
  const cls = tone
    ? value > 0
      ? "text-emerald-400"
      : value < 0
        ? "text-red-400"
        : "text-slate-200"
    : "text-slate-200";
  return (
    <div className="border border-slate-800 rounded bg-slate-900 p-3">
      <div className="text-xs text-slate-400">{label}</div>
      <div className={`text-lg font-mono ${cls}`}>{text}</div>
    </div>
  );
}
