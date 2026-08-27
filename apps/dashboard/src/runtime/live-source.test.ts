import { expect, test, vi } from "vitest";

import type {
  DashboardEventFrame,
  DashboardReducedState,
  DashboardSnapshot,
  OrderedDashboardEvent,
} from "../contracts/generated";
import { replayStateDigest } from "../domain/canonical";
import {
  createNativeDashboardEventSource,
  startDashboardLiveSource,
  type DashboardEventSourcePort,
  type DashboardSourceView,
} from "./live-source";

class FakeEventSource implements DashboardEventSourcePort {
  readonly listeners = new Map<string, ((event: { readonly data?: unknown }) => void)[]>();
  closed = false;

  addEventListener(type: string, listener: (event: { readonly data?: unknown }) => void): void {
    const listeners = this.listeners.get(type) ?? [];
    listeners.push(listener);
    this.listeners.set(type, listeners);
  }

  close(): void {
    this.closed = true;
  }

  emit(type: string, data?: unknown): void {
    for (const listener of this.listeners.get(type) ?? []) {
      listener({ data });
    }
  }
}

function initialState(): DashboardReducedState & { latestAuditOrdinal: 0 } {
  return {
    canonicalizationVersion: 1,
    stateVersion: 1,
    currentMission: {
      identifier: "mission-synthetic-0001",
      lifecycle: "PLANNED",
      predecessorIdentifier: null,
    },
    fleet: [
      {
        identifier: "drone-sim-01",
        participation: "SIMULATED",
        connectivity: "CONNECTED",
        telemetry: null,
      },
    ],
    latestAuditOrdinal: 0,
    sectors: [],
  };
}

async function snapshot(runtimeId = "runtime-synthetic-0001"): Promise<DashboardSnapshot> {
  const state = initialState();
  return {
    snapshotVersion: "dashboard-snapshot/v1",
    runtimeId,
    cursor: "cursor-initial",
    digest: await replayStateDigest(state),
    latestEventDigest: null,
    currentRun: {
      mode: "degradedLive",
      missionId: "mission-synthetic-0001",
      runId: "run-synthetic-0001",
    },
    state,
    timeline: [],
  };
}

async function missionFrame(
  source: DashboardSnapshot,
  lifecycle: "SEARCHING" | "EXHAUSTED",
): Promise<DashboardEventFrame> {
  const event: OrderedDashboardEvent = {
    auditOrdinal: source.state.latestAuditOrdinal + 1,
    event: {
      kind: "missionLifecycle",
      eventClass: "MISSION",
      mission: "mission-synthetic-0001",
      time: "2026-08-26T12:00:00.000Z",
      data: { lifecycle },
    },
  };
  const state = structuredClone(source.state);
  if (state.currentMission !== null) {
    state.currentMission.lifecycle = lifecycle;
  }
  state.latestAuditOrdinal = event.auditOrdinal;
  return {
    frameVersion: "ordered-dashboard-event-frame/v1",
    cursor: `cursor-${String(event.auditOrdinal)}`,
    digest: await replayStateDigest(state),
    event,
  };
}

test("publishes only a validated snapshot and digest-consistent ordered successor", async () => {
  // Arrange
  const stream = new FakeEventSource();
  const views: DashboardSourceView[] = [];
  const initial = await snapshot();
  const frame = await missionFrame(initial, "SEARCHING");
  const source = startDashboardLiveSource({
    runtimeId: initial.runtimeId,
    isOnline: () => true,
    onView: (view) => views.push(view),
    openEventSource: () => stream,
  });

  // Act
  stream.emit("snapshot", JSON.stringify(initial));
  await source.whenIdle();
  stream.emit("dashboard-event", JSON.stringify(frame));
  await source.whenIdle();

  // Assert
  expect(views.map((view) => view.sourceState)).toEqual(["loading", "connected", "connected"]);
  expect(views.at(-1)?.snapshot).toMatchObject({
    cursor: "cursor-1",
    state: {
      currentMission: { lifecycle: "SEARCHING" },
      latestAuditOrdinal: 1,
    },
  });
  expect(stream.closed).toBe(false);
});

test("retains the last validated state and closes on a malformed ordered frame", async () => {
  // Arrange
  const stream = new FakeEventSource();
  const views: DashboardSourceView[] = [];
  const initial = await snapshot();
  const source = startDashboardLiveSource({
    runtimeId: initial.runtimeId,
    isOnline: () => true,
    onView: (view) => views.push(view),
    openEventSource: () => stream,
  });

  // Act
  stream.emit("snapshot", JSON.stringify(initial));
  await source.whenIdle();
  stream.emit("dashboard-event", '{"frameVersion":"ordered-dashboard-event-frame/v1"');
  await source.whenIdle();

  // Assert
  expect(views.at(-1)).toMatchObject({
    sourceState: "contractFailure",
    snapshot: { cursor: "cursor-initial", state: { latestAuditOrdinal: 0 } },
  });
  expect(stream.closed).toBe(true);
});

