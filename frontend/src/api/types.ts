// Types aligned with src/roleplay/api/schemas.py

export type RunStatus = "idle" | "running" | "paused" | "done" | "error";

export interface PartySchema {
  id: string;
  kind: string;
  name: string;
  state: Record<string, unknown>;
}

export interface SessionSummary {
  session_id: string;
  created_at: string;
  episode_count: number;
  status: RunStatus;
  origin: string | null;
  parent_session_id: string | null;
}

export interface SessionDetail {
  session_id: string;
  created_at: string;
  episode_count: number;
  status: RunStatus;
  config: Record<string, unknown>;
  parties: PartySchema[];
  environment: PartySchema | null;
}

export interface RunStatusResponse {
  session_id: string;
  status: RunStatus;
  episodes_completed: number;
  episodes_requested: number;
  error: string | null;
  goal_achieved: boolean;
  goal_status: string;
}

// WebSocket event types
export interface TurnEvent {
  type: "turn";
  episode: number;
  party_id: string;
  output: string;
  state_update_proposals: Record<string, unknown>;
}

export interface EpisodeStartEvent {
  type: "episode_start";
  episode: number;
}

export interface EpisodeEndEvent {
  type: "episode_end";
  episode: number;
  summary: string;
}

export interface SimulationCompleteEvent {
  type: "simulation_complete";
  episodes_completed: number;
}

export interface ErrorEvent {
  type: "error";
  message: string;
}

export interface ConnectedEvent {
  type: "connected";
}

export interface InjectionEvent {
  type: "injection";
  text: string;
}

export interface GoalAchievedEvent {
  type: "goal_achieved";
  status: string;
}

export type WsEvent =
  | TurnEvent
  | EpisodeStartEvent
  | EpisodeEndEvent
  | SimulationCompleteEvent
  | ErrorEvent
  | ConnectedEvent
  | InjectionEvent
  | GoalAchievedEvent;

export interface HistoryTurn {
  episode: number;
  party_id: string;
  output: string;
  state_update_proposals: Record<string, unknown>;
}

export interface HistoryEpisode {
  episode: number;
  done: boolean;
  summary: string;
  turns: HistoryTurn[];
}
