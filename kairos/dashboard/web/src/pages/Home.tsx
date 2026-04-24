import { useEffect } from "react";
import { Link } from "react-router-dom";
import RunsTable from "../components/RunsTable";
import { useAppStore } from "../store";

export default function Home() {
  const runs = useAppStore((s) => s.runs);
  const runsLoaded = useAppStore((s) => s.runsLoaded);
  const refreshRuns = useAppStore((s) => s.refreshRuns);

  // Fetch once on mount; store memoises via runsLoaded to avoid refetch on revisit.
  useEffect(() => {
    if (!runsLoaded) {
      void refreshRuns();
    }
  }, [runsLoaded, refreshRuns]);

  const recent = runs.slice(0, 10);

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold tracking-tight">
          Kairos Backtest Dashboard
        </h1>
        <div className="flex gap-2">
          <Link
            to="/run/new"
            className="px-4 py-2 rounded bg-emerald-600 hover:bg-emerald-500 text-white text-sm font-medium"
          >
            New Run
          </Link>
          <Link
            to="/gate-reports"
            className="px-4 py-2 rounded border border-slate-700 hover:bg-slate-800 text-slate-200 text-sm font-medium"
          >
            Gate Reports
          </Link>
        </div>
      </div>

      <section className="flex flex-col gap-3">
        <h2 className="text-sm uppercase tracking-wider text-slate-400">
          Recent runs
        </h2>
        {!runsLoaded ? (
          <div className="text-slate-500 text-sm">Loading…</div>
        ) : (
          <RunsTable runs={recent} emptyMessage="No runs yet" />
        )}
      </section>
    </div>
  );
}
