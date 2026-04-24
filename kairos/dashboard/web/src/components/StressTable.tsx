import PassFailBadge from "./PassFailBadge";

export interface StressRow {
  label: string;
  worst_daily_return: number;
  raw_halts: unknown[];
  daily_limit_respected: boolean;
}

interface StressTableProps {
  items: StressRow[];
}

function fmtPct(x: number): string {
  return `${(x * 100).toFixed(3)}%`;
}

export default function StressTable({ items }: StressTableProps) {
  if (items.length === 0) {
    return <div className="text-slate-500 text-sm">No stress windows.</div>;
  }
  return (
    <div className="overflow-x-auto rounded-lg border border-slate-800">
      <table className="min-w-full text-sm">
        <thead className="bg-slate-900 text-slate-400">
          <tr>
            <th className="text-left px-3 py-2 font-medium">Label</th>
            <th className="text-right px-3 py-2 font-medium">
              Worst Daily Return
            </th>
            <th className="text-right px-3 py-2 font-medium">Raw Halts</th>
            <th className="text-center px-3 py-2 font-medium">
              Daily-limit Respected
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-800 bg-slate-950 text-slate-200">
          {items.map((r) => (
            <tr key={r.label}>
              <td className="px-3 py-2 font-mono text-xs">{r.label}</td>
              <td className="px-3 py-2 text-right tabular-nums">
                {fmtPct(r.worst_daily_return)}
              </td>
              <td className="px-3 py-2 text-right tabular-nums">
                {r.raw_halts.length}
              </td>
              <td className="px-3 py-2 text-center">
                <PassFailBadge pass={r.daily_limit_respected} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
