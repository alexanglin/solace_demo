import { expect, test, vi } from "vitest";

import { fixtureForState } from "../../tests/e2e/support/dashboard-fixtures";
import type { DashboardReducedState } from "../contracts/generated";
import { replayStateDigest } from "../domain/canonical";
import type {
  DashboardEventSource,
  DashboardSourceConsumer,
  DashboardSourceInput,
  DashboardSourceSubscription,
} from "./event-source";
import { ProductionDashboardRuntime, readProductionBootstrap } from "./production-runtime";
import { DashboardSourceSession } from "./source-session";

const VALID_BOOTSTRAP: DashboardSourceInput = {
  channel: "bootstrap",
  name: "bootstrap",
  raw: '{"bearer":"synthetic-memory-only","bootstrapVersion":"dashboard-bootstrap/v1","runtimeId":"runtime-synthetic-0001"}',
};

class ManualLiveSource implements DashboardEventSource {
  disposeCount = 0;
  private consumer: DashboardSourceConsumer | null = null;

  open(consumer: DashboardSourceConsumer): DashboardSourceSubscription {
    this.consumer = consumer;
    return {
      dispose: () => {
        this.disposeCount += 1;
      },
    };
  }

  emit(input: DashboardSourceInput): Promise<void> {
    if (this.consumer === null) throw new Error("manual source is closed");
    return this.consumer(input);
  }
}

async function replayOwnedSnapshot(input: DashboardSourceInput): Promise<DashboardSourceInput> {
  const document = JSON.parse(input.raw) as {
    currentRun: unknown;
    digest: string;
    state: DashboardReducedState;
  };
  const state: DashboardReducedState = {
    ...document.state,
    currentMission:
      document.state.currentMission === null
        ? null
        : { ...document.state.currentMission, identifier: "mission-replay-owned" },
  };
  return {
    ...input,
    raw: JSON.stringify({
      ...document,
      currentRun: { mode: "replay", sessionId: "session-production-0001" },
      digest: await replayStateDigest(state),
      state,
    }),
  };
}

test("removes the unique production bootstrap before returning its untrusted bytes", () => {
  // Arrange
  document.head.innerHTML =
    '<script id="dashboard-bootstrap" type="application/json">{"bearer":"memory-only"}</script>';

  // Act
  const input = readProductionBootstrap(document);

  // Assert
  expect(document.querySelectorAll('[id="dashboard-bootstrap"]')).toHaveLength(0);
  expect(input).toEqual({
    channel: "bootstrap",
    name: "bootstrap",
    raw: '{"bearer":"memory-only"}',
  });
});

test("clears duplicate bootstrap nodes and returns a refusal candidate", () => {
  // Arrange
  document.head.innerHTML =
    '<script id="dashboard-bootstrap" type="application/json">first</script>' +
    '<script id="dashboard-bootstrap" type="application/json">second</script>';

  // Act
  const input = readProductionBootstrap(document);

  // Assert
  expect(document.querySelectorAll('[id="dashboard-bootstrap"]')).toHaveLength(0);
  expect(input.raw).toBe("");
});

test("binds an accepted live mutation identity before opening its replacement source", () => {
  // Arrange
  const order: string[] = [];
  const session = {
    acceptReplayResume: vi.fn(() => false),
    anchorRuntime: vi.fn(),
    expectLiveRun: vi.fn((missionId: string, runId: string) => {
      order.push(`expect:${missionId}:${runId}`);
    }),
    replaceSource: vi.fn(() => {
      order.push("source");
    }),
  };
  const runtime = new ProductionDashboardRuntime({
    bootstrap: VALID_BOOTSTRAP,
    consumeBoundary: vi.fn(),
    liveSourceFactory: () => new ManualLiveSource(),
    session,
  });

  // Act
  runtime.acceptedMutation({
    declaredCount: 23,
    declaredOnlyCount: 3,
    missionId: "mission-live-accepted",
    mode: "degradedLive",
    operationVersion: "dashboard-start-response/v1",
    runId: "run-live-accepted",
    simulatedCount: 20,
  });

  // Assert
  expect(order).toEqual(["expect:mission-live-accepted:run-live-accepted", "source"]);
  expect(session.expectLiveRun).toHaveBeenCalledOnce();
});

