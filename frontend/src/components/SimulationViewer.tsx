import { useEffect, useRef, useState } from "react";
import type { WsEvent, RunStatus, RunStatusResponse } from "../api/types";
import { SimulationStream } from "../api/ws";
import { runSession, pauseSession, injectEvent, getSessionStatus } from "../api/client";

interface TurnCard {
  episode: number;
  party_id: string;
  output: string;
  proposals: Record<string, unknown>;
}

interface EpisodeGroup {
  episode: number;
  turns: TurnCard[];
  done: boolean;
}

const PARTY_COLORS: string[] = [
  "border-blue-500 bg-blue-950/30",
  "border-green-500 bg-green-950/30",
  "border-purple-500 bg-purple-950/30",
  "border-orange-500 bg-orange-950/30",
  "border-pink-500 bg-pink-950/30",
];

function partyColor(id: string, allIds: string[]): string {
  const idx = allIds.indexOf(id);
  return PARTY_COLORS[idx % PARTY_COLORS.length] ?? PARTY_COLORS[0];
}

interface Props {
  sessionId: string;
  partyIds: string[];
  onStatusChange?: (s: RunStatus) => void;
}

export function SimulationViewer({ sessionId, partyIds, onStatusChange }: Props) {
  const [groups, setGroups] = useState<EpisodeGroup[]>([]);
  const [status, setStatus] = useState<RunStatus>("idle");
  const [wsConnected, setWsConnected] = useState(false);
  const [episodes, setEpisodes] = useState(1);
  const [injectText, setInjectText] = useState("");
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const streamRef = useRef<SimulationStream | null>(null);

  useEffect(() => {
    getSessionStatus(sessionId)
      .then((r) => {
        setStatus(r.status);
        onStatusChange?.(r.status);
      })
      .catch(() => {});
  }, [sessionId, onStatusChange]);

  useEffect(() => {
    const stream = new SimulationStream(sessionId);
    streamRef.current = stream;

    const offStatus = stream.onStatus((connected) => setWsConnected(connected));
    const offEvents = stream.on((ev: WsEvent) => {
      switch (ev.type) {
        case "episode_start":
          setGroups((g) => {
            if (g.find((x) => x.episode === ev.episode)) return g;
            return [...g, { episode: ev.episode, turns: [], done: false }];
          });
          break;
        case "turn":
          setGroups((g) =>
            g.map((ep) =>
              ep.episode === ev.episode
                ? {
                    ...ep,
                    turns: [
                      ...ep.turns,
                      {
                        episode: ev.episode,
                        party_id: ev.party_id,
                        output: ev.output,
                        proposals: ev.state_update_proposals,
                      },
                    ],
                  }
                : ep,
            ),
          );
          break;
        case "episode_end":
          setGroups((g) =>
            g.map((ep) =>
              ep.episode === ev.episode ? { ...ep, done: true } : ep,
            ),
          );
          break;
        case "simulation_complete":
          setStatus("done");
          onStatusChange?.("done");
          break;
        case "error":
          setError(ev.message);
          setStatus("error");
          onStatusChange?.("error");
          break;
      }
    });

    stream.open();

    return () => {
      offStatus();
      offEvents();
      stream.close();
      streamRef.current = null;
    };
  }, [sessionId, onStatusChange]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [groups]);

  async function handleRun() {
    setBusyAction("run");
    setError(null);
    try {
      const r: RunStatusResponse = await runSession(sessionId, episodes);
      setStatus(r.status);
      onStatusChange?.(r.status);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusyAction(null);
    }
  }

  async function handlePause() {
    setBusyAction("pause");
    try {
      const r = await pauseSession(sessionId);
      setStatus(r.status);
      onStatusChange?.(r.status);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusyAction(null);
    }
  }

  async function handleInject() {
    if (!injectText.trim()) return;
    setBusyAction("inject");
    try {
      await injectEvent(sessionId, injectText);
      setInjectText("");
    } catch (e) {
      setError(String(e));
    } finally {
      setBusyAction(null);
    }
  }

  const isRunning = status === "running";

  return (
    <div className="flex flex-col h-full">
      {/* Controls bar */}
      <div className="flex items-center gap-3 px-4 py-3 border-b border-gray-800 bg-gray-900/60 flex-wrap">
        <span
          className={`w-2 h-2 rounded-full ${wsConnected ? "bg-green-500" : "bg-gray-600"}`}
          title={wsConnected ? "WebSocket connected" : "WebSocket disconnected"}
        />
        <span className="text-xs text-gray-400 mr-2">
          {isRunning
            ? "● Running"
            : status.charAt(0).toUpperCase() + status.slice(1)}
        </span>

        {!isRunning && (
          <>
            <input
              type="number"
              min={1}
              max={100}
              value={episodes}
              onChange={(e) => setEpisodes(Math.max(1, Number(e.target.value)))}
              className="w-16 bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs text-center focus:outline-none focus:ring-1 focus:ring-blue-500"
            />
            <span className="text-xs text-gray-400">episodes</span>
            <button
              disabled={!!busyAction || status === "done"}
              onClick={() => void handleRun()}
              className="px-3 py-1 text-xs rounded bg-blue-600 hover:bg-blue-500 font-medium disabled:opacity-40"
            >
              {busyAction === "run" ? "Starting…" : "▶ Run"}
            </button>
          </>
        )}

        {isRunning && (
          <button
            disabled={!!busyAction}
            onClick={() => void handlePause()}
            className="px-3 py-1 text-xs rounded bg-yellow-600 hover:bg-yellow-500 font-medium disabled:opacity-40"
          >
            {busyAction === "pause" ? "Pausing…" : "⏸ Pause"}
          </button>
        )}

        <div className="flex-1" />

        <input
          value={injectText}
          onChange={(e) => setInjectText(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && void handleInject()}
          placeholder="Inject event…"
          className="bg-gray-800 border border-gray-700 rounded px-3 py-1 text-xs w-48 focus:outline-none focus:ring-1 focus:ring-purple-500"
        />
        <button
          disabled={!injectText.trim() || !!busyAction}
          onClick={() => void handleInject()}
          className="px-3 py-1 text-xs rounded bg-purple-700 hover:bg-purple-600 font-medium disabled:opacity-40"
        >
          Inject
        </button>
      </div>

      {error && (
        <div className="px-4 py-2 bg-red-900/40 border-b border-red-700 text-red-300 text-xs">
          {error}
        </div>
      )}

      <div className="flex-1 overflow-auto px-4 py-4 space-y-6">
        {groups.length === 0 && (
          <div className="text-center py-16 text-gray-600 text-sm">
            {isRunning
              ? "Waiting for first episode…"
              : "Press ▶ Run to start the simulation."}
          </div>
        )}

        {groups.map((ep) => (
          <div key={ep.episode}>
            <div className="flex items-center gap-2 mb-3">
              <span className="text-xs font-semibold text-gray-500 uppercase tracking-wider">
                Episode {ep.episode + 1}
              </span>
              <div className="flex-1 h-px bg-gray-800" />
              {ep.done && (
                <span className="text-xs text-green-600">✓ done</span>
              )}
            </div>

            <div className="space-y-3">
              {ep.turns.map((turn, i) => (
                <div
                  key={i}
                  className={`rounded-lg border-l-2 px-4 py-3 ${partyColor(turn.party_id, partyIds)}`}
                >
                  <div className="mb-1">
                    <span className="text-xs font-bold text-gray-300 uppercase tracking-wide">
                      {turn.party_id}
                    </span>
                  </div>
                  <p className="text-sm text-gray-200 whitespace-pre-wrap leading-relaxed">
                    {turn.output}
                  </p>
                  {Object.keys(turn.proposals).length > 0 && (
                    <details className="mt-2">
                      <summary className="text-xs text-gray-500 cursor-pointer hover:text-gray-300">
                        State updates ({Object.keys(turn.proposals).length})
                      </summary>
                      <pre className="mt-1 text-xs text-gray-400 bg-gray-900 rounded p-2 overflow-auto">
                        {JSON.stringify(turn.proposals, null, 2)}
                      </pre>
                    </details>
                  )}
                </div>
              ))}
            </div>
          </div>
        ))}

        <div ref={bottomRef} />
      </div>
    </div>
  );
}
