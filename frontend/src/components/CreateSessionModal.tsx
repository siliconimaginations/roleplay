import { useState } from "react";
import { ApiError, generateSession, validateSession } from "../api/client";

const EXAMPLE_YAML = `session_id: my-first-session
config:
  default_provider: gemini
  default_model: gemini-3.5-flash
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

  // Generate-from-prompt state
  const [prompt, setPrompt] = useState("");
  const [generating, setGenerating] = useState(false);
  const [generateError, setGenerateError] = useState<string | null>(null);

  // Reset validation result whenever YAML changes.
  function handleYamlChange(value: string) {
    setYaml(value);
    if (validation.status !== "idle") setValidation({ status: "idle" });
  }

  async function handleGenerate() {
    if (!prompt.trim()) return;
    setGenerating(true);
    setGenerateError(null);
    setValidation({ status: "idle" });
    try {
      const result = await generateSession(prompt.trim());
      setYaml(result.yaml);
    } catch (e) {
      if (e instanceof ApiError) {
        setGenerateError(`Generation failed (${e.status}): ${e.message}`);
      } else {
        setGenerateError(String(e));
      }
    } finally {
      setGenerating(false);
    }
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
          {/* Generate from prompt */}
          <div className="mb-4">
            <label className="block text-sm font-medium text-gray-300 mb-1">
              Generate from prompt
            </label>
            <div className="flex gap-2">
              <input
                type="text"
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && !generating && void handleGenerate()}
                placeholder="e.g. a tense salary negotiation between an employee and their manager"
                className="flex-1 bg-gray-950 border border-gray-700 rounded px-3 py-2 text-sm text-gray-200 placeholder-gray-600 focus:outline-none focus:ring-1 focus:ring-purple-500"
              />
              <button
                disabled={generating || !prompt.trim()}
                onClick={() => void handleGenerate()}
                className="px-4 py-2 text-sm rounded bg-purple-700 hover:bg-purple-600 font-medium disabled:opacity-40 whitespace-nowrap"
              >
                {generating ? "Generating…" : "Generate"}
              </button>
            </div>
            {generateError && (
              <p className="mt-1 text-xs text-red-400">{generateError}</p>
            )}
          </div>

          <div className="border-t border-gray-800 mb-4" />

          <p className="text-sm text-gray-400 mb-3">
            Or paste / edit a YAML scenario directly. The session is created
            immediately and simulation starts when you press{" "}
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
            rows={20}
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
