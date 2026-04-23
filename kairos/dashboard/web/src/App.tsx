import { useEffect, useState } from "react";

type Health = { status: string; version: string };

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/health")
      .then((r) => r.json())
      .then(setHealth)
      .catch((e) => setError(String(e)));
  }, []);

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 p-8 font-sans">
      <header className="max-w-3xl mx-auto">
        <h1 className="text-3xl font-bold tracking-tight">Kairos</h1>
        <p className="text-slate-400 mt-1">OOS backtest-only validation dashboard · Phase 0 scaffold</p>
      </header>
      <section className="max-w-3xl mx-auto mt-8 rounded-lg border border-slate-800 bg-slate-900 p-6">
        <h2 className="text-lg font-semibold mb-3">API health</h2>
        {error && <pre className="text-red-400 text-sm">{error}</pre>}
        {!error && !health && <p className="text-slate-500">loading…</p>}
        {health && (
          <pre className="text-emerald-400 text-sm">{JSON.stringify(health, null, 2)}</pre>
        )}
      </section>
      <footer className="max-w-3xl mx-auto mt-8 text-xs text-slate-500">
        Pages (Home · Run · Results · Compare · Trades) land in Phase 4.
      </footer>
    </div>
  );
}
