import { useState } from "react";
import { ApiError, generateSession } from "../api/client";
import { YamlEditor } from "./YamlEditor";

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

interface Props {
  onClose: () => void;
  onCreate: (yaml: string) => Promise<void>;
}

export function CreateSessionModal({ onClose, onCreate }: Props) {
  const [yaml, setYaml] = useState(EXAMPLE_YAML);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Generate-from-prompt state
  const [prompt, setPrompt] = useState("");
  const [fixCycles, setFixCycles] = useState(0);
  const [generating, setGenerating] = useState(false);
  const [generateError, setGenerateError] = useState<string | null>(null);

  async function handleGenerate() {
    if (!prompt.trim()) return;
    setGenerating(true);
    setGenerateError(null);
    try {
      const result = await generateSession(prompt.trim(), fixCycles);
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
            <div className="flex flex-col gap-2">
              <textarea
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && !generating && (e.preventDefault(), void handleGenerate())}
                placeholder="e.g. a tense salary negotiation between an employee and their manager"
                rows={3}
                className="w-full bg-gray-950 border border-gray-700 rounded px-3 py-2 text-sm text-gray-200 placeholder-gray-600 focus:outline-none focus:ring-1 focus:ring-purple-500 resize-none"
              />
              <button
                disabled={generating || !prompt.trim()}
                onClick={() => void handleGenerate()}
                className="self-end px-4 py-2 text-sm rounded bg-purple-700 hover:bg-purple-600 font-medium disabled:opacity-40 whitespace-nowrap"
              >
                {generating ? "Generating…" : "Generate"}
              </button>
            </div>
            <div className="flex items-center gap-2 mt-2">
              <label className="text-xs text-gray-400 whitespace-nowrap">Fix cycles (0-5):</label>
              <input
                type="number"
                min={0}
                max={5}
                value={fixCycles}
                onChange={(e) => setFixCycles(Math.max(0, Math.min(5, Number(e.target.value))))}
                className="w-14 bg-gray-950 border border-gray-700 rounded px-2 py-1 text-sm text-gray-200 focus:outline-none focus:ring-1 focus:ring-purple-500 text-center"
              />
              <span className="text-xs text-gray-500">Validate &amp; auto-correct after generation</span>
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

          <YamlEditor value={yaml} onChange={setYaml} rows={20} submitError={error} />
        </div>

        <div className="flex justify-end gap-2 px-6 py-4 border-t border-gray-800">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm rounded border border-gray-700 hover:bg-gray-800"
          >
            Cancel
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
