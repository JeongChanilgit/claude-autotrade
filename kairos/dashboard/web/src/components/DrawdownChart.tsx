import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

interface DrawdownChartProps {
  timestamps: string[];
  equity: number[];
  height?: number;
}

// Underwater plot: drawdown % from running max, always <= 0.
function computeDrawdown(equity: number[]): number[] {
  const out: number[] = [];
  let peak = -Infinity;
  for (const v of equity) {
    if (v > peak) peak = v;
    out.push(peak > 0 ? (v - peak) / peak : 0);
  }
  return out;
}

export default function DrawdownChart({
  timestamps,
  equity,
  height = 200,
}: DrawdownChartProps) {
  const dd = computeDrawdown(equity);
  const data = timestamps.map((t, i) => ({ t, dd: (dd[i] ?? 0) * 100 }));
  return (
    <div style={{ width: "100%", height }}>
      <ResponsiveContainer>
        <AreaChart
          data={data}
          margin={{ top: 8, right: 16, bottom: 8, left: 8 }}
        >
          <CartesianGrid stroke="#1e293b" strokeDasharray="3 3" />
          <XAxis
            dataKey="t"
            stroke="#64748b"
            tick={{ fontSize: 11 }}
            minTickGap={40}
          />
          <YAxis
            stroke="#64748b"
            tick={{ fontSize: 11 }}
            tickFormatter={(v: number) => `${v.toFixed(0)}%`}
          />
          <Tooltip
            contentStyle={{
              background: "#0f172a",
              border: "1px solid #1e293b",
              color: "#e2e8f0",
            }}
            formatter={(v: number) => `${v.toFixed(2)}%`}
          />
          <Area
            type="monotone"
            dataKey="dd"
            stroke="#f87171"
            fill="#7f1d1d"
            fillOpacity={0.5}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
