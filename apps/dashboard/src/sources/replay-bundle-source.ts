import type {
  DashboardEventSource,
  DashboardSourceConsumer,
  DashboardSourceInput,
  DashboardSourceSubscription,
} from "./event-source";
import { boundedUtf8Body } from "./response-body";

const MAXIMUM_REPLAY_BYTES = 1024 * 1024;

export type ReplayFetch = (input: string, init: RequestInit) => Promise<Response>;

export interface ReplayBundleSourceOptions {
  readonly fetcher?: ReplayFetch;
  readonly url: string;
}

function browserFetch(input: string, init: RequestInit): Promise<Response> {
  return fetch(input, init);
}

function replayInput(raw: string): DashboardSourceInput {
  return { channel: "replay-bundle", name: "validated-replay-bundle", raw };
}

/** One-shot same-origin source for the validator's exact replay response. */
export class ReplayBundleSource implements DashboardEventSource {
  private readonly fetcher: ReplayFetch;
  private readonly url: string;

  constructor(options: ReplayBundleSourceOptions) {
    this.fetcher = options.fetcher ?? browserFetch;
    this.url = options.url;
  }

  open(consumer: DashboardSourceConsumer): DashboardSourceSubscription {
    const abort = new AbortController();
    let disposed = false;
    const deliver = async (raw: string): Promise<void> => {
      if (!disposed) await consumer(replayInput(raw));
    };
    void (async () => {
      try {
        const response = await this.fetcher(this.url, {
          cache: "no-store",
          credentials: "same-origin",
          headers: { Accept: "application/json" },
          method: "GET",
          signal: abort.signal,
        });
        if (abort.signal.aborted) return;
        const raw = await boundedUtf8Body(response, MAXIMUM_REPLAY_BYTES);
        await deliver(response.status === 200 && raw !== null ? raw : "");
      } catch {
        await deliver("");
      }
    })().catch(() => undefined);
    return {
      dispose: () => {
        if (disposed) return;
        disposed = true;
        abort.abort();
      },
    };
  }
}