test("refuses a snapshot from a different server runtime", async () => {
  // Arrange
  const stream = new FakeEventSource();
  const views: DashboardSourceView[] = [];
  const foreignSnapshot = await snapshot("runtime-synthetic-0002");
  const source = startDashboardLiveSource({
    runtimeId: "runtime-synthetic-0001",
    isOnline: () => true,
    onView: (view) => views.push(view),
    openEventSource: () => stream,
  });

  // Act
  stream.emit("snapshot", JSON.stringify(foreignSnapshot));
  await source.whenIdle();

  // Assert
  expect(views.at(-1)).toEqual({ mode: "degradedLive", sourceState: "staleRuntime" });
  expect(stream.closed).toBe(true);
});

test("retains state across offline retry, overload resnapshot, and recovery", async () => {
  // Arrange
  const streams: FakeEventSource[] = [];
  const views: DashboardSourceView[] = [];
  const initial = await snapshot();
  let online = false;
  const source = startDashboardLiveSource({
    runtimeId: initial.runtimeId,
    isOnline: () => online,
    onView: (view) => views.push(view),
    openEventSource: () => {
      const stream = new FakeEventSource();
      streams.push(stream);
      return stream;
    },
  });
  const first = streams[0];
  if (first === undefined) {
    throw new Error("initial event source was not opened");
  }

  // Act
  first.emit("snapshot", JSON.stringify(initial));
  await source.whenIdle();
  first.emit("error");
  await source.whenIdle();
  online = true;
  first.emit("error");
  await source.whenIdle();
  first.emit(
    "stream-overloaded",
    JSON.stringify({
      controlVersion: "dashboard-stream-overloaded/v1",
      reason: "NON_DROPPABLE_BUFFER_FULL",
    }),
  );
  await source.whenIdle();
  const replacement = streams[1];
  if (replacement === undefined) {
    throw new Error("replacement event source was not opened");
  }
  replacement.emit("snapshot", JSON.stringify(initial));
  await source.whenIdle();

  // Assert
  expect(views.map((view) => view.sourceState)).toEqual([
    "loading",
    "connected",
    "offline",
    "retrying",
    "retrying",
    "recovered",
  ]);
  expect(views.at(-1)?.snapshot).toEqual(initial);
  expect(first.closed).toBe(true);
  expect(replacement.closed).toBe(false);
});

test("marks an exhausted mission while retaining its final durable fact", async () => {
  // Arrange
  const stream = new FakeEventSource();
  const views: DashboardSourceView[] = [];
  const initial = await snapshot();
  const exhausted = await missionFrame(initial, "EXHAUSTED");
  const source = startDashboardLiveSource({
    runtimeId: initial.runtimeId,
    isOnline: () => true,
    onView: (view) => views.push(view),
    openEventSource: () => stream,
  });

  // Act
  stream.emit("snapshot", JSON.stringify(initial));
  await source.whenIdle();
  stream.emit("dashboard-event", JSON.stringify(exhausted));
  await source.whenIdle();

  // Assert
  expect(views.at(-1)).toMatchObject({
    sourceState: "exhausted",
    snapshot: { state: { currentMission: { lifecycle: "EXHAUSTED" } } },
  });
});

test("refuses non-text, schema-invalid, and digest-invalid snapshots", async () => {
  // Arrange
  const streams = [new FakeEventSource(), new FakeEventSource(), new FakeEventSource()];
  const viewSets: DashboardSourceView[][] = [[], [], []];
  const digestInvalid = await snapshot();
  digestInvalid.digest = "f".repeat(64);
  const sources = streams.map((stream, index) =>
    startDashboardLiveSource({
      runtimeId: "runtime-synthetic-0001",
      isOnline: () => true,
      onView: (view) => viewSets[index]?.push(view),
      openEventSource: () => stream,
    }),
  );

  // Act
  streams[0]?.emit("snapshot", { not: "text" });
  streams[1]?.emit("snapshot", JSON.stringify({ snapshotVersion: "unknown" }));
  streams[2]?.emit("snapshot", JSON.stringify(digestInvalid));
  await Promise.all(sources.map((source) => source.whenIdle()));

  // Assert
  expect(viewSets.map((views) => views.at(-1)?.sourceState)).toEqual([
    "contractFailure",
    "contractFailure",
    "contractFailure",
  ]);
  expect(streams.map((stream) => stream.closed)).toEqual([true, true, true]);
});

