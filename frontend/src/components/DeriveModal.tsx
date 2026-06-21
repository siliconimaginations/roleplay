import { useEffect, useState } from "react";
import { ApiError, deriveSession, getSessionYaml } from "../api/client";
import { YamlEditor } from "./YamlEditor";

interface Props {
  sourceId: string;
  onClose: () => void;
  onDerived: (newId: string) => void;
}

export function DeriveModal({ sourceId, onClose, onDerived }: Props) {
  const [yaml, setYaml] = useState("");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [newId, setNewId] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  // Fetch source YAML on open.
  useEffect(() => {
    getSessionYaml(sourceId)
      .then((r) => setYaml(r.yaml))
      .catch((e) => setLoadError(String(e)));
  }, [sourceId]);

  async function handleDerive() {
    setSubmitting(true);
    setSubmitError(null);
    try {
      const result = await deriveSession(sourceId, newId.trim() || undefined, yaml);
      onDerived(result.session_id);
    } catch (e) {
      if (e instanceof ApiError) {
        setSubmitError(`${e.status}: ${e.message}`);
      } else {
        setSubmitError(String(e));
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="bg-gray-900 border border-gray-700 rounded-xl w-full max-w-2xl shadow-2xl flex flex-col max-h-[90vh]">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-800">
          <div>
            <h2 className="text-lg font-semibold">Derive Session</h2>
            <p className="text-xs text-gray-500 mt-0.5">
              Inherits config from{" "}
              <span className="font-mono text-gray-400">{sourceId}</span> and
              starts fresh. Edit the YAML before creating.
            </p>
          </div>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-200 text-xl leading-none ml-4"
          >
            ×
          </button>
        </div>

        <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
          {/* New session ID */}
          <div className="px-6 pt-6 mb-5">
            <label className="block text-xs font-medium text-gray-400 mb-1">
              New session ID{" "}
              <span className="text-gray-600 font-normal">(optional — auto-generated if blank)</span>
            </label>
            <input
              type="text"
              value={newId}
              onChange={(e) => setNewId(e.target.value)}
              placeholder={`${sourceId}-v2`}
              className="w-full px-3 py-2 text-sm bg-gray-800 border border-gray-700 rounded text-gray-200 placeholder-gray-600 focus:outline-none focus:border-purple-500"
            />
          </div>

          {/* YAML editor */}
          <div className="flex-1 min-h-0 overflow-auto px-6 pb-6">
            <label className="block text-xs font-medium text-gray-400 mb-2">
              Scenario config
            </label>
            {loadError ? (
              <div className="px-3 py-2 rounded bg-red-900/40 border border-red-700 text-red-300 text-sm">
                Failed to load source config: {loadError}
              </div>
            ) : yaml === "" ? (
              <div className="text-gray-600 text-xs py-4 text-center">Loading…</div>
            ) : (
              <YamlEditor
                value={yaml}
                onChange={setYaml}
                rows={18}
                submitError={submitError}
              />
            )}
          </div>
        </div>

        {/* Footer */}
        <div className="flex justify-end gap-2 px-6 py-4 border-t border-gray-800">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm rounded border border-gray-700 hover:bg-gray-800"
          >
            Cancel
          </button>
          <button
            disabled={submitting || !yaml.trim() || !!loadError}
            onClick={() => void handleDerive()}
            className="px-4 py-2 text-sm rounded bg-purple-700 hover:bg-purple-600 font-medium disabled:opacity-40"
          >
            {submitting ? "Deriving…" : "Derive"}
          </button>
        </div>
      </div>
    </div>
  );
}
