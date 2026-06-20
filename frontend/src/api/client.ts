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

export interface ValidationResult {
  valid: boolean;
  errors: string[];
}

export interface GenerateResult { yaml: string; }

export const generateSession = async (prompt: string): Promise<GenerateResult> => {
  const headers: Record<string, string> = { "Content-Type": "text/plain" };
  if (_apiKey) headers["X-API-Key"] = _apiKey;
  const res = await fetch("/sessions/generate", { method: "POST", headers, body: prompt });
  if (res.ok) {
    return res.json() as Promise<GenerateResult>;
  }
  const text = await res.text().catch(() => res.statusText);
  throw new ApiError(res.status, text);
};

export const validateSession = async (yaml: string): Promise<ValidationResult> => {
  const headers: Record<string, string> = { "Content-Type": "text/plain" };
  if (_apiKey) headers["X-API-Key"] = _apiKey;
  const res = await fetch("/sessions/validate", { method: "POST", headers, body: yaml });
  // 200 = valid, 422 = invalid — both are expected results, not errors.
  if (res.status === 200 || res.status === 422) {
    return res.json() as Promise<ValidationResult>;
  }
  const text = await res.text().catch(() => res.statusText);
  throw new ApiError(res.status, text);
};

export const deleteSession = (id: string): Promise<void> =>
  apiFetch(`/sessions/${id}`, { method: "DELETE" });

export const forkSession = (id: string): Promise<SessionSummary> =>
  apiFetch(`/sessions/${id}/fork`, { method: "POST" });

// Simulation control
export const runSession = (
  id: string,
  episodes: number,
): Promise<RunStatusResponse> =>
  apiFetch(`/sessions/${id}/run?episodes=${episodes}`, { method: "POST" });

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
  return apiFetch<import("./types").HistoryEpisode[]>(`/sessions/${sessionId}/history`);
}
