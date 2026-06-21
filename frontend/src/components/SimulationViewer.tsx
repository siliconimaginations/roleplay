import { useEffect, useRef, useState } from "react";
import type { WsEvent, RunStatus, RunStatusResponse } from "../api/types";
import { SimulationStream } from "../api/ws";
import { runSession, pauseSession, injectEvent, getSessionStatus, getSessionHistory, getSessionYaml, getSessionExport } from "../api/client";

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
  summary: string;
}

/** A past injection tied to the episode that consumed it. */
interface InjectionRecord {
  text: string;
  /** Episode index that consumed this injection (set on episode_start). */
  beforeEpisode: number;
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
  // "summary" = one-line per episode; "detail" = full turn dialog + summary
  const [viewMode, setViewMode] = useState<"summary" | "detail">("detail");
  const [goalAchieved, setGoalAchieved] = useState(false);
  const [goalStatus, setGoalStatus] = useState("");

  // Injections submitted but not yet consumed by an episode
  const [pendingInjections, setPendingInjections] = useState<string[]>([]);
  // Permanent log: injections that were consumed, keyed to the consuming episode
  const [injectionLog, setInjectionLog] = useState<InjectionRecord[]>([]);

  // YAML config modal
  const [showYaml, setShowYaml] = useState(false);
  const [yamlContent, setYamlContent] = useState<string | null>(null);
  const [yamlLoading, setYamlLoading] = useState(false);

  const bottomRef = useRef<HTMLDivElement>(null);
  const streamRef = useRef<SimulationStream | null>(null);

  useEffect(() => {
    getSessionStatus(sessionId)
      .then((r) => {
        setStatus(r.status);
        onStatusChange?.(r.status);
        if (r.goal_achieved) {
          setGoalAchieved(true);
          setGoalStatus(r.goal_status ?? "");
        }
      })
      .catch(() => {});
  }, [sessionId, onStatusChange]);

  // Load persisted history on mount so returning to a completed session shows past turns.
  useEffect(() => {
    getSessionHistory(sessionId)
      .then((eps) => {
        if (eps.length > 0) {
          setGroups(
            eps.map((ep) => ({
              episode: ep.episode,
              done: ep.done,
              summary: ep.summary ?? "",
              turns: ep.turns.map((t) => ({
                episode: t.episode,
                party_id: t.party_id,
                output: t.output,
                proposals: t.state_update_proposals,
              })),
            })),
          );
        }
      })
      .catch(() => {});
  }, [sessionId]);

