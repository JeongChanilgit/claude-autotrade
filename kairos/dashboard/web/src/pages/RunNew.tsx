import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import OosWarning from "../components/OosWarning";
import { ApiError, createRun } from "../api";
import { useAppStore } from "../store";
import type {
  CreateRunRequest,
  EngineName,
  IntervalName,
  ParamField,
  StrategyEntry,
} from "../types";

// Sealed OOS window [start, end) — any overlap must produce a warning banner.
const OOS_START = "2024-11-01";
const OOS_END = "2025-05-01";

const SYMBOL_SUGGESTIONS = [
  "BTCUSDT",
  "ETHUSDT",
  "SOLUSDT",
  "BNBUSDT",
  "XRPUSDT",
  "ADAUSDT",
  "DOGEUSDT",
  "AVAXUSDT",
  "DOTUSDT",
  "MATICUSDT",
];

const WEIGHT_EPS = 1e-9;

interface SlotRow {
  symbol: string;
  strategy_name: string;
  params: Record<string, unknown>;
  weight: number;
}

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

function findStrategy(
  strategies: StrategyEntry[],
  name: string,
): StrategyEntry | undefined {
  return strategies.find((s) => s.name === name);
}

function equalizeWeights(count: number): number {
  return count > 0 ? 1 / count : 0;
}

function paramsAreValid(
  params: Record<string, unknown>,
  schema: Record<string, ParamField>,
): boolean {
  for (const [key, field] of Object.entries(schema)) {
    const v = params[key];
    if (v === undefined || v === null) return false;
    if (field.type === "int" || field.type === "float") {
      if (typeof v !== "number" || Number.isNaN(v)) return false;
      if (field.min != null && v < field.min) return false;
      if (field.max != null && v > field.max) return false;
    } else if (field.type === "bool") {
      if (typeof v !== "boolean") return false;
    } else if (field.type === "enum") {
      if (typeof v !== "string") return false;
      if (field.choices && !field.choices.includes(v)) return false;
    } else if (field.type === "string") {
      if (typeof v !== "string") return false;
    }
  }
  return true;
}

