import { useState } from "react";

interface JsonParamsEditorProps {
  initial?: Record<string, unknown>;
  placeholder?: string;
  rows?: number;
  onValid: (obj: Record<string, unknown>) => void;
}

export default function JsonParamsEditor({
  initial,
  placeholder = "{}",
  rows = 8,
  onValid,
}: JsonParamsEditorProps) {
  const [text, setText] = useState<string>(
    initial ? JSON.stringify(initial, null, 2) : "",
  );
  const [err, setErr] = useState<string | null>(null);

  const validate = () => {
    const raw = text.trim();
    if (raw === "") {
      setErr(null);
      onValid({});
      return;
    }
    try {
      const parsed = JSON.parse(raw);
      if (
        parsed == null ||
        typeof parsed !== "object" ||
        Array.isArray(parsed)
      ) {
        setErr("Params must be a JSON object.");
        return;
      }
      setErr(null);
      onValid(parsed as Record<string, unknown>);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div className="flex flex-col gap-1">
      <textarea
        className="w-full font-mono text-xs bg-slate-950 border border-slate-800 rounded p-2 text-slate-200"
        rows={rows}
        placeholder={placeholder}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onBlur={validate}
        spellCheck={false}
      />
      {err && <div className="text-xs text-red-400">{err}</div>}
    </div>
  );
}
