import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { fixtureForState, replayFixture } from "../tests/e2e/support/dashboard-fixtures";
import type { DashboardReducedState } from "./contracts/generated";
import { DashboardApplication } from "./dashboard-app";
import { replayStateDigest } from "./domain/canonical";
import type {
  DashboardEventSource,
  DashboardSourceConsumer,
  DashboardSourceInput,
  DashboardSourceSubscription,
} from "./sources/event-source";
import type {
  ProductionDashboardRuntimeOptions,
  ProductionRuntimePort,
} from "./sources/production-runtime";
import { ProductionDashboardRuntime } from "./sources/production-runtime";

vi.mock("./components/search-map", () => ({
  SearchMap: () => <section aria-label="Search map">Production map</section>,
}));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

class FakeProductionRuntime implements ProductionRuntimePort {
  accepted = vi.fn();
  disposed = false;
  private readonly consume: DashboardSourceConsumer;
  private readonly inputs: readonly DashboardSourceInput[];
  private readonly session: ProductionDashboardRuntimeOptions["session"];

  constructor(options: ProductionDashboardRuntimeOptions, inputs: readonly DashboardSourceInput[]) {
    this.consume = options.consumeBoundary;
    this.inputs = [options.bootstrap, ...inputs];
    this.session = options.session;
  }

  acceptedMutation = this.accepted;

  dispose(): void {
    this.disposed = true;
  }

  emitBoundary(input: DashboardSourceInput): Promise<void> {
    return this.consume(input);
  }

  observeSnapshotRun = vi.fn();

  resnapshot = vi.fn();

  replaceSource(source: DashboardEventSource, expectedRunMode: "degradedLive" | "replay"): void {
    this.session.replaceSource(source, expectedRunMode);
  }

  async selectMode(mode: "degradedLive" | "replay"): Promise<void> {
    await this.consume({
      channel: "http-response",
      name: "readiness",
      raw: JSON.stringify({
        mode,
        readinessVersion: "dashboard-readiness/v1",
        ready: true,
        reasons: [],
      }),
    });
  }

  async start(): Promise<void> {
    for (const input of this.inputs) await this.consume(input);
  }
}

class ManualSource implements DashboardEventSource {
  opened = false;
  private consumer: DashboardSourceConsumer | null = null;

  emit(input: DashboardSourceInput): Promise<void> {
    if (this.consumer === null) throw new Error("production integration source is not open");
    return this.consumer(input);
  }

  open(consumer: DashboardSourceConsumer): DashboardSourceSubscription {
    this.consumer = consumer;
    this.opened = true;
    return { dispose: vi.fn() };
  }
}

function readyProductionInputs(): {
  bootstrap: DashboardSourceInput;
  boundaries: readonly DashboardSourceInput[];
  catalog: DashboardSourceInput;
  readiness: DashboardSourceInput;
  snapshot: DashboardSourceInput;
} {
  const inputs = fixtureForState("ready").inputs;
  const bootstrap = inputs.find(({ channel }) => channel === "bootstrap");
  const readiness = inputs.find(({ name }) => name === "readiness");
  const catalog = inputs.find(({ name }) => name === "scenario-catalog");
  const snapshot = inputs.find(({ name }) => name === "snapshot");
  if (
    bootstrap === undefined ||
    readiness === undefined ||
    catalog === undefined ||
    snapshot === undefined
  ) {
    throw new Error("production ready fixture is incomplete");
  }
  return { bootstrap, boundaries: [readiness, catalog], catalog, readiness, snapshot };
}

function renderFakeProductionDashboard(
  bootstrap: DashboardSourceInput,
  boundaries: readonly DashboardSourceInput[],
): { current: FakeProductionRuntime | undefined } {
  const runtime: { current: FakeProductionRuntime | undefined } = { current: undefined };
  render(
    <DashboardApplication
      productionBootstrap={bootstrap}
      productionRuntimeFactory={(options) => {
        runtime.current = new FakeProductionRuntime(options, boundaries);
        return runtime.current;
      }}
    />,
  );
  return runtime;
}

