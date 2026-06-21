import { useCallback, useEffect, useState } from "react";
import type { SessionSummary } from "../api/types";
import {
  createSession,
  deleteSession,
  deriveSession,
  forkSession,
  listSessions,
} from "../api/client";
import { CreateSessionModal } from "../components/CreateSessionModal";

const STATUS_COLORS: Record<string, string> = {
  idle: "text-gray-400",
  running: "text-blue-400",
  paused: "text-yellow-400",
  done: "text-green-400",
  error: "text-red-400",
};

interface Props {
  onOpen: (id: string) => void;
}

type ActionMode = "fork" | "derive";

interface NameModalState {
  sourceId: string;
  mode: ActionMode;
}

export function SessionsPage({ onOpen }: Props) {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [actionId, setActionId] = useState<string | null>(null);
  const [nameModal, setNameModal] = useState<NameModalState | null>(null);
  const [pendingName, setPendingName] = useState("");

  const refresh = useCallback(async () => {
    try {
      const data = await listSessions();
      setSessions(data.sort((a, b) => b.created_at.localeCompare(a.created_at)));
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const t = setInterval(() => void refresh(), 5000);
    return () => clearInterval(t);
  }, [refresh]);

  async function handleCreate(yaml: string) {
    const s = await createSession(yaml);
    setShowCreate(false);
    await refresh();
    onOpen(s.session_id);
  }

  function openNameModal(id: string, mode: ActionMode) {
    setNameModal({ sourceId: id, mode });
    setPendingName("");
  }

  async function commitAction() {
    if (!nameModal) return;
    const { sourceId, mode } = nameModal;
    const customId = pendingName.trim() || undefined;
    setNameModal(null);
    setActionId(sourceId + "-" + mode);
    try {
      const s =
        mode === "fork"
          ? await forkSession(sourceId, customId)
          : await deriveSession(sourceId, customId);
      await refresh();
      onOpen(s.session_id);
    } catch (e) {
      alert(String(e));
    } finally {
      setActionId(null);
    }
  }

  async function handleDelete(id: string) {
    if (!confirm(`Delete session ${id.slice(0, 8)}…?`)) return;
    setActionId(id + "-delete");
    try {
      await deleteSession(id);
      await refresh();
    } catch (e) {
      alert(String(e));
    } finally {
      setActionId(null);
    }
  }

  return (
    <div className="max-w-5xl mx-auto px-6 py-8">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Sessions</h1>
        <div className="flex gap-2">
          <button
            onClick={() => void refresh()}
            className="px-3 py-1.5 text-sm rounded border border-gray-700 hover:bg-gray-800"
          >
            Refresh
          </button>
          <button
            onClick={() => setShowCreate(true)}
            className="px-3 py-1.5 text-sm rounded bg-blue-600 hover:bg-blue-500 font-medium"
          >
            + New Session
          </button>
        </div>
      </div>

      {error && (
        <div className="mb-4 px-4 py-3 rounded bg-red-900/40 border border-red-700 text-red-300 text-sm">
          {error}
        </div>
      )}

      {loading ? (
        <div className="text-center py-16 text-gray-500">Loading…</div>
      ) : sessions.length === 0 ? (
        <div className="text-center py-20 text-gray-500">
          <p className="text-4xl mb-4">🎭</p>
          <p className="text-lg mb-2">No sessions yet</p>
          <p className="text-sm">Create one from a YAML scenario file.</p>
        </div>
      ) : (
        <div className="rounded-xl border border-gray-800 overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-900 text-gray-400 uppercase text-xs tracking-wide">
              <tr>
                <th className="text-left px-4 py-3">Session ID</th>
                <th className="text-left px-4 py-3">Created</th>
                <th className="text-left px-4 py-3">Episodes</th>
                <th className="text-left px-4 py-3">Status</th>
                <th className="px-4 py-3" />
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800">
              {sessions.map((s) => (
                <tr
                  key={s.session_id}
                  className="hover:bg-gray-900/50 cursor-pointer"
                  onClick={() => onOpen(s.session_id)}
                >
                  <td className="px-4 py-3 font-mono text-gray-300">
                    {s.session_id.slice(0, 8)}…
                    <span className="text-gray-600 text-xs ml-1">
                      {s.session_id.slice(8, 13)}
                    </span>
                    {s.origin && (
                      <span className="ml-2 text-xs text-purple-400 font-medium">
                        [{s.origin}]
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-gray-400">
                    {new Date(s.created_at).toLocaleString()}
                  </td>
                  <td className="px-4 py-3 text-gray-300">{s.episode_count}</td>
                  <td className="px-4 py-3">
                    <span
                      className={`font-medium ${STATUS_COLORS[s.status] ?? "text-gray-400"}`}
                    >
                      {s.status}
                    </span>
                  </td>
                  <td
                    className="px-4 py-3 text-right"
                    onClick={(e) => e.stopPropagation()}
                  >
                    <button
                      disabled={!!actionId}
                      onClick={() => openNameModal(s.session_id, "fork")}
                      className="mr-1 px-2 py-1 text-xs rounded border border-gray-700 hover:bg-gray-800 disabled:opacity-40"
                    >
                      {actionId === s.session_id + "-fork" ? "Forking…" : "Fork"}
                    </button>
                    <button
                      disabled={!!actionId}
                      onClick={() => openNameModal(s.session_id, "derive")}
                      className="mr-1 px-2 py-1 text-xs rounded border border-purple-800 text-purple-400 hover:bg-purple-900/30 disabled:opacity-40"
                    >
                      {actionId === s.session_id + "-derive" ? "Deriving…" : "Derive"}
                    </button>
                    <button
                      disabled={!!actionId}
                      onClick={() => void handleDelete(s.session_id)}
                      className="px-2 py-1 text-xs rounded border border-red-800 text-red-400 hover:bg-red-900/30 disabled:opacity-40"
                    >
                      {actionId === s.session_id + "-delete" ? "Deleting…" : "Delete"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {showCreate && (
        <CreateSessionModal
          onClose={() => setShowCreate(false)}
          onCreate={handleCreate}
        />
      )}

      {/* Name modal for fork / derive */}
      {nameModal && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70"
          onClick={() => setNameModal(null)}
        >
          <div
            className="bg-gray-900 border border-gray-700 rounded-xl shadow-2xl w-full max-w-sm mx-4 p-5"
            onClick={(e) => e.stopPropagation()}
          >
            <h2 className="text-sm font-semibold text-gray-200 mb-1 capitalize">
              {nameModal.mode} session
            </h2>
            <p className="text-xs text-gray-500 mb-4">
              {nameModal.mode === "fork"
                ? "Copies all current run state into a new session."
                : "Inherits the config and starts fresh from the initial state."}
              {" "}Leave name blank for an auto-generated ID.
            </p>
            <label className="block text-xs text-gray-400 mb-1">
              New session name (optional)
            </label>
            <input
              type="text"
              value={pendingName}
              onChange={(e) => setPendingName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void commitAction();
                if (e.key === "Escape") setNameModal(null);
              }}
              placeholder="my-ablation-run-1"
              className="w-full px-3 py-2 text-sm bg-gray-800 border border-gray-700 rounded text-gray-200 placeholder-gray-600 focus:outline-none focus:border-blue-500 mb-4"
              autoFocus
            />
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setNameModal(null)}
                className="px-3 py-1.5 text-xs rounded border border-gray-700 text-gray-400 hover:bg-gray-800"
              >
                Cancel
              </button>
              <button
                onClick={() => void commitAction()}
                className="px-3 py-1.5 text-xs rounded bg-blue-600 hover:bg-blue-500 font-medium capitalize"
              >
                {nameModal.mode}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
