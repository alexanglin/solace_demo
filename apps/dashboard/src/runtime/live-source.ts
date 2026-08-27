import type { DashboardSnapshot } from "../contracts/generated";
import { createDashboardSchemaRegistry } from "../contracts/schema-registry";
import { digestMatches, replayStateDigest } from "../domain/canonical";
import type { ApplicationSourceState } from "../application-shell";
import type { DashboardMode } from "../operator/proposal-decision-panel";
import { applyDashboardEventFrame } from "./dashboard-reducer";

const SNAPSHOT_SCHEMA_ID =
  "https://aerial-rescue.invalid/schemas/v1/dashboard/dashboard-snapshot.schema.json";
const EVENT_FRAME_SCHEMA_ID =
  "https://aerial-rescue.invalid/schemas/v1/dashboard/dashboard-event-frame.schema.json";
const STREAM_OVERLOADED_SCHEMA_ID =
  "https://aerial-rescue.invalid/schemas/v1/dashboard/stream-overloaded.schema.json";

export interface DashboardStreamEvent {
  readonly data?: unknown;
}

export interface DashboardEventSourcePort {
  addEventListener(type: string, listener: (event: DashboardStreamEvent) => void): void;
  close(): void;
}

export interface DashboardSourceView {
  readonly mode: DashboardMode;
  readonly snapshot?: DashboardSnapshot;
  readonly sourceState: ApplicationSourceState;
}

interface DashboardLiveSourceDependencies {
  readonly isOnline: () => boolean;
  readonly onView: (view: DashboardSourceView) => void;
  readonly openEventSource: () => DashboardEventSourcePort;
  readonly runtimeId: string;
}

export interface DashboardLiveSource {
  dispose(): void;
  whenIdle(): Promise<void>;
}

const malformedJson = Symbol("malformed-json");

function decodeEventData(event: DashboardStreamEvent): unknown {
  if (typeof event.data !== "string") {
    return malformedJson;
  }
  try {
    return JSON.parse(event.data) as unknown;
  } catch {
    return malformedJson;
  }
}

function modeFor(snapshot: DashboardSnapshot, fallback: DashboardMode): DashboardMode {
  return snapshot.currentRun?.mode ?? fallback;
}

function missionExhausted(snapshot: DashboardSnapshot): boolean {
  return snapshot.state.currentMission?.lifecycle === "EXHAUSTED";
}

export function createNativeDashboardEventSource(): DashboardEventSourcePort {
  const source = new EventSource("/api/v1/events");
  return {
    addEventListener(type, listener): void {
      source.addEventListener(type, (event) => {
        listener({ data: event instanceof MessageEvent ? event.data : undefined });
      });
    },
    close(): void {
      source.close();
    },
  };
}

export function startDashboardLiveSource(
  dependencies: DashboardLiveSourceDependencies,
): DashboardLiveSource {
  const registry = createDashboardSchemaRegistry();
  let active = true;
  let terminal = false;
  let interrupted = false;
  let mode: DashboardMode = "degradedLive";
  let snapshot: DashboardSnapshot | undefined;
  let stream: DashboardEventSourcePort | undefined;
  let processing = Promise.resolve();

  function publish(sourceState: ApplicationSourceState): void {
    dependencies.onView({
      mode,
      sourceState,
      ...(snapshot === undefined ? {} : { snapshot }),
    });
  }

  function closeStream(): void {
    stream?.close();
    stream = undefined;
  }

  function failClosed(sourceState: "contractFailure" | "staleRuntime"): void {
    terminal = true;
    closeStream();
    publish(sourceState);
  }

  function enqueue(operation: () => Promise<void> | void): void {
    processing = processing.then(async () => {
      if (!active || terminal) {
        return;
      }
      try {
        await operation();
      } catch {
        failClosed("contractFailure");
      }
    });
  }

  async function acceptSnapshot(event: DashboardStreamEvent): Promise<void> {
    const candidate = decodeEventData(event);
    if (candidate === malformedJson) {
      failClosed("contractFailure");
      return;
    }
    const validated = registry.validate(SNAPSHOT_SCHEMA_ID, candidate);
    if (!validated.ok) {
      failClosed("contractFailure");
      return;
    }
    if (validated.value.runtimeId !== dependencies.runtimeId) {
      failClosed("staleRuntime");
      return;
    }
    const stateDigest = await replayStateDigest(validated.value.state);
    const comparison = digestMatches(validated.value.digest, stateDigest);
    if (!comparison.ok || !comparison.matches) {
      failClosed("contractFailure");
      return;
    }

    snapshot = validated.value;
    mode = modeFor(snapshot, mode);
    if (missionExhausted(snapshot)) {
      publish("exhausted");
      interrupted = false;
      return;
    }
    publish(interrupted ? "recovered" : "connected");
    interrupted = false;
  }

  async function acceptFrame(event: DashboardStreamEvent): Promise<void> {
    if (snapshot === undefined) {
      failClosed("contractFailure");
      return;
    }
    const candidate = decodeEventData(event);
    if (candidate === malformedJson) {
      failClosed("contractFailure");
      return;
    }
    const validated = registry.validate(EVENT_FRAME_SCHEMA_ID, candidate);
    if (!validated.ok) {
      failClosed("contractFailure");
      return;
    }
    const result = await applyDashboardEventFrame(snapshot, validated.value);
    if (!result.ok) {
      failClosed("contractFailure");
      return;
    }
    snapshot = result.snapshot;
    publish(missionExhausted(snapshot) ? "exhausted" : "connected");
  }

  function connect(): void {
    if (!active || terminal) {
      return;
    }
    let opened: DashboardEventSourcePort;
    try {
      opened = dependencies.openEventSource();
    } catch {
      interrupted = true;
      publish(dependencies.isOnline() ? "retrying" : "offline");
      return;
    }
    stream = opened;
    opened.addEventListener("snapshot", (event) => {
      if (stream === opened) {
        enqueue(() => acceptSnapshot(event));
      }
    });
    opened.addEventListener("dashboard-event", (event) => {
      if (stream === opened) {
        enqueue(() => acceptFrame(event));
      }
    });
    opened.addEventListener("error", () => {
      if (stream === opened) {
        enqueue(() => {
          interrupted = true;
          publish(dependencies.isOnline() ? "retrying" : "offline");
        });
      }
    });
    opened.addEventListener("stream-overloaded", (event) => {
      if (stream === opened) {
        enqueue(() => {
          const candidate = decodeEventData(event);
          if (
            candidate === malformedJson ||
            !registry.validate(STREAM_OVERLOADED_SCHEMA_ID, candidate).ok
          ) {
            failClosed("contractFailure");
            return;
          }
          interrupted = true;
          publish("retrying");
          closeStream();
          connect();
        });
      }
    });
  }

  publish("loading");
  connect();

  return {
    dispose(): void {
      active = false;
      closeStream();
    },
    whenIdle(): Promise<void> {
      return processing;
    },
  };
}