async function renderLiveProductionDashboard(inputs: {
  bootstrap: DashboardSourceInput;
  catalog: DashboardSourceInput;
  readiness: DashboardSourceInput;
}): Promise<{ fetcher: ReturnType<typeof vi.fn>; liveSource: ManualSource }> {
  const liveSource = new ManualSource();
  const fetcher = vi.fn((url: string) =>
    Promise.resolve(
      new Response(url.includes("readiness") ? inputs.readiness.raw : inputs.catalog.raw, {
        status: 200,
      }),
    ),
  );
  render(
    <DashboardApplication
      productionBootstrap={inputs.bootstrap}
      productionRuntimeFactory={(options) =>
        new ProductionDashboardRuntime({
          ...options,
          fetcher,
          liveSourceFactory: () => liveSource,
        })
      }
    />,
  );
  await vi.waitUntil(() => liveSource.opened);
  return { fetcher, liveSource };
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

test("starts a fresh production mission from readiness and catalog without fabricating state", async () => {
  // Arrange
  const { bootstrap, boundaries } = readyProductionInputs();
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve(
        new Response(
          '{"declaredCount":23,"declaredOnlyCount":3,"missionId":"mission-production","mode":"degradedLive","operationVersion":"dashboard-start-response/v1","runId":"run-production","simulatedCount":20}',
          { status: 202 },
        ),
      ),
    ),
  );
  const runtime = renderFakeProductionDashboard(bootstrap, boundaries);
  const start = await screen.findByRole("button", { name: "Start wilderness mission" });
  await vi.waitUntil(() => !(start as HTMLButtonElement).disabled);

  // Act
  fireEvent.click(start);
  await vi.waitUntil(() => runtime.current?.accepted.mock.calls.length === 1);

  // Assert
  expect(screen.getByRole("status", { name: "Current mission" }).textContent).toBe(
    "No current mission",
  );
  expect(runtime.current?.accepted).toHaveBeenCalledWith(
    expect.objectContaining({ missionId: "mission-production", mode: "degradedLive" }),
  );
});

