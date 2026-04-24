import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import JsonParamsEditor from "../components/JsonParamsEditor";
import OosWarning from "../components/OosWarning";
import { ApiError, createRun } from "../api";
import type { CreateRunRequest } from "../types";

type Strategy = CreateRunRequest["strategy"];
type Interval = CreateRunRequest["interval"];
type Engine = CreateRunRequest["engine"];

// Sealed OOS window [start, end) — any overlap must produce a warning banner.
const OOS_START = "2024-11-01";
const OOS_END = "2025-05-01";

// Half-open overlap check on ISO date strings (YYYY-MM-DD). Lexicographic compare
// works because the format is fixed-width and zero-padded.
function overlapsOos(start: string, end: string): boolean {
  if (!start || !end) return false;
  return start < OOS_END && end > OOS_START;
}

function toUtcIso(date: string): string {
  // Backend expects full RFC3339; a bare date picker value is midnight UTC.
  return `${date}T00:00:00Z`;
}

export default function RunNew() {
  const navigate = useNavigate();

  const [strategy, setStrategy] = useState<Strategy>("orb");
  const [symbol, setSymbol] = useState<string>("BTCUSDT");
  const [interval, setInterval] = useState<Interval>("15m");
  const [start, setStart] = useState<string>("");
  const [end, setEnd] = useState<string>("");
  const [engine, setEngine] = useState<Engine>("vectorbt");
  const [params, setParams] = useState<Record<string, unknown>>({});
  const [initialCash, setInitialCash] = useState<number>(10000);
  const [applyRisk, setApplyRisk] = useState<boolean>(true);

  const [submitting, setSubmitting] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  const oosWarn = useMemo(() => overlapsOos(start, end), [start, end]);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!start || !end) {
      setError("Start and end dates are required.");
      return;
    }
    if (start >= end) {
      setError("Start date must be before end date.");
      return;
    }
    setSubmitting(true);
    try {
      const body: CreateRunRequest = {
        strategy,
        symbol,
        interval,
        start: toUtcIso(start),
        end: toUtcIso(end),
        engine,
        params,
        initial_cash: initialCash,
        apply_risk_simulator: applyRisk,
      };
      const res = await createRun(body);
      navigate(`/run/${res.id}`);
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? err.message
          : err instanceof Error
            ? err.message
            : String(err);
      setError(msg);
      setSubmitting(false);
    }
  };

  return (
    <div className="max-w-2xl flex flex-col gap-6">
      <h1 className="text-2xl font-semibold tracking-tight">New Run</h1>

      {oosWarn && (
        <OosWarning message="Selected window overlaps sealed OOS period" />
      )}

      <form onSubmit={onSubmit} className="flex flex-col gap-5">
        <Field label="Strategy">
          <select
            className="bg-slate-950 border border-slate-800 rounded px-2 py-1.5 text-sm text-slate-100"
            value={strategy}
            onChange={(e) => setStrategy(e.target.value as Strategy)}
          >
            <option value="orb">orb</option>
            <option value="funding_counter">funding_counter</option>
            <option value="vol_compression">vol_compression</option>
            <option value="ensemble">ensemble</option>
          </select>
        </Field>

        <Field label="Symbol">
          <input
            type="text"
            className="bg-slate-950 border border-slate-800 rounded px-2 py-1.5 text-sm text-slate-100 w-48"
            value={symbol}
            onChange={(e) => setSymbol(e.target.value)}
          />
        </Field>

        <Field label="Interval">
          <RadioGroup
            name="interval"
            value={interval}
            options={["1m", "15m"]}
            onChange={(v) => setInterval(v as Interval)}
          />
        </Field>

        <div className="grid grid-cols-2 gap-4">
          <Field label="Start (UTC)">
            <input
              type="date"
              className="bg-slate-950 border border-slate-800 rounded px-2 py-1.5 text-sm text-slate-100"
              value={start}
              onChange={(e) => setStart(e.target.value)}
            />
          </Field>
          <Field label="End (UTC)">
            <input
              type="date"
              className="bg-slate-950 border border-slate-800 rounded px-2 py-1.5 text-sm text-slate-100"
              value={end}
              onChange={(e) => setEnd(e.target.value)}
            />
          </Field>
        </div>

        <Field label="Engine">
          <RadioGroup
            name="engine"
            value={engine}
            options={["vectorbt", "backtrader"]}
            onChange={(v) => setEngine(v as Engine)}
          />
        </Field>

        <Field label="Params (JSON)">
          <JsonParamsEditor onValid={setParams} />
        </Field>

        <Field label="Initial cash">
          <input
            type="number"
            min={0}
            step={1}
            className="bg-slate-950 border border-slate-800 rounded px-2 py-1.5 text-sm text-slate-100 w-48"
            value={initialCash}
            onChange={(e) => setInitialCash(Number(e.target.value))}
          />
        </Field>

        <label className="flex items-center gap-2 text-sm text-slate-200">
          <input
            type="checkbox"
            checked={applyRisk}
            onChange={(e) => setApplyRisk(e.target.checked)}
          />
          Apply risk simulator
        </label>

        <div className="flex items-center gap-3 pt-2">
          <button
            type="submit"
            disabled={submitting}
            className="px-4 py-2 rounded bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-medium"
          >
            {submitting ? "Creating..." : "Create run"}
          </button>
          {error && <span className="text-sm text-red-400">{error}</span>}
        </div>
      </form>
    </div>
  );
}

interface FieldProps {
  label: string;
  children: React.ReactNode;
}

function Field({ label, children }: FieldProps) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-xs uppercase tracking-wider text-slate-400">
        {label}
      </span>
      {children}
    </label>
  );
}

interface RadioGroupProps {
  name: string;
  value: string;
  options: string[];
  onChange: (v: string) => void;
}

function RadioGroup({ name, value, options, onChange }: RadioGroupProps) {
  return (
    <div className="flex gap-4">
      {options.map((opt) => (
        <label
          key={opt}
          className="flex items-center gap-2 text-sm text-slate-200"
        >
          <input
            type="radio"
            name={name}
            value={opt}
            checked={value === opt}
            onChange={() => onChange(opt)}
          />
          {opt}
        </label>
      ))}
    </div>
  );
}
