import { expect, test, vi } from "vitest";

import type { DashboardEventSource, DashboardSourceInput } from "./event-source";
import { ProductionDashboardRuntime, readProductionBootstrap } from "./production-runtime";

const EMPTY_SOURCE: DashboardEventSource = {
  open: () => ({ dispose: vi.fn() }),
};
const VALID_BOOTSTRAP: DashboardSourceInput = {
  channel: "bootstrap",
  name: "bootstrap",
  raw: '{"bearer":"synthetic-memory-only","bootstrapVersion":"dashboard-bootstrap/v1","runtimeId":"runtime-synthetic-0001"}',
};

test("removes a non-script bootstrap candidate and returns empty refusal bytes", () => {
  // Arrange
  document.head.innerHTML = '<meta id="dashboard-bootstrap" content="not-json">';

  // Act
  const input = readProductionBootstrap(document);

  // Assert
  expect(input.raw).toBe("");
  expect(document.querySelector("#dashboard-bootstrap")).toBeNull();
});

test("consumes an empty bootstrap once without opening production transports", async () => {
  // Arrange
  const consumeBoundary = vi.fn<(input: DashboardSourceInput) => Promise<void>>(() =>
    Promise.resolve(),
  );
  const fetcher = vi.fn(() => Promise.resolve(new Response("{}")));
  const liveSourceFactory = vi.fn(() => EMPTY_SOURCE);
  const session = {
    acceptReplayResume: vi.fn(() => false),
    anchorRuntime: vi.fn(),
    expectLiveRun: vi.fn(),
    replaceSource: vi.fn(),
  };
  const runtime = new ProductionDashboardRuntime({
    bootstrap: { channel: "bootstrap", name: "bootstrap", raw: "" },
    consumeBoundary,
    fetcher,
    liveSourceFactory,
    session,
  });

  // Act
  await runtime.start();
  await runtime.start();

  // Assert
  expect(consumeBoundary).toHaveBeenCalledOnce();
  expect(fetcher).not.toHaveBeenCalled();
  expect(liveSourceFactory).not.toHaveBeenCalled();
  expect(session.replaceSource).not.toHaveBeenCalled();
});

test("makes every production runtime operation inert after idempotent disposal", async () => {
  // Arrange
  const fetcher = vi.fn(() => Promise.resolve(new Response("{}")));
  const session = {
    acceptReplayResume: vi.fn(() => false),
    anchorRuntime: vi.fn(),
    expectLiveRun: vi.fn(),
    replaceSource: vi.fn(),
  };
  const runtime = new ProductionDashboardRuntime({
    bootstrap: { channel: "bootstrap", name: "bootstrap", raw: "sensitive" },
    consumeBoundary: vi.fn(),
    fetcher,
    liveSourceFactory: () => EMPTY_SOURCE,
    replaySourceFactory: () => EMPTY_SOURCE,
    session,
  });

  // Act
  runtime.dispose();
  runtime.dispose();
  await runtime.start();
  await runtime.selectMode("replay");
  runtime.resnapshot();
  runtime.acceptedMutation({
    declaredCount: 23,
    declaredOnlyCount: 3,
    mode: "replay",
    operationVersion: "dashboard-start-response/v1",
    sessionId: "session-production-0001",
    simulatedCount: 20,
  });

  // Assert
  expect(fetcher).not.toHaveBeenCalled();
  expect(session.replaceSource).not.toHaveBeenCalled();
});

test("refreshes readiness and replaces live transport only when returning to live mode", async () => {
  // Arrange
  const fetcher = vi.fn<(url: string, init: RequestInit) => Promise<Response>>(() =>
    Promise.resolve(new Response("{}", { status: 200 })),
  );
  const session = {
    acceptReplayResume: vi.fn(() => false),
    anchorRuntime: vi.fn(),
    expectLiveRun: vi.fn(),
    replaceSource: vi.fn(),
  };
  const runtime = new ProductionDashboardRuntime({
    bootstrap: VALID_BOOTSTRAP,
    consumeBoundary: vi.fn(),
    fetcher,
    liveSourceFactory: () => EMPTY_SOURCE,
    session,
  });
  await runtime.start();

  // Act
  await runtime.selectMode("replay");
  await runtime.selectMode("degradedLive");

  // Assert
  expect(session.replaceSource).toHaveBeenCalledTimes(2);
  expect(fetcher.mock.calls.map(([url]) => url)).toEqual([
    "/api/v1/readiness?mode=degradedLive",
    "/api/v1/scenarios",
    "/api/v1/readiness?mode=replay",
    "/api/v1/readiness?mode=degradedLive",
  ]);
});