test("keeps mutation controls locked without a validated production bootstrap", async () => {
  // Arrange
  const { bootstrap, boundaries } = readyProductionInputs();
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  const runtime = renderFakeProductionDashboard({ ...bootstrap, raw: "{}" }, boundaries);
  await screen.findByText(/23 declared = 20 simulated \+ 3 declared only/i);
  const liveStart = screen.getByRole("button", { name: "Start wilderness mission" });
  const liveReset = screen.getByRole("button", { name: "Reset mission" });

  // Act
  const liveStartWasDisabled = (liveStart as HTMLButtonElement).disabled;
  const liveResetWasDisabled = (liveReset as HTMLButtonElement).disabled;
  fireEvent.click(screen.getByRole("radio", { name: "Isolated replay" }));
  const replayStart = await screen.findByRole("button", { name: "Start replay" });
  await vi.waitUntil(
    () => screen.getByRole("status", { name: "Readiness" }).textContent === "READY",
  );
  const replayStartWasDisabled = (replayStart as HTMLButtonElement).disabled;

  // Assert
  expect(liveStartWasDisabled).toBe(true);
  expect(liveResetWasDisabled).toBe(true);
  expect(replayStartWasDisabled).toBe(true);
  expect((replayStart as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByRole("button", { name: "Reload dashboard" })).toBeDefined();
  expect(screen.getByRole("alert").textContent).toBe("Contract validation failed · bootstrap");
  expect(fetcher).not.toHaveBeenCalled();
  expect(runtime.current?.accepted).not.toHaveBeenCalled();
});

test("fails closed when the mutation bootstrap disappears after reset is armed", async () => {
  // Arrange
  const { bootstrap, boundaries, snapshot } = readyProductionInputs();
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  const runtime = renderFakeProductionDashboard(bootstrap, boundaries);
  await screen.findByText(/23 declared = 20 simulated \+ 3 declared only/i);
  if (runtime.current === undefined) throw new Error("production mutation runtime was not created");
  const source = new ManualSource();
  runtime.current.replaceSource(source, "degradedLive");
  await source.emit(snapshot);
  const liveStart = screen.getByRole("button", { name: "Start wilderness mission" });
  const liveReset = screen.getByRole("button", { name: "Reset mission" });
  await vi.waitUntil(() => !(liveReset as HTMLButtonElement).disabled);
  fireEvent.click(liveReset);
  screen.getByRole("button", { name: "Confirm reset" });

  // Act
  await runtime.current.emitBoundary({ ...bootstrap, raw: "{}" });
  await vi.waitUntil(() => (liveReset as HTMLButtonElement).disabled);
  fireEvent.click(screen.getByRole("button", { name: "Confirm reset" }));
  await vi.waitUntil(
    () =>
      screen.queryByRole("alert")?.textContent ===
      "Contract validation failed · mutation bootstrap",
  );
  fireEvent.click(screen.getByRole("radio", { name: "Isolated replay" }));
  const replayStart = await screen.findByRole("button", { name: "Start replay" });

  // Assert
  expect((liveStart as HTMLButtonElement).disabled).toBe(true);
  expect((liveReset as HTMLButtonElement).disabled).toBe(true);
  expect((replayStart as HTMLButtonElement).disabled).toBe(true);
  expect(screen.queryByRole("dialog", { name: "Reset current mission" })).toBeNull();
  expect(screen.getByRole("button", { name: "Reload dashboard" })).toBeDefined();
  expect(fetcher).not.toHaveBeenCalled();
  expect(runtime.current.accepted).not.toHaveBeenCalled();
});

test("routes synchronous duplicate start activations through the mutation owner", async () => {
  // Arrange
  const { bootstrap, boundaries } = readyProductionInputs();
  let release: ((response: Response) => void) | undefined;
  const fetcher = vi.fn(
    () =>
      new Promise<Response>((resolve) => {
        release = resolve;
      }),
  );
  vi.stubGlobal("fetch", fetcher);
  const runtime = renderFakeProductionDashboard(bootstrap, boundaries);
  const start = await screen.findByRole("button", { name: "Start wilderness mission" });
  await vi.waitUntil(() => !(start as HTMLButtonElement).disabled);

  // Act
  await act(async () => {
    start.click();
    start.click();
    await Promise.resolve();
  });
  const duplicateOutcome = (
    await screen.findByText("Duplicate start submission ignored · start already pending")
  ).textContent;
  release?.(
    new Response(
      '{"declaredCount":23,"declaredOnlyCount":3,"missionId":"mission-production","mode":"degradedLive","operationVersion":"dashboard-start-response/v1","runId":"run-production","simulatedCount":20}',
      { status: 202 },
    ),
  );
  await vi.waitUntil(() => runtime.current?.accepted.mock.calls.length === 1);

  // Assert
  expect(duplicateOutcome).toBe("Duplicate start submission ignored · start already pending");
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(runtime.current?.accepted).toHaveBeenCalledTimes(1);
});

test("selects isolated replay and starts it through the authenticated production mutation", async () => {
  // Arrange
  const { bootstrap, boundaries } = readyProductionInputs();
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve(
        new Response(
          '{"declaredCount":23,"declaredOnlyCount":3,"mode":"replay","operationVersion":"dashboard-start-response/v1","sessionId":"session-production-0001","simulatedCount":20}',
          { status: 202 },
        ),
      ),
    ),
  );
  const runtime = renderFakeProductionDashboard(bootstrap, boundaries);
  const replayMode = await screen.findByRole("radio", { name: "Isolated replay" });

  // Act
  fireEvent.click(replayMode);
  const startReplay = await screen.findByRole("button", { name: "Start replay" });
  fireEvent.click(startReplay);
  await vi.waitUntil(() => runtime.current?.accepted.mock.calls.length === 1);

  // Assert
  expect(runtime.current?.accepted).toHaveBeenCalledWith(
    expect.objectContaining({ mode: "replay", sessionId: "session-production-0001" }),
  );
});

