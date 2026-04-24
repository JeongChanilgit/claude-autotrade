interface MetricCardProps {
  label: string;
  value: string | number;
  delta?: string | number;
  deltaTone?: "pos" | "neg" | "neutral";
}

export default function MetricCard({
  label,
  value,
  delta,
  deltaTone = "neutral",
}: MetricCardProps) {
  const deltaClass =
    deltaTone === "pos"
      ? "text-emerald-400"
      : deltaTone === "neg"
        ? "text-red-400"
        : "text-slate-400";
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900 p-4 min-w-[8rem]">
      <div className="text-xs uppercase tracking-wide text-slate-500">
        {label}
      </div>
      <div className="text-2xl font-semibold text-slate-100 mt-1">{value}</div>
      {delta != null && (
        <div className={`text-xs mt-1 ${deltaClass}`}>{delta}</div>
      )}
    </div>
  );
}