test("requires snapshot-first ordering and refuses invalid or noncontiguous frames", async () => {
  // Arrange
  const noSnapshotStream = new FakeEventSource();
  const invalidFrameStream = new FakeEventSource();
  const gapStream = new FakeEventSource();
  const viewSets: DashboardSourceView[][] = [[], [], []];
  const initial = await snapshot();
  const sources = [noSnapshotStream, invalidFrameStream, gapStream].map((stream, index) =>
    startDashboardLiveSource({
      runtimeId: initial.runtimeId,
      isOnline: () => true,
      onView: (view) => viewSets[index]?.push(view),
      openEventSource: () => stream,
    }),
  );

  // Act
  noSnapshotStream.emit("dashboard-event", JSON.stringify({}));
  invalidFrameStream.emit("snapshot", JSON.stringify(initial));
  gapStream.emit("snapshot", JSON.stringify(initial));
  await Promise.all(sources.map((source) => source.whenIdle()));
  invalidFrameStream.emit("dashboard-event", JSON.stringify({ frameVersion: "unknown" }));
  const contiguous = await missionFrame(initial, "SEARCHING");
  const gap = { ...contiguous, event: { ...contiguous.event, auditOrdinal: 2 } };
  gapStream.emit("dashboard-event", JSON.stringify(gap));
  await Promise.all(sources.map((source) => source.whenIdle()));

  // Assert
  expect(viewSets.map((views) => views.at(-1)?.sourceState)).toEqual([
    "contractFailure",
    "contractFailure",
    "contractFailure",
  ]);
  expect([noSnapshotStream, invalidFrameStream, gapStream].map((stream) => stream.closed)).toEqual([
    true,
    true,
    true,
  ]);
});

test("fails closed on an invalid overload control and ignores stale source events", async () => {
  // Arrange
  const streams: FakeEventSource[] = [];
  const views: DashboardSourceView[] = [];
  const initial = await snapshot();
  const source = startDashboardLiveSource({
    runtimeId: initial.runtimeId,
    isOnline: () => true,
    onView: (view) => views.push(view),
    openEventSource: () => {
      const stream = new FakeEventSource();
      streams.push(stream);
      return stream;
    },
  });
  const first = streams[0];
  if (first === undefined) {
    throw new Error("initial source was not opened");
  }

  // Act
  first.emit("snapshot", JSON.stringify(initial));
  await source.whenIdle();
  first.emit("stream-overloaded", JSON.stringify({ controlVersion: "invalid" }));
  await source.whenIdle();
  first.emit("snapshot", JSON.stringify(initial));
  first.emit("dashboard-event", JSON.stringify(await missionFrame(initial, "SEARCHING")));
  first.emit("error");
  await source.whenIdle();

  // Assert
  expect(views.map((view) => view.sourceState)).toEqual([
    "loading",
    "connected",
    "contractFailure",
  ]);
  expect(first.closed).toBe(true);
});

test.each([
  [true, "retrying"],
  [false, "offline"],
] as const)(
  "reports an initial transport-open failure when online is %s",
  async (online, expected) => {
    // Arrange
    const views: DashboardSourceView[] = [];

    // Act
    const source = startDashboardLiveSource({
      runtimeId: "runtime-synthetic-0001",
      isOnline: () => online,
      onView: (view) => views.push(view),
      openEventSource: () => {
        throw new Error("synthetic transport refusal");
      },
    });
    await source.whenIdle();

    // Assert
    expect(views.map((view) => view.sourceState)).toEqual(["loading", expected]);
  },
);

test("preserves the current mode for an idle snapshot and ignores queued work after disposal", async () => {
  // Arrange
  const stream = new FakeEventSource();
  const views: DashboardSourceView[] = [];
  const idleSnapshot = await snapshot();
  idleSnapshot.currentRun = null;
  const source = startDashboardLiveSource({
    runtimeId: idleSnapshot.runtimeId,
    isOnline: () => true,
    onView: (view) => views.push(view),
    openEventSource: () => stream,
  });

  // Act
  stream.emit("snapshot", JSON.stringify(idleSnapshot));
  await source.whenIdle();
  stream.emit("error");
  source.dispose();
  await source.whenIdle();

  // Assert
  expect(views.map((view) => view.sourceState)).toEqual(["loading", "connected"]);
  expect(views.at(-1)?.mode).toBe("degradedLive");
  expect(stream.closed).toBe(true);
});

test("adapts native message and control events without exposing the browser source", () => {
  // Arrange
  const listeners = new Map<string, EventListener[]>();
  let closed = false;
  class NativeSource {
    addEventListener(type: string, listener: EventListener): void {
      listeners.set(type, [...(listeners.get(type) ?? []), listener]);
    }

    close(): void {
      closed = true;
    }
  }
  vi.stubGlobal("EventSource", NativeSource);
  const observed: unknown[] = [];
  const source = createNativeDashboardEventSource();
  source.addEventListener("snapshot", (event) => observed.push(event.data));
  source.addEventListener("error", (event) => observed.push(event.data));

  // Act
  listeners.get("snapshot")?.[0]?.(new MessageEvent("snapshot", { data: "snapshot-data" }));
  listeners.get("error")?.[0]?.(new Event("error"));
  source.close();
  vi.unstubAllGlobals();

  // Assert
  expect(observed).toEqual(["snapshot-data", undefined]);
  expect(closed).toBe(true);
});
