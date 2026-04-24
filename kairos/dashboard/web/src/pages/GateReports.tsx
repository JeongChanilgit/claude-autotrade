import { useEffect } from "react";
import { Link } from "react-router-dom";
import PassFailBadge from "../components/PassFailBadge";
import { useAppStore } from "../store";

export default function GateReports() {
  const reports = useAppStore((s) => s.gateReports);
  const refresh = useAppStore((s) => s.refreshGateReports);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold tracking-tight">Gate Reports</h1>
      {reports.length === 0 ? (
        <div className="text-slate-500 text-sm">No gate reports found.</div>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-slate-800">
          <table className="min-w-full text-sm">
            <thead className="bg-slate-900 text-slate-400">
              <tr>
                <th className="text-left px-3 py-2 font-medium">Name</th>
                <th className="text-left px-3 py-2 font-medium">Symbol</th>
                <th className="text-left px-3 py-2 font-medium">Interval</th>
                <th className="text-left px-3 py-2 font-medium">Window</th>
                <th className="text-center px-3 py-2 font-medium">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800 bg-slate-950 text-slate-200">
              {reports.map((r) => (
                <tr key={r.name} className="hover:bg-slate-900">
                  <td className="px-3 py-2">
                    <Link
                      to={`/gate-reports/${encodeURIComponent(r.name)}`}
                      className="text-emerald-400 hover:underline font-mono text-xs"
                    >
                      {r.name}
                    </Link>
                  </td>
                  <td className="px-3 py-2 font-mono text-xs">{r.symbol}</td>
                  <td className="px-3 py-2">{r.interval}</td>
                  <td className="px-3 py-2 text-slate-400">
                    {r.start} – {r.end}
                  </td>
                  <td className="px-3 py-2 text-center">
                    <PassFailBadge pass={r.overall_pass} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
