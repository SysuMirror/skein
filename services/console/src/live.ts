// 实时推送：控制面的 /internal/stream 是 SSE，用 fetch 读流以便带令牌头。
// 断流后指数退避重连；401 直接回到落地页。
import { defineStore } from "pinia";
import type { ControlEvent, Signal, Summary, Ticket, Topology } from "./api";
import { useConsoleStore } from "./store";

export interface Snapshot {
  summary: Summary;
  signals: Signal[];
  tickets: Ticket[];
  topology: Topology;
  events: ControlEvent[];
}

export type LiveState = "idle" | "connecting" | "open" | "closed";

interface Handlers {
  onState: (state: LiveState) => void;
  onSnapshot: (snapshot: Snapshot) => void;
}

export function openLiveStream(handlers: Handlers): () => void {
  let stopped = false;
  let controller: AbortController | null = null;
  let retry = 1000;
  let timer: number | undefined;

  const stop = () => {
    stopped = true;
    controller?.abort();
    if (timer !== undefined) window.clearTimeout(timer);
  };

  const schedule = () => {
    if (stopped) return;
    timer = window.setTimeout(() => void connect(), retry);
    retry = Math.min(retry * 2, 15000);
  };

  function handle(chunk: string) {
    let event = "message";
    const data: string[] = [];
    for (const line of chunk.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) data.push(line.slice(5).trim());
    }
    if (event !== "snapshot" || data.length === 0) return;
    try {
      handlers.onSnapshot(JSON.parse(data.join("\n")) as Snapshot);
    } catch {
      // 半帧就丢弃，下一帧会补齐
    }
  }

  async function consume(body: ReadableStream<Uint8Array>) {
    const reader = body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (!stopped) {
      const { value, done } = await reader.read();
      if (done) return;
      buffer += decoder.decode(value, { stream: true });
      let cut = buffer.indexOf("\n\n");
      while (cut >= 0) {
        handle(buffer.slice(0, cut));
        buffer = buffer.slice(cut + 2);
        cut = buffer.indexOf("\n\n");
      }
    }
  }

  async function connect() {
    if (stopped) return;
    controller = new AbortController();
    handlers.onState("connecting");
    try {
      const response = await fetch("/internal/stream", {
        // 身份来自同站 Cookie（HttpOnly），不再往前端存任何令牌
        headers: { Accept: "text/event-stream" },
        signal: controller.signal,
        cache: "no-store",
        credentials: "same-origin",
      });
      if (response.status === 401) {
        useConsoleStore().clearConnection();
        stopped = true;
        handlers.onState("closed");
        return;
      }
      if (!response.ok || !response.body) throw new Error(`HTTP ${response.status}`);
      handlers.onState("open");
      retry = 1000;
      await consume(response.body);
    } catch {
      // 断流或网络错误，统一走重连
    }
    if (!stopped) {
      handlers.onState("closed");
      schedule();
    }
  }

  void connect();
  return stop;
}

let closer: (() => void) | null = null;

export const useLiveStore = defineStore("live", {
  state: () => ({
    state: "idle" as LiveState,
    snapshot: null as Snapshot | null,
    updatedAt: 0,
  }),
  actions: {
    start() {
      if (closer) return;
      closer = openLiveStream({
        onState: (state) => {
          this.state = state;
        },
        onSnapshot: (snapshot) => {
          this.snapshot = snapshot;
          this.updatedAt = Date.now();
          this.state = "open";
        },
      });
    },
    stop() {
      closer?.();
      closer = null;
      this.state = "idle";
    },
  },
});
