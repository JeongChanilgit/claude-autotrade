interface PassFailBadgeProps {
  pass: boolean;
  label?: string;
}

export default function PassFailBadge({ pass, label }: PassFailBadgeProps) {
  const text = label ?? (pass ? "PASS" : "FAIL");
  const cls = pass
    ? "bg-emerald-900 text-emerald-300 border-emerald-700"
    : "bg-red-950 text-red-300 border-red-800";
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-semibold border ${cls}`}
    >
      {text}
    </span>
  );
}
