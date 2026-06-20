import type { ReactNode } from "react";
import { useEffect, useState } from "react";
import { getApiKey, setApiKey } from "../api/client";

interface Props {
  children: ReactNode;
}

export function Shell({ children }: Props) {
  const [showSettings, setShowSettings] = useState(false);
  const [key, setKey] = useState(getApiKey());
  const [draft, setDraft] = useState(getApiKey());

  // On mount, check whether the server requires an API key.
  // If it does AND we don't have one stored, open the key modal automatically.
  useEffect(() => {
    fetch("/health")
      .then((r) => r.json())
      .then((data: { auth_required?: boolean }) => {
        if (data.auth_required && !getApiKey()) {
          setShowSettings(true);
        }
      })
      .catch(() => {
        // Server not reachable yet — don't block the UI.
      });
  }, []);

  function saveKey() {
    setApiKey(draft);
    setKey(draft);
    setShowSettings(false);
  }

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 flex flex-col">
      {/* Header */}
      <header className="border-b border-gray-800 bg-gray-900 px-6 py-3 flex items-center justify-between shrink-0">
        <div className="flex items-center gap-3">
          <span className="text-lg font-bold tracking-tight text-white">
            ⚙ Roleplay Simulator
          </span>
        </div>
        <button
          onClick={() => setShowSettings(true)}
          className="text-xs text-gray-400 hover:text-gray-200 flex items-center gap-1 border border-gray-700 rounded px-2 py-1"
        >
          {key ? (
            <>
              <span className="w-2 h-2 rounded-full bg-green-500 inline-block" />
              API Key set
            </>
          ) : (
            <>
              <span className="w-2 h-2 rounded-full bg-gray-500 inline-block" />
              API Key
            </>
          )}
        </button>
      </header>

      {/* API Key modal */}
      {showSettings && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
          <div className="bg-gray-900 border border-gray-700 rounded-xl p-6 w-full max-w-md shadow-2xl">
            <h2 className="text-lg font-semibold mb-4">API Key</h2>
            <p className="text-sm text-gray-400 mb-4">
              Set your <code className="text-green-400">ROLEPLAY_API_KEY</code>.
              Sent as the <code className="text-green-400">X-API-Key</code>{" "}
              header on every request. Stored in memory only.
            </p>
            <input
              autoFocus
              type="password"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && saveKey()}
              placeholder="sk-..."
              className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm font-mono focus:outline-none focus:ring-1 focus:ring-blue-500 mb-4"
            />
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setShowSettings(false)}
                className="px-4 py-2 text-sm rounded border border-gray-700 hover:bg-gray-800"
              >
                Cancel
              </button>
              <button
                onClick={saveKey}
                className="px-4 py-2 text-sm rounded bg-blue-600 hover:bg-blue-500 font-medium"
              >
                Save
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Main content */}
      <main className="flex-1 overflow-auto">{children}</main>
    </div>
  );
}
