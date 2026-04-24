import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

interface EquityChartProps {
  timestamps: string[];
  equity: number[];
  height?: number;
}

export default function EquityChart({
  timestamps,
  equity,
  height = 320,
}: EquityChartProps) {
  const data = timestamps.map((t, i) => ({ t, equity: equity[i] ?? 0 }));
  return (
    <div style={{ width: "100%", height }}>
      <ResponsiveContainer>
        <LineChart
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
          <YAxis stroke="#64748b" tick={{ fontSize: 11 }} />
          <Tooltip
            contentStyle={{
              background: "#0f172a",
              border: "1px solid #1e293b",
              color: "#e2e8f0",
            }}
          />
          <Line
            type="monotone"
            dataKey="equity"
            stroke="#34d399"
            dot={false}
            strokeWidth={1.5}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
