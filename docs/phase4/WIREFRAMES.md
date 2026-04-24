# Phase 4 — Frontend Wireframes & Routing

**FROZEN** — Sonnet-4D/4E/4F/4G implement verbatim.

## Tech stack
- React 18 + TypeScript strict
- Vite dev server (port 5173)
- React Router v6 (SPA routing)
- Zustand for global state
- Recharts for charts
- shadcn/ui + Tailwind v4 for components
- Playwright for E2E

## Routes
| Path              | Page           | Sonnet |
|-------------------|----------------|--------|
| `/`               | Home           | 4E     |
| `/run/new`        | Run (create)   | 4E     |
| `/run/:id`        | Results        | 4F     |
| `/compare`        | Compare        | 4G     |
| `/trades/:id`     | Trades         | 4G     |
| `/gate-reports`   | Gate Viewer    | 4F     |
| `/gate-reports/:name` | Gate Detail | 4F     |

## Zustand store (`src/store.ts`)
```ts
interface AppStore {
  runs: RunSummary[];
  runsLoaded: boolean;
  refreshRuns: () => Promise<void>;

  currentRun: RunDetail | null;
  loadRun: (id: string) => Promise<void>;

  compareIds: [string | null, string | null];
  setCompareA: (id: string | null) => void;
  setCompareB: (id: string | null) => void;

  gateReports: GateReportSummary[];
  refreshGateReports: () => Promise<void>;
}
```

## API client (`src/api.ts`)
Thin fetch wrapper exposing one function per endpoint. Base URL from
`import.meta.env.VITE_API_BASE ?? "/api"`. All functions typed to DTOs in
`src/types.ts` (copy of `docs/phase4/API.md` TypeScript section).

## WS hook (`src/ws.ts`)
```ts
function useBacktestProgress(runId: string): {
  pct: number;
  stage: string | null;
  log: string[];          // capped to last 50 lines
  done: boolean;
  error: string | null;
};
```

## Pages

### Home `/`
- Heading + version badge.
- Recent runs table (10 rows): id / strategy / symbol / status / CAGR / Sharpe / action link.
- "New Run" CTA → `/run/new`.
- "Gate Reports" link → `/gate-reports`.

### Run (create) `/run/new`
- Form (react-hook-form or controlled):
  - strategy (select)
  - symbol (text, default BTCUSDT)
  - interval (radio: 1m | 15m)
  - start / end (date pickers, UTC)
  - engine (radio: vectorbt | backtrader)
  - params (JSON textarea, validated on blur)
  - initial_cash (number, default 10000)
  - apply_risk (checkbox, default on)
- OOS warning banner when start/end overlaps OOS window (red background).
- Submit → `POST /api/runs` → navigate to `/run/{id}` (showing progress bar).

### Results `/run/:id`
- Header: strategy / window / status / engine
- Progress bar (WS-driven) while status ∈ {pending, running}
- Metrics cards: CAGR, MDD, Sharpe, Calmar, PF, Win%, Total trades
- Equity curve (Recharts LineChart), x-axis time, y-axis equity
- Drawdown underwater plot below
- "Engine A/B compare" widget: if the run has a sibling with the other engine,
  show rel-diff badges (green if < 1%).
- Link to `/trades/{id}`.

### Trades `/trades/:id`
- Virtualized table (react-window or similar) with 5000-row capacity.
- Columns: entry_time, exit_time, side, qty, entry_price, exit_price, net_pnl,
  fees, exit_reason.
- Top bar: total PnL, win rate, avg win/loss, filter by exit_reason.

### Compare `/compare`
- Two run selectors (autocomplete or dropdown).
- Side-by-side metric table (metric | A | B | rel_diff).
- Overlaid equity curves (both on same axis, different colors).
- PnL diff underlay chart.
- Pass/fail badge at top: "±1% engine agreement: PASS/FAIL".

### Gate Viewer `/gate-reports`
- Table of files in `reports/` matching `phase3_gate_*.json`.
- Columns: name, symbol, window, overall_pass.
- Click row → `/gate-reports/{name}`.

### Gate Detail `/gate-reports/:name`
- Summary pass/fail rubric (4 lines from `summary_lines()`).
- Per-strategy Sharpe bar chart.
- Ensemble headline cards.
- Per-regime table (family, label, bars, total_return, sharpe, mdd, break-even).
- Stress results table (label, worst_daily_return, halts, limit respected).

## Component inventory (`src/components/`)
- `Layout.tsx` — sidebar + topbar shell
- `MetricCard.tsx`
- `EquityChart.tsx`
- `DrawdownChart.tsx`
- `RunsTable.tsx`
- `TradesTable.tsx` (virtualized)
- `ProgressBar.tsx`
- `OosWarning.tsx`
- `PassFailBadge.tsx`
- `JsonParamsEditor.tsx`
