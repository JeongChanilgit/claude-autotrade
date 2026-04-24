import { useRef, useState } from "react";
import type { Trade } from "../types";

interface TradesTableProps {
  rows: Trade[];
}

// Manual virtualization: only materialize rows in the viewport + overscan.
// Avoids depending on react-window/react-virtuoso while still keeping the
// DOM bounded for the 5000-row upper bound allowed by /api/runs/{id}/trades.
const ROW_HEIGHT = 28;
const VIEWPORT_HEIGHT = 640;
const OVERSCAN = 8;

const COLUMNS: { key: keyof Trade; label: string; className?: string }[] = [
  { key: "symbol", label: "Symbol", className: "w-24" },
  { key: "entry_time", label: "Entry", className: "w-40" },
  { key: "exit_time", label: "Exit", className: "w-40" },
  { key: "side", label: "Side", className: "w-14 text-right" },
  { key: "qty", label: "Qty", className: "w-20 text-right" },
  { key: "entry_price", label: "Entry Px", className: "w-24 text-right" },
  { key: "exit_price", label: "Exit Px", className: "w-24 text-right" },
  { key: "net_pnl", label: "Net PnL", className: "w-24 text-right" },
  { key: "fees", label: "Fees", className: "w-20 text-right" },
  { key: "exit_reason", label: "Reason", className: "w-28" },
];

function formatNumber(n: number, digits = 2): string {
  return n.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function cell(row: Trade, key: keyof Trade): string {
  const v = row[key];
  if (key === "side") return v === 1 ? "LONG" : "SHORT";
  if (typeof v === "number") {
    if (key === "qty") return formatNumber(v, 4);
    return formatNumber(v, 2);
  }
  return String(v);
}

export default function TradesTable({ rows }: TradesTableProps) {
  const [scrollTop, setScrollTop] = useState(0);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  const total = rows.length;
  const start = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - OVERSCAN);
  const visibleCount = Math.ceil(VIEWPORT_HEIGHT / ROW_HEIGHT) + OVERSCAN * 2;
  const end = Math.min(total, start + visibleCount);
  const slice = rows.slice(start, end);

  return (
    <div className="border border-slate-800 rounded overflow-hidden bg-slate-900">
      <div className="flex px-2 py-1 text-xs font-semibold text-slate-300 border-b border-slate-800 bg-slate-950">
        {COLUMNS.map((c) => (
          <div key={String(c.key)} className={`px-2 ${c.className ?? ""}`}>
            {c.label}
          </div>
        ))}
      </div>
      <div
        ref={scrollRef}
        onScroll={(e) => setScrollTop((e.target as HTMLDivElement).scrollTop)}
        style={{ height: VIEWPORT_HEIGHT, overflowY: "auto" }}
        className="relative"
      >
        <div style={{ height: total * ROW_HEIGHT, position: "relative" }}>
          {slice.map((row, i) => {
            const index = start + i;
            const pnlCls =
              row.net_pnl > 0
                ? "text-emerald-400"
                : row.net_pnl < 0
                  ? "text-red-400"
                  : "text-slate-300";
            return (
              <div
                key={`${row.symbol}-${row.entry_time}-${index}`}
                style={{
                  position: "absolute",
                  top: index * ROW_HEIGHT,
                  left: 0,
                  right: 0,
                  height: ROW_HEIGHT,
                }}
                className={`flex items-center px-2 text-xs font-mono border-b border-slate-800/50 ${
                  index % 2 === 0 ? "bg-slate-900" : "bg-slate-900/60"
                }`}
              >
                {COLUMNS.map((c) => {
                  const extra = c.key === "net_pnl" ? pnlCls : "text-slate-300";
                  return (
                    <div
                      key={String(c.key)}
                      className={`px-2 truncate ${c.className ?? ""} ${extra}`}
                      title={cell(row, c.key)}
                    >
                      {cell(row, c.key)}
                    </div>
                  );
                })}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