test("anchors the validated bootstrap runtime before opening the first live source", async () => {
  // Arrange
  const order: string[] = [];
  const session = {
    acceptReplayResume: vi.fn(() => false),
    anchorRuntime: vi.fn((runtimeId: string) => {
      order.push(`anchor:${runtimeId}`);
    }),
    expectLiveRun: vi.fn(),
    replaceSource: vi.fn(() => {
      order.push("source");
    }),
  };
  const runtime = new ProductionDashboardRuntime({
    bootstrap: {
      channel: "bootstrap",
      name: "bootstrap",
      raw: '{"bearer":"synthetic-memory-only","bootstrapVersion":"dashboard-bootstrap/v1","runtimeId":"runtime-bootstrap-0001"}',
    },
    consumeBoundary: () => {
      order.push("boundary");
      return Promise.resolve();
    },
    fetcher: () => Promise.resolve(new Response("{}", { status: 503 })),
    liveSourceFactory: () => new ManualLiveSource(),
    session,
  });

  // Act
  await runtime.start();

  // Assert
  expect(order.slice(0, 3)).toEqual(["anchor:runtime-bootstrap-0001", "boundary", "source"]);
  expect(session.anchorRuntime).toHaveBeenCalledOnce();
});

test("discards stale replay readiness after the operator returns to degraded live", async () => {
  // Arrange
  const readinessModes: string[] = [];
  let resolveLiveSelection: ((response: Response) => void) | undefined;
  let resolveReplayResume: ((response: Response) => void) | undefined;
  let readinessRequest = 0;
  const fetcher = vi.fn((url: string) => {
    if (url === "/api/v1/scenarios") {
      return Promise.resolve(new Response('{"catalogVersion":"scenario-catalog/v1"}'));
    }
    readinessRequest += 1;
    if (readinessRequest === 1) {
      return Promise.resolve(
        new Response(
          '{"mode":"degradedLive","readinessVersion":"dashboard-readiness/v1","ready":true,"reasons":[]}',
        ),
      );
    }
    return new Promise<Response>((resolve) => {
      if (readinessRequest === 2) resolveReplayResume = resolve;
      else resolveLiveSelection = resolve;
    });
  });
  const runtime = new ProductionDashboardRuntime({
    bootstrap: VALID_BOOTSTRAP,
    consumeBoundary: (input) => {
      if (input.name === "readiness" && input.raw !== "") {
        readinessModes.push((JSON.parse(input.raw) as { mode: string }).mode);
      }
      return Promise.resolve();
    },
    fetcher,
    session: {
      acceptReplayResume: vi.fn(() => true),
      anchorRuntime: vi.fn(),
      expectLiveRun: vi.fn(),
      replaceSource: vi.fn(),
    },
  });
  await runtime.start();
  runtime.observeSnapshotRun({ mode: "replay", sessionId: "session-production-0001" });
  const liveSelection = runtime.selectMode("degradedLive");
  await vi.waitUntil(() => resolveLiveSelection !== undefined && resolveReplayResume !== undefined);

  // Act
  resolveLiveSelection?.(
    new Response(
      '{"mode":"degradedLive","readinessVersion":"dashboard-readiness/v1","ready":true,"reasons":[]}',
    ),
  );
  await liveSelection;
  resolveReplayResume?.(
    new Response(
      '{"mode":"replay","readinessVersion":"dashboard-readiness/v1","ready":true,"reasons":[]}',
    ),
  );
  await new Promise<void>((resolve) => {
    globalThis.setTimeout(resolve, 0);
  });

  // Assert
  expect(readinessModes).toEqual(["degradedLive", "degradedLive"]);
});

test("replaces an overloaded stream with exactly one fresh live source", async () => {
  // Arrange
  const sources: ManualLiveSource[] = [];
  const runtimeHolder: { current: ProductionDashboardRuntime | null } = { current: null };
  const session = new DashboardSourceSession({
    requestSnapshot: () => {
      runtimeHolder.current?.resnapshot();
    },
  });
  const fetcher = vi.fn((input: string) =>
    Promise.resolve(
      new Response(
        input.includes("readiness")
          ? '{"mode":"degradedLive","readinessVersion":"dashboard-readiness/v1","ready":true,"reasons":[]}'
          : '{"catalogVersion":"scenario-catalog/v1","scenarios":[]}',
        { status: 200 },
      ),
    ),
  );
  const runtime = new ProductionDashboardRuntime({
    bootstrap: VALID_BOOTSTRAP,
    consumeBoundary: vi.fn(),
    fetcher,
    liveSourceFactory: () => {
      const source = new ManualLiveSource();
      sources.push(source);
      return source;
    },
    session,
  });
  runtimeHolder.current = runtime;
  await runtime.start();
  const overload = {
    channel: "sse-frame",
    name: "stream-overloaded",
    raw: '{"controlVersion":"dashboard-stream-overloaded/v1","reason":"NON_DROPPABLE_BUFFER_FULL"}',
  };

  // Act
  await sources[0]?.emit(overload);
  await session.whenIdle();

  // Assert
  expect(sources).toHaveLength(2);
  expect(sources[0]?.disposeCount).toBe(1);
  expect(sources[1]?.disposeCount).toBe(0);
  expect(fetcher).toHaveBeenCalledTimes(2);
});

