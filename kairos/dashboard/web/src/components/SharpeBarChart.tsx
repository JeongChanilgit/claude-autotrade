import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

export interface SharpeBarItem {
  name: string;
  sharpe: number;
}

interface SharpeBarChartProps {
  items: SharpeBarItem[];
  height?: number;
}

// Colour bars by sign so negative Sharpe reads red without a legend.
export default function SharpeBarChart({
  items,
  height = 260,
}: SharpeBarChartProps) {
  return (
    <div style={{ width: "100%", height }}>
      <ResponsiveContainer>
        <BarChart
          data={items}
          margin={{ top: 8, right: 16, bottom: 8, left: 8 }}
        >
          <CartesianGrid stroke="#1e293b" strokeDasharray="3 3" />
          <XAxis dataKey="name" stroke="#64748b" tick={{ fontSize: 11 }} />
          <YAxis stroke="#64748b" tick={{ fontSize: 11 }} />
          <Tooltip
            contentStyle={{
              background: "#0f172a",
              border: "1px solid #1e293b",
              color: "#e2e8f0",
            }}
            formatter={(v: number) => v.toFixed(3)}
          />
          <Bar dataKey="sharpe">
            {items.map((item) => (
              <Cell
                key={item.name}
                fill={item.sharpe >= 0 ? "#34d399" : "#f87171"}
              />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