export default function RunNew() {
  const navigate = useNavigate();
  const strategies = useAppStore((s) => s.strategies);
  const strategiesLoaded = useAppStore((s) => s.strategiesLoaded);
  const refreshStrategies = useAppStore((s) => s.refreshStrategies);

  const [rows, setRows] = useState<SlotRow[]>([]);
  const [interval, setIntervalValue] = useState<IntervalName>("15m");
  const [start, setStart] = useState<string>("");
  const [end, setEnd] = useState<string>("");
  const [engine, setEngine] = useState<EngineName>("vectorbt");
  const [initialCash, setInitialCash] = useState<number>(10000);
  const [applyRisk, setApplyRisk] = useState<boolean>(true);

  const [submitting, setSubmitting] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [errorCode, setErrorCode] = useState<string | null>(null);
  const [registryError, setRegistryError] = useState<string | null>(null);

  // Load the strategy registry once on mount.
  useEffect(() => {
    if (!strategiesLoaded) {
      void refreshStrategies().catch((err: unknown) => {
        const msg =
          err instanceof ApiError
            ? err.message
            : err instanceof Error
              ? err.message
              : String(err);
        setRegistryError(msg);
      });
    }
  }, [strategiesLoaded, refreshStrategies]);

  // Seed the first row once the registry is available.
  useEffect(() => {
    if (strategiesLoaded && strategies.length > 0 && rows.length === 0) {
      const first = strategies[0];
      setRows([
        {
          symbol: "BTCUSDT",
          strategy_name: first.name,
          params: { ...first.default_params },
          weight: 1.0,
        },
      ]);
    }
  }, [strategiesLoaded, strategies, rows.length]);

  const weightSum = useMemo(
    () => rows.reduce((acc, r) => acc + (Number.isFinite(r.weight) ? r.weight : NaN), 0),
    [rows],
  );

  const duplicateSymbols = useMemo(() => {
    const seen = new Set<string>();
    const dupes = new Set<string>();
    for (const r of rows) {
      const s = r.symbol.trim().toUpperCase();
      if (!s) continue;
      if (seen.has(s)) dupes.add(s);
      seen.add(s);
    }
    return dupes;
  }, [rows]);

  const paramsValid = useMemo(() => {
    return rows.every((r) => {
      const entry = findStrategy(strategies, r.strategy_name);
      if (!entry) return false;
      return paramsAreValid(r.params, entry.param_schema);
    });
  }, [rows, strategies]);

  const weightsOk =
    rows.length > 0 &&
    rows.every((r) => Number.isFinite(r.weight)) &&
    Math.abs(weightSum - 1) < WEIGHT_EPS;

  const symbolsFilled = rows.every((r) => r.symbol.trim().length > 0);
  const datesOk = Boolean(start && end && start < end);

  const canSubmit =
    !submitting &&
    strategiesLoaded &&
    rows.length >= 1 &&
    symbolsFilled &&
    duplicateSymbols.size === 0 &&
    weightsOk &&
    paramsValid &&
    datesOk &&
    Number.isFinite(initialCash);

  const oosWarn = useMemo(() => overlapsOos(start, end), [start, end]);

  const updateRow = (idx: number, patch: Partial<SlotRow>) => {
    setRows((prev) =>
      prev.map((r, i) => (i === idx ? { ...r, ...patch } : r)),
    );
  };

  const addRow = () => {
    if (strategies.length === 0) return;
    const first = strategies[0];
    setRows((prev) => {
      const next: SlotRow[] = [
        ...prev,
        {
          symbol: "",
          strategy_name: first.name,
          params: { ...first.default_params },
          weight: 0,
        },
      ];
      const w = equalizeWeights(next.length);
      return next.map((r) => ({ ...r, weight: w }));
    });
  };

  const removeRow = (idx: number) => {
    setRows((prev) => {
      const next = prev.filter((_, i) => i !== idx);
      if (next.length === 0) return next;
      const w = equalizeWeights(next.length);
      return next.map((r) => ({ ...r, weight: w }));
    });
  };

  const equalizeAll = () => {
    setRows((prev) => {
      if (prev.length === 0) return prev;
      const w = equalizeWeights(prev.length);
      return prev.map((r) => ({ ...r, weight: w }));
    });
  };

  const onStrategyChange = (idx: number, name: string) => {
    const entry = findStrategy(strategies, name);
    if (!entry) return;
    updateRow(idx, {
      strategy_name: name,
      params: { ...entry.default_params },
    });
  };

  const onParamChange = (idx: number, key: string, value: unknown) => {
    setRows((prev) =>
      prev.map((r, i) =>
        i === idx ? { ...r, params: { ...r.params, [key]: value } } : r,
      ),
    );
  };

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setErrorCode(null);
    if (!strategiesLoaded) {
      setError("Strategy registry has not loaded yet.");
      return;
    }
    if (rows.length === 0) {
      setError("At least one symbol slot is required.");
      return;
    }
    if (!start || !end) {
      setError("Start and end dates are required.");
      return;
    }
    if (start >= end) {
      setError("Start date must be before end date.");
      return;
    }
    if (duplicateSymbols.size > 0) {
      setError(
        `Duplicate symbols: ${Array.from(duplicateSymbols).join(", ")}`,
      );
      return;
    }
    if (!weightsOk) {
      setError(`Weights must sum to 1.0 (currently ${weightSum.toFixed(6)}).`);
      return;
    }
    if (!paramsValid) {
      setError("One or more strategy params are invalid.");
      return;
    }
    for (const r of rows) {
      if (!findStrategy(strategies, r.strategy_name)) {
        setError(`Unknown strategy: ${r.strategy_name}`);
        return;
      }
    }

    setSubmitting(true);
    try {
      const strategiesOut: Record<
        string,
        { name: string; params: Record<string, unknown> }
      > = {};
      const weightsOut: Record<string, number> = {};
      for (const r of rows) {
        const sym = r.symbol.trim().toUpperCase();
        strategiesOut[sym] = { name: r.strategy_name, params: r.params };
        weightsOut[sym] = r.weight;
      }
      const body: CreateRunRequest = {
        strategies: strategiesOut,
        weights: weightsOut,
        interval,
        start: toUtcIso(start),
        end: toUtcIso(end),
        engine,
        initial_cash: initialCash,
        apply_risk_simulator: applyRisk,
      };
      const res = await createRun(body);
      navigate(`/run/${res.id}`);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
        setErrorCode(err.code);
      } else if (err instanceof Error) {
        setError(err.message);
      } else {
        setError(String(err));
      }
      setSubmitting(false);
    }
  };

  if (!strategiesLoaded) {
    return (
      <div className="max-w-2xl flex flex-col gap-6">
        <h1 className="text-2xl font-semibold tracking-tight">New Run</h1>
        {registryError ? (
          <div className="text-sm text-red-400">
            Failed to load strategy registry: {registryError}
          </div>
        ) : (
          <div className="text-slate-500 text-sm">Loading strategies…</div>
        )}
      </div>
    );
  }

  const weightSumDisplay = weightSum.toFixed(6);
  const weightsBad = !weightsOk;

  return (
    <div className="max-w-4xl flex flex-col gap-6">
      <h1 className="text-2xl font-semibold tracking-tight">New Run</h1>

      {oosWarn && (
        <OosWarning message="Selected window overlaps sealed OOS period" />
      )}

      <form onSubmit={onSubmit} className="flex flex-col gap-5">
        <section className="flex flex-col gap-3">
          <div className="flex items-center justify-between">
            <h2 className="text-xs uppercase tracking-wider text-slate-400">
              Portfolio
            </h2>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={equalizeAll}
                className="px-3 py-1 rounded border border-slate-700 hover:bg-slate-800 text-slate-200 text-xs"
              >
                Equalize
              </button>
              <button
                type="button"
                onClick={addRow}
                className="px-3 py-1 rounded border border-slate-700 hover:bg-slate-800 text-slate-200 text-xs"
              >
                Add symbol
              </button>
            </div>
          </div>

          <datalist id="symbol-suggestions">
            {SYMBOL_SUGGESTIONS.map((s) => (
              <option key={s} value={s} />
            ))}
          </datalist>

          <div className="flex flex-col gap-3">
            {rows.map((row, idx) => {
              const entry = findStrategy(strategies, row.strategy_name);
              const dupe = duplicateSymbols.has(
                row.symbol.trim().toUpperCase(),
              );
              return (
                <div
                  key={idx}
                  className="rounded border border-slate-800 bg-slate-950 p-3 flex flex-col gap-3"
                >
                  <div className="grid grid-cols-12 gap-3 items-end">
                    <div className="col-span-4 flex flex-col gap-1.5">
                      <span className="text-xs uppercase tracking-wider text-slate-400">
                        Symbol
                      </span>
                      <input
                        type="text"
                        list="symbol-suggestions"
                        className={`bg-slate-950 border rounded px-2 py-1.5 text-sm text-slate-100 ${
                          dupe ? "border-red-600" : "border-slate-800"
                        }`}
                        value={row.symbol}
                        onChange={(e) =>
                          updateRow(idx, {
                            symbol: e.target.value.toUpperCase(),
                          })
                        }
                      />
                      {dupe && (
                        <span className="text-xs text-red-400">
                          Duplicate symbol
                        </span>
                      )}
                    </div>
                    <div className="col-span-4 flex flex-col gap-1.5">
                      <span className="text-xs uppercase tracking-wider text-slate-400">
                        Strategy
                      </span>
                      <select
                        className="bg-slate-950 border border-slate-800 rounded px-2 py-1.5 text-sm text-slate-100"
                        value={row.strategy_name}
                        onChange={(e) =>
                          onStrategyChange(idx, e.target.value)
                        }
                      >
                        {strategies.map((s) => (
                          <option key={s.name} value={s.name}>
                            {s.label}
                          </option>
                        ))}
                      </select>
                    </div>
                    <div className="col-span-3 flex flex-col gap-1.5">
                      <span className="text-xs uppercase tracking-wider text-slate-400">
                        Weight
                      </span>
                      <input
                        type="number"
                        step="0.01"
                        min={0}
                        max={1}
                        className="bg-slate-950 border border-slate-800 rounded px-2 py-1.5 text-sm text-slate-100"
                        value={Number.isFinite(row.weight) ? row.weight : ""}
                        onChange={(e) => {
                          const v = e.target.value;
                          updateRow(idx, {
                            weight: v === "" ? NaN : Number(v),
                          });
                        }}
                      />
                    </div>
                    <div className="col-span-1 flex justify-end">
                      <button
                        type="button"
                        onClick={() => removeRow(idx)}
                        disabled={rows.length <= 1}
                        className="px-2 py-1 rounded border border-slate-700 hover:bg-slate-800 text-slate-300 text-xs disabled:opacity-40 disabled:cursor-not-allowed"
                      >
                        Remove
                      </button>
                    </div>
                  </div>

                  {entry && (
                    <ParamsEditor
                      entry={entry}
                      params={row.params}
                      onChange={(key, value) =>
                        onParamChange(idx, key, value)
                      }
                    />
                  )}
                </div>
              );
            })}
          </div>

          <div
            className={`text-xs ${
              weightsBad ? "text-red-400" : "text-slate-400"
            }`}
          >
            Weight sum: {weightSumDisplay}
            {weightsBad && " — must equal 1.0"}
          </div>
        </section>

        <Field label="Interval">
          <RadioGroup
            name="interval"
            value={interval}
            options={["1m", "15m"]}
            onChange={(v) => setIntervalValue(v as IntervalName)}
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
            onChange={(v) => setEngine(v as EngineName)}
          />
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
            disabled={!canSubmit}
            className="px-4 py-2 rounded bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-medium"
          >
            {submitting ? "Creating..." : "Create run"}
          </button>
          {error && (
            <span
              className={`text-sm ${
                errorCode === "STRATEGY_NOT_FOUND"
                  ? "text-red-300 font-semibold"
                  : "text-red-400"
              }`}
            >
              {errorCode ? `[${errorCode}] ` : ""}
              {error}
            </span>
          )}
        </div>
      </form>
    </div>
  );
}