test("aborts pending document reads and suppresses their late boundary callbacks", async () => {
  // Arrange
  const pending: ((response: Response) => void)[] = [];
  const signals: AbortSignal[] = [];
  const fetcher = vi.fn((_url: string, init: RequestInit) => {
    if (init.signal instanceof AbortSignal) signals.push(init.signal);
    return new Promise<Response>((resolve) => {
      pending.push(resolve);
    });
  });
  const consumeBoundary = vi.fn<(input: DashboardSourceInput) => Promise<void>>(() =>
    Promise.resolve(),
  );
  const runtime = new ProductionDashboardRuntime({
    bootstrap: VALID_BOOTSTRAP,
    consumeBoundary,
    fetcher,
    liveSourceFactory: () => EMPTY_SOURCE,
    session: {
      acceptReplayResume: vi.fn(() => false),
      anchorRuntime: vi.fn(),
      expectLiveRun: vi.fn(),
      replaceSource: vi.fn(),
    },
  });
  const starting = runtime.start();
  await Promise.resolve();

  // Act
  runtime.dispose();
  for (const resolve of pending) resolve(new Response("{}", { status: 200 }));
  await starting;

  // Assert
  expect(signals.every((signal) => signal.aborted)).toBe(true);
  expect(consumeBoundary).toHaveBeenCalledOnce();
});

test("converts network and HTTP document failures into empty untrusted boundaries", async () => {
  // Arrange
  const consumeBoundary = vi.fn<(input: DashboardSourceInput) => Promise<void>>(() =>
    Promise.resolve(),
  );
  const fetcher = vi
    .fn<(url: string, init: RequestInit) => Promise<Response>>()
    .mockRejectedValueOnce(new Error("network down"))
    .mockResolvedValueOnce(new Response("ignored", { status: 503 }));
  const runtime = new ProductionDashboardRuntime({
    bootstrap: VALID_BOOTSTRAP,
    consumeBoundary,
    fetcher,
    liveSourceFactory: () => EMPTY_SOURCE,
    session: {
      acceptReplayResume: vi.fn(() => false),
      anchorRuntime: vi.fn(),
      expectLiveRun: vi.fn(),
      replaceSource: vi.fn(),
    },
  });

  // Act
  await runtime.start();

  // Assert
  expect(consumeBoundary.mock.calls.slice(1).map(([input]) => input.raw)).toEqual(["", ""]);
});

test("forwards typed readiness bodies at 200 and 503 while refusing other document statuses", async () => {
  // Arrange
  const unavailable =
    '{"mode":"degradedLive","readinessVersion":"dashboard-readiness/v1","ready":false,"reasons":["recorder-capture-unavailable"]}';
  const replayReady =
    '{"mode":"replay","readinessVersion":"dashboard-readiness/v1","ready":true,"reasons":[]}';
  const consumeBoundary = vi.fn<(input: DashboardSourceInput) => Promise<void>>(() =>
    Promise.resolve(),
  );
  const fetcher = vi.fn((url: string) => {
    if (url === "/api/v1/readiness?mode=degradedLive") {
      return Promise.resolve(new Response(unavailable, { status: 503 }));
    }
    if (url === "/api/v1/readiness?mode=replay") {
      return Promise.resolve(new Response(replayReady, { status: 500 }));
    }
    return Promise.resolve(
      new Response('{"catalogVersion":"scenario-catalog/v1"}', { status: 503 }),
    );
  });
  const runtime = new ProductionDashboardRuntime({
    bootstrap: VALID_BOOTSTRAP,
    consumeBoundary,
    fetcher,
    liveSourceFactory: () => EMPTY_SOURCE,
    session: {
      acceptReplayResume: vi.fn(() => false),
      anchorRuntime: vi.fn(),
      expectLiveRun: vi.fn(),
      replaceSource: vi.fn(),
    },
  });

  // Act
  await runtime.start();
  await runtime.selectMode("replay");
  const received = consumeBoundary.mock.calls.slice(1).map(([input]) => ({
    name: input.name,
    raw: input.raw,
  }));

  // Assert
  expect(received).toEqual([
    { name: "readiness", raw: unavailable },
    { name: "scenario-catalog", raw: "" },
    { name: "readiness", raw: "" },
  ]);
});
