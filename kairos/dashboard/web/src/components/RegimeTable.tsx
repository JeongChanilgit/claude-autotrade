import PassFailBadge from "./PassFailBadge";

export interface RegimeRow {
  family: string;
  label: string;
  bars: number;
  total_return: number;
  sharpe: number;
  mdd: number;
  break_even: boolean;
}

interface RegimeTableProps {
  items: RegimeRow[];
}

function fmtPct(x: number): string {
  return `${(x * 100).toFixed(2)}%`;
}

export default function RegimeTable({ items }: RegimeTableProps) {
  if (items.length === 0) {
    return <div className="text-slate-500 text-sm">No regime windows.</div>;
  }
  return (
    <div className="overflow-x-auto rounded-lg border border-slate-800">
      <table className="min-w-full text-sm">
        <thead className="bg-slate-900 text-slate-400">
          <tr>
            <th className="text-left px-3 py-2 font-medium">Family</th>
            <th className="text-left px-3 py-2 font-medium">Label</th>
            <th className="text-right px-3 py-2 font-medium">Bars</th>
            <th className="text-right px-3 py-2 font-medium">Total Return</th>
            <th className="text-right px-3 py-2 font-medium">Sharpe</th>
            <th className="text-right px-3 py-2 font-medium">MDD</th>
            <th className="text-center px-3 py-2 font-medium">Break-even</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-800 bg-slate-950 text-slate-200">
          {items.map((r) => (
            <tr key={`${r.family}:${r.label}`}>
              <td className="px-3 py-2 text-slate-400">{r.family}</td>
              <td className="px-3 py-2 font-mono text-xs">{r.label}</td>
              <td className="px-3 py-2 text-right tabular-nums">{r.bars}</td>
              <td className="px-3 py-2 text-right tabular-nums">
                {fmtPct(r.total_return)}
              </td>
              <td className="px-3 py-2 text-right tabular-nums">
                {r.sharpe.toFixed(3)}
              </td>
              <td className="px-3 py-2 text-right tabular-nums">
                {fmtPct(r.mdd)}
              </td>
              <td className="px-3 py-2 text-center">
                <PassFailBadge
                  pass={r.break_even}
                  label={r.break_even ? "YES" : "NO"}
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