interface ParamsEditorProps {
  entry: StrategyEntry;
  params: Record<string, unknown>;
  onChange: (key: string, value: unknown) => void;
}

function ParamsEditor({ entry, params, onChange }: ParamsEditorProps) {
  const keys = Object.keys(entry.param_schema);
  if (keys.length === 0) {
    return (
      <div className="text-xs text-slate-500">
        No parameters for {entry.label}.
      </div>
    );
  }
  return (
    <div className="grid grid-cols-2 gap-3">
      {keys.map((key) => {
        const field = entry.param_schema[key];
        const value = params[key];
        return (
          <ParamInput
            key={key}
            name={key}
            field={field}
            value={value}
            onChange={(v) => onChange(key, v)}
          />
        );
      })}
    </div>
  );
}

interface ParamInputProps {
  name: string;
  field: ParamField;
  value: unknown;
  onChange: (value: unknown) => void;
}

function ParamInput({ name, field, value, onChange }: ParamInputProps) {
  const inputClass =
    "bg-slate-950 border border-slate-800 rounded px-2 py-1 text-sm text-slate-100";

  let control: React.ReactNode;
  if (field.type === "int" || field.type === "float") {
    const numVal = typeof value === "number" ? value : Number.NaN;
    control = (
      <input
        type="number"
        step={field.type === "int" ? 1 : "any"}
        min={field.min}
        max={field.max}
        className={inputClass}
        value={Number.isFinite(numVal) ? numVal : ""}
        onChange={(e) => {
          const raw = e.target.value;
          if (raw === "") {
            onChange(Number.NaN);
            return;
          }
          const n =
            field.type === "int" ? Number.parseInt(raw, 10) : Number(raw);
          onChange(n);
        }}
      />
    );
  } else if (field.type === "bool") {
    control = (
      <input
        type="checkbox"
        checked={Boolean(value)}
        onChange={(e) => onChange(e.target.checked)}
      />
    );
  } else if (field.type === "enum") {
    const choices = field.choices ?? [];
    const strVal = typeof value === "string" ? value : "";
    control = (
      <select
        className={inputClass}
        value={strVal}
        onChange={(e) => onChange(e.target.value)}
      >
        {choices.map((c) => (
          <option key={c} value={c}>
            {c}
          </option>
        ))}
      </select>
    );
  } else {
    const strVal = typeof value === "string" ? value : "";
    control = (
      <input
        type="text"
        className={inputClass}
        value={strVal}
        onChange={(e) => onChange(e.target.value)}
      />
    );
  }

  return (
    <label className="flex flex-col gap-1">
      <span className="text-[11px] uppercase tracking-wider text-slate-400">
        {name}
      </span>
      {control}
      {field.help && (
        <span className="text-[11px] text-slate-500">{field.help}</span>
      )}
    </label>
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