test("resumes the replay session owned by the first validated production snapshot", async () => {
  // Arrange
  const { bootstrap, catalog, readiness: liveReadiness, snapshot } = readyProductionInputs();
  const replayBundle = replayFixture().inputs.find(({ channel }) => channel === "replay-bundle");
  if (replayBundle === undefined) {
    throw new Error("production replay-resume fixture is incomplete");
  }
  const liveSource = new ManualSource();
  const replaySource = new ManualSource();
  const replaySessions: string[] = [];
  const fetcher = vi.fn((url: string) => {
    if (url === "/api/v1/readiness?mode=replay") {
      return Promise.resolve(
        new Response(
          '{"mode":"replay","readinessVersion":"dashboard-readiness/v1","ready":true,"reasons":[]}',
          { status: 200 },
        ),
      );
    }
    return Promise.resolve(
      new Response(url.includes("readiness") ? liveReadiness.raw : catalog.raw, { status: 200 }),
    );
  });
  render(
    <DashboardApplication
      productionBootstrap={bootstrap}
      productionRuntimeFactory={(options) =>
        new ProductionDashboardRuntime({
          ...options,
          fetcher,
          liveSourceFactory: () => liveSource,
          replaySourceFactory: (sessionId) => {
            replaySessions.push(sessionId);
            return replaySource;
          },
        })
      }
    />,
  );
  await vi.waitUntil(() => liveSource.opened);
  await screen.findByText(/23 declared = 20 simulated \+ 3 declared only/i);
  const replaySnapshot = await replayOwnedSnapshot(snapshot);

  // Act
  await liveSource.emit(replaySnapshot);
  await vi.waitUntil(() => replaySource.opened);
  await replaySource.emit(replayBundle);
  await vi.waitUntil(
    () => screen.getByRole("status", { name: "Operating mode" }).textContent === "ISOLATED REPLAY",
  );

  // Assert
  expect(replaySessions).toEqual(["session-production-0001"]);
  expect(fetcher.mock.calls.map(([url]) => url)).toContain("/api/v1/readiness?mode=replay");
  expect(await screen.findByRole("region", { name: "Replay controls" })).toBeDefined();
  expect(screen.queryByRole("button", { name: "Reload dashboard" })).toBeNull();
});

test("honors an operator who reaffirms live mode before replay resume settles", async () => {
  // Arrange
  const { bootstrap, catalog, readiness, snapshot } = readyProductionInputs();
  const liveSource = new ManualSource();
  const replaySource = new ManualSource();
  const fetcher = vi.fn((url: string) =>
    Promise.resolve(
      new Response(
        url === "/api/v1/scenarios"
          ? catalog.raw
          : url.includes("mode=replay")
            ? '{"mode":"replay","readinessVersion":"dashboard-readiness/v1","ready":true,"reasons":[]}'
            : readiness.raw,
        { status: 200 },
      ),
    ),
  );
  render(
    <DashboardApplication
      productionBootstrap={bootstrap}
      productionRuntimeFactory={(options) =>
        new ProductionDashboardRuntime({
          ...options,
          fetcher,
          liveSourceFactory: () => liveSource,
          replaySourceFactory: () => replaySource,
        })
      }
    />,
  );
  await vi.waitUntil(() => liveSource.opened);
  await screen.findByText(/23 declared = 20 simulated \+ 3 declared only/i);
  const liveMode = screen.getByRole("radio", { name: "Degraded live simulation" });

  // Act
  fireEvent.click(liveMode);
  await liveSource.emit(await replayOwnedSnapshot(snapshot));
  await new Promise<void>((resolve) => {
    globalThis.setTimeout(resolve, 0);
  });

  // Assert
  expect((liveMode as HTMLInputElement).checked).toBe(true);
  expect(screen.getByRole("status", { name: "Operating mode" }).textContent).toBe(
    "DEGRADED LIVE SIMULATION",
  );
  expect(replaySource.opened).toBe(false);
  expect(screen.getByRole("button", { name: "Start wilderness mission" })).toBeDefined();
});

test("keeps live start reachable when the shared runtime currently points to replay", async () => {
  // Arrange
  const { bootstrap, boundaries, snapshot } = readyProductionInputs();
  const fetcher = vi.fn(() =>
    Promise.resolve(
      new Response(
        '{"declaredCount":23,"declaredOnlyCount":3,"missionId":"mission-live-successor","mode":"degradedLive","operationVersion":"dashboard-start-response/v1","runId":"run-live-successor","simulatedCount":20}',
        { status: 202 },
      ),
    ),
  );
  vi.stubGlobal("fetch", fetcher);
  const runtime = renderFakeProductionDashboard(bootstrap, boundaries);
  await screen.findByRole("button", { name: "Start wilderness mission" });
  const initialSource = new ManualSource();
  runtime.current?.replaceSource(initialSource, "degradedLive");
  await initialSource.emit(snapshot);
  const replacementSource = new ManualSource();
  runtime.current?.replaceSource(replacementSource, "degradedLive");
  const incompatible = await replayOwnedSnapshot(snapshot);

  // Act
  await replacementSource.emit(incompatible);
  await vi.waitUntil(
    () =>
      screen.getByRole("status", { name: "Connection" }).textContent === "AWAITING LIVE SNAPSHOT",
  );
  const startButton = screen.getByRole("button", { name: "Start wilderness mission" });
  const resetButton = screen.getByRole("button", { name: "Reset mission" });
  if (!(startButton instanceof HTMLButtonElement) || !(resetButton instanceof HTMLButtonElement)) {
    throw new TypeError("production mission controls are not buttons");
  }
  fireEvent.click(startButton);
  await vi.waitUntil(() => runtime.current?.accepted.mock.calls.length === 1);

  // Assert
  expect(screen.getByRole("status", { name: "Dashboard state" }).textContent).toBe(
    "Replay session active · start live mission to switch",
  );
  expect(screen.getByRole("status", { name: "Current mission" }).textContent).toBe(
    "Previous live context · mission-synthetic-0001 · PLANNED",
  );
  expect(screen.getByRole("alert").textContent).toContain("start a live mission to switch");
  expect(startButton.disabled).toBe(false);
  expect(resetButton.disabled).toBe(true);
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(runtime.current?.accepted).toHaveBeenCalledWith(
    expect.objectContaining({
      missionId: "mission-live-successor",
      mode: "degradedLive",
      runId: "run-live-successor",
    }),
  );
});

