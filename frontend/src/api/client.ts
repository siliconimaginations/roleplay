import type {
  RunStatusResponse,
  SessionDetail,
  SessionSummary,
} from "./types";

let _apiKey = "";

export function setApiKey(key: string): void {
  _apiKey = key;
}

export function getApiKey(): string {
  return _apiKey;
}

async function apiFetch<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const headers: Record<string, string> = {
    ...(options.headers as Record<string, string>),
  };
  if (_apiKey) headers["X-API-Key"] = _apiKey;

  const res = await fetch(path, { ...options, headers });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new ApiError(res.status, text);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export class ApiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

// Sessions
export const listSessions = (): Promise<SessionSummary[]> =>
  apiFetch("/sessions");

export const getSession = (id: string): Promise<SessionDetail> =>
  apiFetch(`/sessions/${id}`);

export const createSession = (yaml: string): Promise<SessionSummary> =>
  apiFetch("/sessions", {
    method: "POST",
    headers: { "Content-Type": "text/plain" },
    body: yaml,
  });

export const deleteSession = (id: string): Promise<void> =>
  apiFetch(`/sessions/${id}`, { method: "DELETE" });

export const forkSession = (id: string): Promise<SessionSummary> =>
  apiFetch(`/sessions/${id}/fork`, { method: "POST" });

// Simulation control
export const runSession = (
  id: string,
  episodes: number,
): Promise<RunStatusResponse> =>
  apiFetch(`/sessions/${id}/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ episodes }),
  });

export const pauseSession = (id: string): Promise<RunStatusResponse> =>
  apiFetch(`/sessions/${id}/pause`, { method: "POST" });

export const getSessionStatus = (id: string): Promise<RunStatusResponse> =>
  apiFetch(`/sessions/${id}/status`);

export const injectEvent = (
  id: string,
  text: string,
): Promise<RunStatusResponse> =>
  apiFetch(`/sessions/${id}/inject`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });

export async function getSessionHistory(sessionId: string): Promise<import("./types").HistoryEpisode[]> {
  return request<import("./types").HistoryEpisode[]>(`/sessions/${sessionId}/history`);
}
