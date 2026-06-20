import { useState } from "react";
import { ApiError, validateSession } from "../api/client";

const EXAMPLE_YAML = `session_id: my-first-session
config:
  default_provider: gemini
  default_model: gemini-2.0-flash
  max_episodes: 3

parties:
  - id: alice
    kind: person
    name: Alice
    persona:
      description: A curious scientist
      goals:
        - Understand the new discovery
      traits:
        - analytical
        - open-minded

  - id: bob
    kind: person
    name: Bob
    persona:
      description: A sceptical engineer
      goals:
        - Challenge assumptions
      traits:
        - pragmatic
        - detail-oriented

  - id: lab
    kind: environment
    name: Research Lab
    persona:
      description: A modern laboratory in the evening
      knowledge:
        - The new experiment results are on the whiteboard
    state:
      env.tension_level: low
      env.time_of_day: evening
`;

type ValidationState =
  | { status: "idle" }
  | { status: "checking" }
  | { status: "ok" }
  | { status: "errors"; errors: string[] };

interface Props {
  onClose: () => void;
  onCreate: (yaml: string) => Promise<void>;
}

export function CreateSessionModal({ onClose, onCreate }: Props) {
  const [yaml, setYaml] = useState(EXAMPLE_YAML);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [validation, setValidation] = useState<ValidationState>({ status: "idle" });

  // Reset validation result whenever YAML changes.
  function handleYamlChange(value: string) {
    setYaml(value);
    if (validation.status !== "idle") setValidation({ status: "idle" });
  }

  async function handleValidate() {
    setValidation({ status: "checking" });
    setError(null);
    try {
      const result = await validateSession(yaml);
      if (result.valid) {
        setValidation({ status: "ok" });
      } else {
        setValidation({ status: "errors", errors: result.errors });
      }
    } catch (e) {
      setValidation({ status: "errors", errors: [String(e)] });
    }
  }

  async function handleSubmit() {
    setLoading(true);
    setError(null);
    try {
      await onCreate(yaml);
    } catch (e) {
      if (e instanceof ApiError) {
        setError(`${e.status}: ${e.message}`);
      } else {
        setError(String(e));
      }
    } finally {
      setLoading(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="bg-gray-900 border border-gray-700 rounded-xl w-full max-w-2xl shadow-2xl flex flex-col max-h-[90vh]">
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-800">
          <h2 className="text-lg font-semibold">New Session</h2>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-200 text-xl leading-none"
          >
            ×
          </button>
        </div>

        <div className="flex-1 overflow-auto p-6">
          <p className="text-sm text-gray-400 mb-3">
            Paste or edit a YAML scenario. The session is created immediately
            and simulation starts when you press{" "}
            <span className="text-blue-400">Run</span> in the session view.
          </p>

          {/* Create error */}
          {error && (
            <div className="mb-3 px-3 py-2 rounded bg-red-900/40 border border-red-700 text-red-300 text-sm whitespace-pre-wrap">
              {error}
            </div>
          )}

          {/* Validation result */}
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
            value={yaml}
            onChange={(e) => handleYamlChange(e.target.value)}
            rows={24}
            spellCheck={false}
            className="w-full bg-gray-950 border border-gray-700 rounded font-mono text-xs text-green-300 p-3 focus:outline-none focus:ring-1 focus:ring-blue-500 resize-none"
          />
        </div>

        <div className="flex justify-end gap-2 px-6 py-4 border-t border-gray-800">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm rounded border border-gray-700 hover:bg-gray-800"
          >
            Cancel
          </button>
          <button
            disabled={loading || !yaml.trim() || validation.status === "checking"}
            onClick={() => void handleValidate()}
            className="px-4 py-2 text-sm rounded border border-gray-600 hover:bg-gray-800 font-medium disabled:opacity-40"
          >
            {validation.status === "checking" ? "Checking…" : "Validate"}
          </button>
          <button
            disabled={loading || !yaml.trim()}
            onClick={() => void handleSubmit()}
            className="px-4 py-2 text-sm rounded bg-blue-600 hover:bg-blue-500 font-medium disabled:opacity-40"
          >
            {loading ? "Creating…" : "Create Session"}
          </button>
        </div>
      </div>
    </div>
  );
}
