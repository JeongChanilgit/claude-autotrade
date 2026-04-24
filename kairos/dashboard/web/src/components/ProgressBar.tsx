interface ProgressBarProps {
  pct: number;
  stage?: string | null;
  lastLog?: string | null;
}

export default function ProgressBar({
  pct,
  stage,
  lastLog,
}: ProgressBarProps) {
  const clamped = Math.max(0, Math.min(100, pct));
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900 p-4">
      <div className="flex justify-between text-xs text-slate-400 mb-2">
        <span>{stage ?? "starting…"}</span>
        <span>{clamped.toFixed(1)}%</span>
      </div>
      <div className="w-full h-2 bg-slate-800 rounded overflow-hidden">
        <div
          className="h-full bg-emerald-500 transition-[width] duration-150"
          style={{ width: `${clamped}%` }}
        />
      </div>
      {lastLog && (
        <div className="mt-2 text-xs font-mono text-slate-500 truncate">
          {lastLog}
        </div>
      )}
    </div>
  );
}