  useEffect(() => {
    const stream = new SimulationStream(sessionId);
    streamRef.current = stream;

    const offStatus = stream.onStatus((connected) => setWsConnected(connected));
    const offEvents = stream.on((ev: WsEvent) => {
      switch (ev.type) {
        case "injection":
          // Track as pending until the next episode_start consumes it.
          setPendingInjections((prev) => [...prev, ev.text]);
          break;
        case "connected":
          // WS is live — backfill any episodes that completed before we connected
          // (incremental DB persistence means history is up-to-date mid-run).
          getSessionHistory(sessionId)
            .then((eps) => {
              if (eps.length > 0) {
                setGroups((prev) => {
                  const known = new Set(prev.map((g) => g.episode));
                  const missing = eps
                    .filter((ep) => !known.has(ep.episode))
                    .map((ep) => ({
                      episode: ep.episode,
                      done: ep.done,
                      summary: ep.summary ?? "",
                      turns: ep.turns.map((t) => ({
                        episode: t.episode,
                        party_id: t.party_id,
                        output: t.output,
                        proposals: t.state_update_proposals,
                      })),
                    }));
                  if (missing.length === 0) return prev;
                  return [...missing, ...prev].sort((a, b) => a.episode - b.episode);
                });
              }
            })
            .catch(() => {});
          break;
        case "episode_start":
          // Move all current pending injections into the permanent log,
          // tied to this episode so they render in the right place.
          setPendingInjections((prev) => {
            if (prev.length > 0) {
              const records: InjectionRecord[] = prev.map((text) => ({
                text,
                beforeEpisode: ev.episode,
              }));
              setInjectionLog((log) => [...log, ...records]);
            }
            return [];
          });
          setGroups((g) => {
            if (g.find((x) => x.episode === ev.episode)) return g;
            return [...g, { episode: ev.episode, turns: [], done: false, summary: "" }];
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
              ep.episode === ev.episode
                ? { ...ep, done: true, summary: ev.summary ?? "" }
                : ep,
            ),
          );
          break;
        case "goal_achieved":
          setGoalAchieved(true);
          setGoalStatus((ev as unknown as { status: string }).status ?? "");
          break;
        case "simulation_complete":
          setStatus("done");
          onStatusChange?.("done");
          // Reload history to guarantee all turns + summaries are visible.
          getSessionHistory(sessionId)
            .then((eps) => {
              if (eps.length > 0) {
                setGroups(
                  eps.map((ep) => ({
                    episode: ep.episode,
                    done: ep.done,
                    summary: ep.summary ?? "",
                    turns: ep.turns.map((t) => ({
                      episode: t.episode,
                      party_id: t.party_id,
                      output: t.output,
                      proposals: t.state_update_proposals,
                    })),
                  })),
                );
              }
            })
            .catch(() => {});
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
    setError(null);
    try {
      await injectEvent(sessionId, injectText);
      setInjectText("");
    } catch (e) {
      setError(String(e));
    } finally {
      setBusyAction(null);
    }
  }

  async function handleShowYaml() {
    setShowYaml(true);
    if (yamlContent !== null) return; // already fetched
    setYamlLoading(true);
    try {
      const r = await getSessionYaml(sessionId);
      setYamlContent(r.yaml);
    } catch (e) {
      setYamlContent(`# Error loading config\n# ${String(e)}`);
    } finally {
      setYamlLoading(false);
    }
  }

  async function handleExport() {
    setBusyAction("export");
    setError(null);
    try {
      const data = await getSessionExport(sessionId);
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `session-${sessionId}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusyAction(null);
    }
  }

  const isRunning = status === "running";
  // Enable inject for any state where a future episode might consume it.
  const canInject = status === "idle" || status === "running" || status === "paused";

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
            <span className="text-xs text-gray-400">more episodes</span>
            <button
              disabled={!!busyAction}
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

        {/* Show config button */}
        <button
          onClick={() => void handleShowYaml()}
          className="px-3 py-1 text-xs rounded bg-gray-700 hover:bg-gray-600 font-medium text-gray-300"
          title="View scenario YAML config"
        >
          ⚙ Config
        </button>

        {/* Export history button */}
        <button
          disabled={!!busyAction || groups.length === 0}
          onClick={() => void handleExport()}
          className="px-3 py-1 text-xs rounded bg-gray-700 hover:bg-gray-600 font-medium text-gray-300 disabled:opacity-40"
          title="Download session history as JSON"
        >
          {busyAction === "export" ? "Exporting…" : "⬇ Export"}
        </button>

        {/* View mode toggle */}
        <div className="flex items-center gap-1 bg-gray-800 rounded p-0.5">
          <button
            onClick={() => setViewMode("summary")}
            className={`px-2 py-1 text-xs rounded transition-colors ${
              viewMode === "summary"
                ? "bg-gray-600 text-white"
                : "text-gray-400 hover:text-gray-200"
            }`}
            title="Summary view — one line per episode"
          >
            Summary
          </button>
          <button
            onClick={() => setViewMode("detail")}
            className={`px-2 py-1 text-xs rounded transition-colors ${
              viewMode === "detail"
                ? "bg-gray-600 text-white"
                : "text-gray-400 hover:text-gray-200"
            }`}
            title="Detail view — full dialog"
          >
            Detail
          </button>
        </div>

        <input
          value={injectText}
          onChange={(e) => setInjectText(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && void handleInject()}
          placeholder="Inject event…"
          className="bg-gray-800 border border-gray-700 rounded px-3 py-1 text-xs w-48 focus:outline-none focus:ring-1 focus:ring-purple-500"
        />
        <button
          disabled={!injectText.trim() || !!busyAction || !canInject}
          onClick={() => void handleInject()}
          className="px-3 py-1 text-xs rounded bg-purple-700 hover:bg-purple-600 font-medium disabled:opacity-40"
          title={!canInject ? "Session is done or errored" : "Inject a narrative event"}
        >
          Inject
        </button>
      </div>

      {error && (
        <div className="px-4 py-2 bg-red-900/40 border-b border-red-700 text-red-300 text-xs">
          {error}
        </div>
      )}

      {goalAchieved && (
        <div className="px-4 py-3 bg-green-900/40 border-b border-green-600 text-green-300 text-sm flex items-start gap-2">
          <span className="text-green-400 text-base leading-tight">✓</span>
          <div>
            <span className="font-semibold">Goal achieved — </span>
            <span>{goalStatus.replace(/^GOAL MET:\s*/i, "")}</span>
          </div>
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

        {viewMode === "summary" ? (
          /* ── Summary view ── */
          <div className="space-y-2">
            {groups.map((ep) => (
              <div key={ep.episode}>
                {/* Injection markers that precede this episode */}
                {injectionLog
                  .filter((r) => r.beforeEpisode === ep.episode)
                  .map((r, i) => (
                    <InjectionMarker key={`inj-${ep.episode}-${i}`} text={r.text} />
                  ))}
                <div className="flex items-start gap-3 px-3 py-2.5 rounded-lg bg-gray-900/50 border border-gray-800">
                  <span className="text-xs font-semibold text-gray-500 uppercase tracking-wider whitespace-nowrap pt-0.5">
                    Ep {ep.episode + 1}
                  </span>
                  <span className="text-sm text-gray-300 leading-relaxed flex-1">
                    {ep.done
                      ? ep.summary || (
                          <span className="text-gray-600 italic">No summary available.</span>
                        )
                      : <span className="text-gray-600 italic">Running…</span>}
                  </span>
                  {ep.done && (
                    <span className="text-xs text-green-700 pt-0.5">✓</span>
                  )}
                </div>
              </div>
            ))}
          </div>
        ) : (
          /* ── Detail view ── */
          <>
            {groups.map((ep) => (
              <div key={ep.episode}>
                {/* Injection markers that precede this episode */}
                {injectionLog
                  .filter((r) => r.beforeEpisode === ep.episode)
                  .map((r, i) => (
                    <InjectionMarker key={`inj-${ep.episode}-${i}`} text={r.text} />
                  ))}

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

                  {ep.done && ep.summary && (
                    <div className="mt-2 px-3 py-2 rounded bg-gray-800/60 border border-gray-700/50">
                      <span className="text-xs font-semibold text-gray-500 uppercase tracking-wider mr-2">
                        Summary
                      </span>
                      <span className="text-xs text-gray-400 leading-relaxed">
                        {ep.summary}
                      </span>
                    </div>
                  )}
                </div>
              </div>
            ))}
          </>
        )}

        {/* Pending injections — submitted but not yet consumed by an episode */}
        {pendingInjections.length > 0 && (
          <div className="space-y-2">
            {pendingInjections.map((text, i) => (
              <InjectionMarker key={i} text={text} pending />
            ))}
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* YAML config modal */}
      {showYaml && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70"
          onClick={() => setShowYaml(false)}
        >
          <div
            className="relative bg-gray-900 border border-gray-700 rounded-xl shadow-2xl w-full max-w-2xl max-h-[80vh] flex flex-col mx-4"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between px-4 py-3 border-b border-gray-800">
              <span className="text-sm font-semibold text-gray-300">Scenario Config</span>
              <button
                onClick={() => setShowYaml(false)}
                className="text-gray-500 hover:text-gray-300 text-lg leading-none"
              >
                ✕
              </button>
            </div>
            <div className="flex-1 overflow-auto p-4">
              {yamlLoading ? (
                <div className="text-xs text-gray-500 text-center py-8">Loading…</div>
              ) : (
                <pre className="text-xs text-gray-300 whitespace-pre-wrap font-mono leading-relaxed">
                  {yamlContent}
                </pre>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/** Amber banner shown for injected narrative events. */
function InjectionMarker({ text, pending = false }: { text: string; pending?: boolean }) {
  return (
    <div className="flex items-start gap-2 px-3 py-2 rounded-lg border border-amber-700/60 bg-amber-950/30 mb-2">
      <span className="text-xs font-semibold text-amber-500 uppercase tracking-wider whitespace-nowrap pt-0.5">
        {pending ? "⏳ Injected" : "↳ Injected"}
      </span>
      <span className="text-xs text-amber-300/90 leading-relaxed flex-1">{text}</span>
    </div>
  );
}
