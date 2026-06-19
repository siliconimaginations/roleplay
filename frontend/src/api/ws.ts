import type { WsEvent } from "./types";
import { getApiKey } from "./client";

type Listener = (event: WsEvent) => void;
type StatusListener = (connected: boolean) => void;

export class SimulationStream {
  private ws: WebSocket | null = null;
  private listeners: Listener[] = [];
  private statusListeners: StatusListener[] = [];
  private closed = false;
  private readonly sessionId: string;

  constructor(sessionId: string) {
    this.sessionId = sessionId;
  }

  open(): void {
    if (this.ws) return;

    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const host = window.location.host;
    this.ws = new WebSocket(
      `${proto}//${host}/sessions/${this.sessionId}/stream`,
    );

    this.ws.onopen = () => {
      const key = getApiKey();
      this.ws!.send(JSON.stringify({ api_key: key }));
    };

    this.ws.onmessage = (msg) => {
      try {
        const event = JSON.parse(msg.data as string) as WsEvent;
        if (event.type === "connected") {
          this.notifyStatus(true);
        }
        this.listeners.forEach((fn) => fn(event));
      } catch {
        // ignore parse errors
      }
    };

    this.ws.onclose = () => {
      this.notifyStatus(false);
      if (!this.closed) {
        setTimeout(() => {
          if (!this.closed) {
            this.ws = null;
            this.open();
          }
        }, 2000);
      }
    };

    this.ws.onerror = () => {
      this.ws?.close();
    };
  }

  close(): void {
    this.closed = true;
    this.ws?.close();
    this.ws = null;
  }

  on(listener: Listener): () => void {
    this.listeners.push(listener);
    return () => {
      this.listeners = this.listeners.filter((l) => l !== listener);
    };
  }

  onStatus(listener: StatusListener): () => void {
    this.statusListeners.push(listener);
    return () => {
      this.statusListeners = this.statusListeners.filter((l) => l !== listener);
    };
  }

  private notifyStatus(connected: boolean): void {
    this.statusListeners.forEach((fn) => fn(connected));
  }
}