test("replaces the pinned source once after each accepted live, reset, and replay mutation", async () => {
  // Arrange
  const liveSources: ManualLiveSource[] = [];
  const replaySources: ManualLiveSource[] = [];
  const session = new DashboardSourceSession();
  const fetcher = vi.fn((input: string) =>
    Promise.resolve(
      new Response(
        input.includes("readiness")
          ? '{"mode":"degradedLive","readinessVersion":"dashboard-readiness/v1","ready":true,"reasons":[]}'
          : '{"catalogVersion":"scenario-catalog/v1","scenarios":[]}',
        { status: 200 },
      ),
    ),
  );
  const runtime = new ProductionDashboardRuntime({
    bootstrap: VALID_BOOTSTRAP,
    consumeBoundary: vi.fn(),
    fetcher,
    liveSourceFactory: () => {
      const source = new ManualLiveSource();
      liveSources.push(source);
      return source;
    },
    replaySourceFactory: () => {
      const source = new ManualLiveSource();
      replaySources.push(source);
      return source;
    },
    session,
  });
  await runtime.start();

  // Act
  runtime.acceptedMutation({
    declaredCount: 23,
    declaredOnlyCount: 3,
    missionId: "mission-live",
    mode: "degradedLive",
    operationVersion: "dashboard-start-response/v1",
    runId: "run-live",
    simulatedCount: 20,
  });
  runtime.acceptedMutation({
    declaredCount: 23,
    declaredOnlyCount: 3,
    missionId: "mission-successor",
    mode: "degradedLive",
    operationVersion: "dashboard-reset-response/v1",
    predecessorMissionId: "mission-live",
    runId: "run-successor",
    simulatedCount: 20,
  });
  runtime.acceptedMutation({
    declaredCount: 23,
    declaredOnlyCount: 3,
    mode: "replay",
    operationVersion: "dashboard-start-response/v1",
    sessionId: "session-production-0001",
    simulatedCount: 20,
  });

  // Assert
  expect(liveSources).toHaveLength(3);
  expect(liveSources.map(({ disposeCount }) => disposeCount)).toEqual([1, 1, 1]);
  expect(replaySources).toHaveLength(1);
  expect(replaySources[0]?.disposeCount).toBe(0);
  expect(fetcher).toHaveBeenCalledTimes(2);
});

test("refuses a replay-owned global snapshot after returning to degraded live mode", async () => {
  // Arrange
  const liveSources: ManualLiveSource[] = [];
  const session = new DashboardSourceSession();
  const fetcher = vi.fn((input: string) =>
    Promise.resolve(
      new Response(
        input.includes("readiness")
          ? '{"mode":"degradedLive","readinessVersion":"dashboard-readiness/v1","ready":true,"reasons":[]}'
          : '{"catalogVersion":"scenario-catalog/v1","scenarios":[]}',
        { status: 200 },
      ),
    ),
  );
  const runtime = new ProductionDashboardRuntime({
    bootstrap: VALID_BOOTSTRAP,
    consumeBoundary: vi.fn(),
    fetcher,
    liveSourceFactory: () => {
      const source = new ManualLiveSource();
      liveSources.push(source);
      return source;
    },
    replaySourceFactory: () => new ManualLiveSource(),
    session,
  });
  const snapshot = fixtureForState("ready").inputs.find(({ name }) => name === "snapshot");
  if (snapshot === undefined) throw new Error("production mode test has no live snapshot");
  await runtime.start();
  await liveSources[0]?.emit(snapshot);
  const liveMission = session.state.mission;
  runtime.acceptedMutation({
    declaredCount: 23,
    declaredOnlyCount: 3,
    mode: "replay",
    operationVersion: "dashboard-start-response/v1",
    sessionId: "session-production-0001",
    simulatedCount: 20,
  });
  await runtime.selectMode("degradedLive");
  const replaySnapshot = await replayOwnedSnapshot(snapshot);

  // Act
  await liveSources[1]?.emit(replaySnapshot);
  const refusedMission = session.state.mission;
  const refusedServer = session.state.server;
  await liveSources[1]?.emit(snapshot);

  // Assert
  expect(refusedMission).toBe(liveMission);
  expect(refusedServer).toMatchObject({
    currentRun: { mode: "replay", sessionId: "session-production-0001" },
    refusal: {
      actualMode: "replay",
      code: "RUN_MODE_MISMATCH",
      expectedMode: "degradedLive",
      inputName: "snapshot",
    },
    status: "modeMismatch",
  });
  expect(session.state.server.status).toBe("connected");
  expect(session.state.server.currentRun).toMatchObject({ mode: "degradedLive" });
  expect(session.state.mission.checkpoint.state.currentMission?.identifier).not.toBe(
    "mission-replay-owned",
  );
});