test("locks mutations when the first live snapshot differs from the bootstrap runtime", async () => {
  // Arrange
  const inputs = readyProductionInputs();
  const { liveSource } = await renderLiveProductionDashboard(inputs);
  const { snapshot } = inputs;
  const changed = JSON.parse(snapshot.raw) as Record<string, unknown>;
  changed["runtimeId"] = "runtime-restarted-0002";

  // Act
  await liveSource.emit({ ...snapshot, raw: JSON.stringify(changed) });
  await vi.waitUntil(
    () => screen.getByRole("status", { name: "Connection" }).textContent === "STALE RUNTIME",
  );
  const startButton = screen.getByRole("button", { name: "Start wilderness mission" });
  const resetButton = screen.getByRole("button", { name: "Reset mission" });

  // Assert
  expect(screen.getByRole("status", { name: "Dashboard state" }).textContent).toBe(
    "Runtime changed · reload required",
  );
  expect(screen.getByRole("button", { name: "Reload dashboard" })).toBeDefined();
  expect((startButton as HTMLButtonElement).disabled).toBe(true);
  expect((resetButton as HTMLButtonElement).disabled).toBe(true);
});

test("renders a validated typed 503 readiness body from the production runtime", async () => {
  // Arrange
  const { bootstrap, catalog } = readyProductionInputs();
  const unavailable = JSON.stringify({
    mode: "degradedLive",
    readinessVersion: "dashboard-readiness/v1",
    ready: false,
    reasons: ["recorder-capture-unavailable"],
  });
  const fetcher = vi.fn((url: string) =>
    Promise.resolve(
      new Response(url.includes("readiness") ? unavailable : catalog.raw, {
        status: url.includes("readiness") ? 503 : 200,
      }),
    ),
  );

  // Act
  render(
    <DashboardApplication
      productionBootstrap={bootstrap}
      productionRuntimeFactory={(options) =>
        new ProductionDashboardRuntime({
          ...options,
          fetcher,
          liveSourceFactory: () => new ManualSource(),
        })
      }
    />,
  );
  await vi.waitUntil(
    () => screen.getByRole("status", { name: "Readiness" }).textContent === "UNAVAILABLE",
  );

  // Assert
  expect(screen.getByRole("status", { name: "Dashboard state" }).textContent).toBe(
    "Dashboard unavailable",
  );
  expect(screen.getByRole("status", { name: "Readiness blockers" }).textContent).toContain(
    "Recorder capture unavailable",
  );
});

test("renders the validated production runtime anchor for operator and screenshot evidence", async () => {
  // Arrange
  const inputs = readyProductionInputs();
  const { liveSource } = await renderLiveProductionDashboard(inputs);
  const { snapshot } = inputs;

  // Act
  await liveSource.emit(snapshot);
  await vi.waitUntil(() => screen.getByTestId("runtime-id").textContent === "Runtime …tic-0001");
  const runtimeIdentity = screen.getByTestId("runtime-id");

  // Assert
  expect(runtimeIdentity.textContent).toBe("Runtime …tic-0001");
  expect(runtimeIdentity.getAttribute("aria-label")).toBe(
    "Runtime identifier runtime-synthetic-0001",
  );
  expect(runtimeIdentity.getAttribute("title")).toBe("runtime-synthetic-0001");
});
