import type {
  DashboardResetResponse,
  DashboardSnapshot,
  DashboardStartResponse,
} from "../contracts/generated";
import { parseDashboardBootstrap } from "../contracts/bootstrap";
import type {
  DashboardEventSource,
  DashboardSourceConsumer,
  DashboardSourceInput,
} from "./event-source";
import { LiveSseSource } from "./live-sse-source";
import { ReplayBundleSource } from "./replay-bundle-source";
import { boundedUtf8Body } from "./response-body";

const MAXIMUM_DOCUMENT_BYTES = 512 * 1024;

export type ProductionFetch = (input: string, init: RequestInit) => Promise<Response>;

export interface SourceSessionPort {
  acceptReplayResume(sessionId: string): boolean;
  anchorRuntime(runtimeId: string): void;
  expectLiveRun(missionId: string, runId: string): void;
  replaceSource(
    source: DashboardEventSource,
    expectedRunMode?: "degradedLive" | "replay" | null,
  ): void;
}

export interface ProductionDashboardRuntimeOptions {
  readonly bootstrap: DashboardSourceInput;
  readonly consumeBoundary: DashboardSourceConsumer;
  readonly fetcher?: ProductionFetch;
  readonly liveSourceFactory?: () => DashboardEventSource;
  readonly replaySourceFactory?: (sessionId: string) => DashboardEventSource;
  readonly session: SourceSessionPort;
}

export interface ProductionRuntimePort {
  acceptedMutation(response: DashboardResetResponse | DashboardStartResponse): void;
  dispose(): void;
  observeSnapshotRun(currentRun: DashboardSnapshot["currentRun"]): void;
  resnapshot(): void;
  selectMode(mode: "degradedLive" | "replay"): Promise<void>;
  start(): Promise<void>;
}

function browserFetch(input: string, init: RequestInit): Promise<Response> {
  return fetch(input, init);
}

function defaultLiveSource(): DashboardEventSource {
  return new LiveSseSource({ url: "/api/v1/events" });
}

function defaultReplaySource(sessionId: string): DashboardEventSource {
  return new ReplayBundleSource({
    url: `/api/v1/replays/${encodeURIComponent(sessionId)}`,
  });
}

function boundaryInput(name: string, raw: string): DashboardSourceInput {
  return { channel: "http-response", name, raw };
}

/** Removes the process bearer from the DOM before any asynchronous browser work begins. */
export function readProductionBootstrap(documentRoot: Document): DashboardSourceInput {
  const candidates = Array.from(
    documentRoot.querySelectorAll<HTMLElement>('[id="dashboard-bootstrap"]'),
  );
  const only = candidates.length === 1 ? candidates[0] : undefined;
  const raw =
    only instanceof HTMLScriptElement && only.type === "application/json" ? only.textContent : "";
  for (const candidate of candidates) candidate.remove();
  return { channel: "bootstrap", name: "bootstrap", raw };
}

/** Owns production bootstrap/GET composition and explicit source replacement boundaries. */
export class ProductionDashboardRuntime implements ProductionRuntimePort {
  private bootstrap: DashboardSourceInput;
  private readonly consumeBoundary: DashboardSourceConsumer;
  private readonly fetcher: ProductionFetch;
  private readonly liveSourceFactory: () => DashboardEventSource;
  private readonly replaySourceFactory: (sessionId: string) => DashboardEventSource;
  private readonly session: SourceSessionPort;
  private readonly requests = new Set<AbortController>();
  private disposed = false;
  private initialSnapshotObserved = false;
  private mode: "degradedLive" | "replay" = "degradedLive";
  private started = false;

  constructor(options: ProductionDashboardRuntimeOptions) {
    this.bootstrap = options.bootstrap;
    this.consumeBoundary = options.consumeBoundary;
    this.fetcher = options.fetcher ?? browserFetch;
    this.liveSourceFactory = options.liveSourceFactory ?? defaultLiveSource;
    this.replaySourceFactory = options.replaySourceFactory ?? defaultReplaySource;
    this.session = options.session;
  }

  async start(): Promise<void> {
    if (this.disposed || this.started) return;
    this.started = true;
    const bootstrap = this.bootstrap;
    this.bootstrap = { ...bootstrap, raw: "" };
    const parsed = parseDashboardBootstrap(bootstrap.raw);
    if (parsed.ok) this.session.anchorRuntime(parsed.value.runtimeId);
    await this.consumeBoundary(bootstrap);
    if (this.stopRequested() || !parsed.ok) return;
    this.resnapshot();
    await Promise.all([this.loadReadiness("degradedLive"), this.loadCatalog()]);
  }

  async selectMode(mode: "degradedLive" | "replay"): Promise<void> {
    if (this.disposed) return;
    this.initialSnapshotObserved = true;
    this.mode = mode;
    if (mode === "degradedLive") this.resnapshot();
    await this.loadReadiness(mode);
  }

  acceptedMutation(response: DashboardResetResponse | DashboardStartResponse): void {
    if (this.disposed) return;
    this.initialSnapshotObserved = true;
    this.mode = response.mode;
    if (response.mode === "replay") {
      this.session.replaceSource(this.replaySourceFactory(response.sessionId), "replay");
    } else {
      this.session.expectLiveRun(response.missionId, response.runId);
      this.resnapshot();
    }
  }

  observeSnapshotRun(currentRun: DashboardSnapshot["currentRun"]): void {
    if (this.disposed || this.initialSnapshotObserved) return;
    this.initialSnapshotObserved = true;
    if (currentRun?.mode !== "replay") return;
    if (!this.session.acceptReplayResume(currentRun.sessionId)) return;
    this.mode = "replay";
    this.session.replaceSource(this.replaySourceFactory(currentRun.sessionId), "replay");
    void this.loadReadiness("replay");
  }

  resnapshot(): void {
    if (!this.disposed && this.mode === "degradedLive") {
      this.session.replaceSource(this.liveSourceFactory(), "degradedLive");
    }
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.bootstrap = { ...this.bootstrap, raw: "" };
    for (const request of this.requests) request.abort();
    this.requests.clear();
  }

  private loadCatalog(): Promise<void> {
    return this.loadDocument("/api/v1/scenarios", "scenario-catalog");
  }

  private stopRequested(): boolean {
    return this.disposed;
  }

  private loadReadiness(mode: "degradedLive" | "replay"): Promise<void> {
    return this.loadDocument(
      `/api/v1/readiness?mode=${mode}`,
      "readiness",
      [200, 503],
      () => this.mode === mode,
    );
  }

  private async loadDocument(
    url: string,
    name: string,
    acceptedStatuses: readonly number[] = [200],
    isCurrent: () => boolean = () => true,
  ): Promise<void> {
    const abort = new AbortController();
    this.requests.add(abort);
    let raw = "";
    try {
      const response = await this.fetcher(url, {
        cache: "no-store",
        credentials: "same-origin",
        headers: { Accept: "application/json" },
        method: "GET",
        signal: abort.signal,
      });
      const candidate = await boundedUtf8Body(response, MAXIMUM_DOCUMENT_BYTES);
      if (acceptedStatuses.includes(response.status) && candidate !== null) raw = candidate;
    } catch {
      raw = "";
    } finally {
      this.requests.delete(abort);
    }
    if (!this.disposed && isCurrent()) await this.consumeBoundary(boundaryInput(name, raw));
  }
}
