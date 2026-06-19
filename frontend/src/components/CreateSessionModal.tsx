import { useState } from "react";
import { ApiError } from "../api/client";

const EXAMPLE_YAML = `session_id: my-first-session
provider: gemini
model: gemini-2.0-flash
episodes: 3

parties:
  - id: alice
    kind: person
    name: Alice
    description: A curious scientist
    goals:
      - Understand the new discovery
    traits:
      - analytical
      - open-minded

  - id: bob
    kind: person
    name: Bob
    description: A sceptical engineer
    goals:
      - Challenge assumptions
    traits:
      - pragmatic
      - detail-oriented

environment:
  id: lab
  name: Research Lab
  setting: A modern laboratory in the evening
  facts:
    - The new experiment results are on the whiteboard
  initial_state:
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

          {error && (
            <div className="mb-3 px-3 py-2 rounded bg-red-900/40 border border-red-700 text-red-300 text-sm whitespace-pre-wrap">
              {error}
            </div>
          )}

          <textarea
            value={yaml}
            onChange={(e) => setYaml(e.target.value)}
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
