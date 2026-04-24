import { Link } from "react-router-dom";
import type { RunSummary } from "../types";

interface RunsTableProps {
  runs: RunSummary[];
  emptyMessage?: string;
}

function fmt(n: number | undefined, digits = 2): string {
  return n == null || Number.isNaN(n) ? "—" : n.toFixed(digits);
}

export default function RunsTable({
  runs,
  emptyMessage = "No runs yet.",
}: RunsTableProps) {
  if (runs.length === 0) {
    return <div className="text-slate-500 text-sm">{emptyMessage}</div>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="text-slate-400 border-b border-slate-800">
          <tr>
            <th className="text-left px-3 py-2">ID</th>
            <th className="text-left px-3 py-2">Strategy</th>
            <th className="text-left px-3 py-2">Symbol</th>
            <th className="text-left px-3 py-2">Interval</th>
            <th className="text-left px-3 py-2">Status</th>
            <th className="text-right px-3 py-2">CAGR</th>
            <th className="text-right px-3 py-2">Sharpe</th>
            <th className="text-right px-3 py-2">Trades</th>
            <th className="text-right px-3 py-2"></th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => (
            <tr
              key={r.id}
              className="border-b border-slate-900 hover:bg-slate-900"
            >
              <td className="px-3 py-2 font-mono text-xs">{r.id}</td>
              <td className="px-3 py-2">{r.strategy}</td>
              <td className="px-3 py-2">{r.symbol}</td>
              <td className="px-3 py-2">{r.interval}</td>
              <td className="px-3 py-2">{r.status}</td>
              <td className="px-3 py-2 text-right">{fmt(r.cagr)}</td>
              <td className="px-3 py-2 text-right">{fmt(r.sharpe)}</td>
              <td className="px-3 py-2 text-right">
                {r.total_trades ?? "—"}
              </td>
              <td className="px-3 py-2 text-right">
                <Link
                  to={`/run/${r.id}`}
                  className="text-emerald-400 hover:underline"
                >
                  view
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
