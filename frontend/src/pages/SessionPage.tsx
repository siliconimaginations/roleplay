import { useCallback, useEffect, useState } from "react";
import type { RunStatus, SessionDetail } from "../api/types";
import { deleteSession, deriveSession, forkSession, getSession } from "../api/client";
import { SimulationViewer } from "../components/SimulationViewer";
import { SessionInspector } from "../components/SessionInspector";

const STATUS_COLORS: Record<string, string> = {
  idle: "bg-gray-700 text-gray-300",
  running: "bg-blue-700 text-blue-100",
  paused: "bg-yellow-700 text-yellow-100",
  done: "bg-green-700 text-green-100",
  error: "bg-red-700 text-red-100",
};

interface Props {
  sessionId: string;
  onBack: () => void;
}

type ActionMode = "fork" | "derive";

export function SessionPage({ sessionId, onBack }: Props) {
  const [session, setSession] = useState<SessionDetail | null>(null);
  const [tab, setTab] = useState<"stream" | "inspector">("stream");
  const [status, setStatus] = useState<RunStatus>("idle");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionBusy, setActionBusy] = useState<string | null>(null);
  const [nameModal, setNameModal] = useState<ActionMode | null>(null);
  const [pendingName, setPendingName] = useState("");

  const reload = useCallback(async () => {
    try {
      const s = await getSession(sessionId);
      setSession(s);
      setStatus(s.status);
      setLoadError(null);
    } catch (e) {
      setLoadError(String(e));
    }
  }, [sessionId]);

  useEffect(() => {
    void reload();
    const t = setInterval(() => void reload(), 8000);
    return () => clearInterval(t);
  }, [reload]);

  function handleStatusChange(s: RunStatus) {
    setStatus(s);
  }

  function openNameModal(mode: ActionMode) {
    setNameModal(mode);
    setPendingName("");
  }

  async function commitAction() {
    if (!nameModal) return;
    const customId = pendingName.trim() || undefined;
    setNameModal(null);
    setActionBusy(nameModal);
    try {
      if (nameModal === "fork") {
        await forkSession(sessionId, customId);
      } else {
        await deriveSession(sessionId, customId);
      }
      onBack();
    } catch (e) {
      alert(String(e));
      setActionBusy(null);
    }
  }

  async function handleDelete() {
    if (!confirm("Delete this session? This cannot be undone.")) return;
    setActionBusy("delete");
    try {
      await deleteSession(sessionId);
      onBack();
    } catch (e) {
      alert(String(e));
      setActionBusy(null);
    }
  }

  if (loadError) {
    return (
      <div className="flex flex-col items-center justify-center h-96 gap-4">
        <p className="text-red-400 text-sm">{loadError}</p>
        <button onClick={onBack} className="text-sm underline text-gray-400">
          ← Back to sessions
        </button>
      </div>
    );
  }

  if (!session) {
    return (
      <div className="flex items-center justify-center h-64 text-gray-500">
        Loading…
      </div>
    );
  }

  const partyIds = session.parties.map((p) => p.id);

  return (
    <div className="flex flex-col h-[calc(100vh-52px)]">
      {/* Sub-header */}
      <div className="flex items-center gap-3 px-6 py-3 border-b border-gray-800 bg-gray-900 shrink-0 flex-wrap">
        <button
          onClick={onBack}
          className="text-gray-400 hover:text-gray-200 text-sm"
        >
          ← Sessions
        </button>
        <span className="text-gray-600">/</span>
        <span className="font-mono text-sm text-gray-300 select-all">
          {sessionId}
        </span>
        <span
          className={`text-xs px-2 py-0.5 rounded font-medium ${STATUS_COLORS[status] ?? "bg-gray-700 text-gray-300"}`}
        >
          {status}
        </span>
        <div className="flex-1" />

        {/* Tab switcher */}
        <div className="flex border border-gray-700 rounded overflow-hidden text-xs">
          <button
            onClick={() => setTab("stream")}
            className={`px-3 py-1 ${tab === "stream" ? "bg-gray-700 text-white" : "text-gray-400 hover:bg-gray-800"}`}
          >
            Live Stream
          </button>
          <button
            onClick={() => setTab("inspector")}
            className={`px-3 py-1 ${tab === "inspector" ? "bg-gray-700 text-white" : "text-gray-400 hover:bg-gray-800"}`}
          >
            Inspector
          </button>
        </div>

        <button
          disabled={!!actionBusy}
          onClick={() => openNameModal("fork")}
          className="px-2 py-1 text-xs rounded border border-gray-700 hover:bg-gray-800 disabled:opacity-40"
        >
          {actionBusy === "fork" ? "Forking…" : "Fork"}
        </button>
        <button
          disabled={!!actionBusy}
          onClick={() => openNameModal("derive")}
          className="px-2 py-1 text-xs rounded border border-purple-800 text-purple-400 hover:bg-purple-900/30 disabled:opacity-40"
        >
          {actionBusy === "derive" ? "Deriving…" : "Derive"}
        </button>
        <button
          disabled={!!actionBusy}
          onClick={() => void handleDelete()}
          className="px-2 py-1 text-xs rounded border border-red-800 text-red-400 hover:bg-red-900/30 disabled:opacity-40"
        >
          {actionBusy === "delete" ? "Deleting…" : "Delete"}
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-hidden">
        {tab === "stream" ? (
          <SimulationViewer
            sessionId={sessionId}
            partyIds={partyIds}
            onStatusChange={handleStatusChange}
          />
        ) : (
          <SessionInspector session={session} />
        )}
      </div>

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
              {nameModal} session
            </h2>
            <p className="text-xs text-gray-500 mb-4">
              {nameModal === "fork"
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
                {nameModal}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
