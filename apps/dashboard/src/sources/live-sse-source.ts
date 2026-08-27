import type {
  DashboardEventSource,
  DashboardSourceConsumer,
  DashboardSourceInput,
  DashboardSourceSubscription,
} from "./event-source";

const LIVE_FRAME_NAMES = ["snapshot", "dashboard-event", "stream-overloaded"] as const;
export const DASHBOARD_TRANSPORT_OFFLINE_MILLISECONDS = 6_000;

export interface LiveEventStream {
  readonly readyState?: number;
  addEventListener(name: string, listener: (event: Event) => void): void;
  removeEventListener(name: string, listener: (event: Event) => void): void;
  close(): void;
}

export type LiveEventStreamFactory = (url: string) => LiveEventStream;

const CLOSED_EVENT_STREAM_STATE = 2;

export interface LiveSseSourceOptions {
  readonly factory?: LiveEventStreamFactory;
  readonly url: string;
}

function defaultEventStreamFactory(url: string): LiveEventStream {
  return new EventSource(url);
}

function signalInput(
  signal: "connecting" | "disconnected" | "offline" | "recovered",
): DashboardSourceInput {
  return {
    channel: "source-signal",
    name: signal,
    raw: JSON.stringify({
      signalVersion: "dashboard-source-signal/v1",
      signal,
    }),
  };
}

function frameInput(name: (typeof LIVE_FRAME_NAMES)[number], event: Event): DashboardSourceInput {
  const message = event instanceof MessageEvent ? event : null;
  return {
    channel: "sse-frame",
    lastEventId: message?.lastEventId ?? "",
    name,
    raw: message !== null && typeof message.data === "string" ? message.data : "",
  };
}

/** Browser EventSource adapter using only the accepted named data frames. */
export class LiveSseSource implements DashboardEventSource {
  private readonly factory: LiveEventStreamFactory;
  private readonly url: string;

  constructor(options: LiveSseSourceOptions) {
    this.factory = options.factory ?? defaultEventStreamFactory;
    this.url = options.url;
  }

  open(consumer: DashboardSourceConsumer): DashboardSourceSubscription {
    let stream: LiveEventStream | null = null;
    let disposed = false;
    let disconnected = false;
    let offlineReported = false;
    let offlineTimer: ReturnType<typeof globalThis.setTimeout> | null = null;
    const cancelOfflineTimer = (): void => {
      if (offlineTimer !== null) {
        globalThis.clearTimeout(offlineTimer);
        offlineTimer = null;
      }
    };
    const deliver = (input: DashboardSourceInput): void => {
      if (!disposed) {
        void consumer(input);
      }
    };
    const listeners = new Map<string, (event: Event) => void>();
    const detach = (held: LiveEventStream): void => {
      for (const [name, listener] of listeners) {
        held.removeEventListener(name, listener);
      }
      held.close();
    };
    const attach = (): void => {
      const replacement = this.factory(this.url);
      stream = replacement;
      for (const [name, listener] of listeners) {
        replacement.addEventListener(name, listener);
      }
    };
    const scheduleOfflineTransition = (): void => {
      if (offlineTimer !== null) return;
      offlineTimer = globalThis.setTimeout(() => {
        offlineTimer = null;
        if (disposed || !disconnected) return;
        if (!offlineReported) {
          offlineReported = true;
          deliver(signalInput("offline"));
        }
        const failed = stream;
        if (failed?.readyState === CLOSED_EVENT_STREAM_STATE) {
          detach(failed);
          attach();
        }
      }, DASHBOARD_TRANSPORT_OFFLINE_MILLISECONDS);
    };
    for (const frameName of LIVE_FRAME_NAMES) {
      listeners.set(frameName, (event) => {
        deliver(frameInput(frameName, event));
      });
    }
    listeners.set("error", () => {
      if (!disconnected) {
        disconnected = true;
        deliver(signalInput("disconnected"));
      }
      scheduleOfflineTransition();
    });
    listeners.set("open", () => {
      if (disconnected) {
        disconnected = false;
        offlineReported = false;
        cancelOfflineTimer();
        deliver(signalInput("recovered"));
      }
    });
    attach();
    deliver(signalInput("connecting"));

    return {
      dispose: () => {
        if (disposed) {
          return;
        }
        disposed = true;
        cancelOfflineTimer();
        if (stream !== null) detach(stream);
        stream = null;
      },
    };
  }
}
