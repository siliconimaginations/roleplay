import { useState } from "react";
import { validateSession } from "../api/client";

type ValidationState =
  | { status: "idle" }
  | { status: "checking" }
  | { status: "ok" }
  | { status: "errors"; errors: string[] };

interface Props {
  value: string;
  onChange: (v: string) => void;
  rows?: number;
  /** If provided, shown above the textarea as an inline error from the parent */
  submitError?: string | null;
}

export function YamlEditor({ value, onChange, rows = 20, submitError }: Props) {
  const [validation, setValidation] = useState<ValidationState>({ status: "idle" });

  function handleChange(v: string) {
    onChange(v);
    if (validation.status !== "idle") setValidation({ status: "idle" });
  }

  async function handleValidate() {
    setValidation({ status: "checking" });
    try {
      const result = await validateSession(value);
      if (result.valid) {
        setValidation({ status: "ok" });
      } else {
        setValidation({ status: "errors", errors: result.errors });
      }
    } catch (e) {
      setValidation({ status: "errors", errors: [String(e)] });
    }
  }

  return (
    <div>
      {submitError && (
        <div className="mb-3 px-3 py-2 rounded bg-red-900/40 border border-red-700 text-red-300 text-sm whitespace-pre-wrap">
          {submitError}
        </div>
      )}

      {validation.status === "ok" && (
        <div className="mb-3 px-3 py-2 rounded bg-green-900/40 border border-green-700 text-green-300 text-sm flex items-center gap-2">
          <span>✓</span>
          <span>Scenario is valid.</span>
        </div>
      )}
      {validation.status === "errors" && (
        <div className="mb-3 px-3 py-2 rounded bg-amber-900/40 border border-amber-700 text-amber-300 text-sm">
          <div className="font-semibold mb-1">
            ✗ {validation.errors.length} validation error
            {validation.errors.length !== 1 ? "s" : ""}:
          </div>
          <ul className="list-disc list-inside space-y-0.5">
            {validation.errors.map((e, i) => (
              <li key={i} className="text-xs">{e}</li>
            ))}
          </ul>
        </div>
      )}

      <textarea
        value={value}
        onChange={(e) => handleChange(e.target.value)}
        rows={rows}
        spellCheck={false}
        className="w-full bg-gray-950 border border-gray-700 rounded font-mono text-xs text-green-300 p-3 focus:outline-none focus:ring-1 focus:ring-blue-500 resize-none"
      />

      <div className="flex justify-end mt-2">
        <button
          disabled={!value.trim() || validation.status === "checking"}
          onClick={() => void handleValidate()}
          className="px-3 py-1.5 text-xs rounded border border-gray-600 hover:bg-gray-800 font-medium disabled:opacity-40"
        >
          {validation.status === "checking" ? "Checking…" : "Validate"}
        </button>
      </div>
    </div>
  );
}
