import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { fixtureForState, replayFixture } from "../tests/e2e/support/dashboard-fixtures";
import type { DashboardResetResponse, DashboardStartResponse } from "./contracts/generated";
import { DashboardApplication } from "./dashboard-app";
import type { DashboardSourceInput } from "./sources/event-source";
import type {
  ProductionDashboardRuntimeOptions,
  ProductionRuntimePort,
} from "./sources/production-runtime";

vi.mock("./components/search-map", () => ({
  SearchMap: () => <section aria-label="Search map">Replay reset map</section>,
}));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

class ReplayResetRuntime implements ProductionRuntimePort {
  readonly acceptedMutation =
    vi.fn<(response: DashboardResetResponse | DashboardStartResponse) => void>();
  disposed = false;
  private readonly consume: ProductionDashboardRuntimeOptions["consumeBoundary"];
  private readonly initialInputs: readonly DashboardSourceInput[];

  constructor(
    options: ProductionDashboardRuntimeOptions,
    initialInputs: readonly DashboardSourceInput[],
  ) {
    this.consume = options.consumeBoundary;
    this.initialInputs = [options.bootstrap, ...initialInputs];
  }

  dispose(): void {
    this.disposed = true;
  }

  observeSnapshotRun = vi.fn();

  emit(input: DashboardSourceInput): Promise<void> {
    return this.consume(input);
  }

  resnapshot = vi.fn();

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
    for (const input of this.initialInputs) await this.consume(input);
  }
}

test("creates a fresh replay session through the guarded production reset mutation", async () => {
  // Arrange
  const initial = fixtureForState("ready").inputs;
  const bootstrap = initial.find(({ channel }) => channel === "bootstrap");
  const boundaries = initial.filter(({ name }) => ["readiness", "scenario-catalog"].includes(name));
  const replayBundle = replayFixture().inputs.find(({ channel }) => channel === "replay-bundle");
  if (bootstrap === undefined || replayBundle === undefined) {
    throw new Error("replay reset integration fixture is incomplete");
  }
  const responses = [
    {
      declaredCount: 23,
      declaredOnlyCount: 3,
      mode: "replay",
      operationVersion: "dashboard-start-response/v1",
      sessionId: "session-replay-initial",
      simulatedCount: 20,
    },
    {
      declaredCount: 23,
      declaredOnlyCount: 3,
      mode: "replay",
      operationVersion: "dashboard-reset-response/v1",
      sessionId: "session-replay-fresh",
      simulatedCount: 20,
    },
  ];
  const fetcher = vi.fn<(input: string, init: RequestInit) => Promise<Response>>(() => {
    const response = responses.shift();
    if (response === undefined) throw new Error("unexpected replay mutation");
    return Promise.resolve(new Response(JSON.stringify(response), { status: 202 }));
  });
  vi.stubGlobal("fetch", fetcher);
  let runtime: ReplayResetRuntime | undefined;
  render(
    <DashboardApplication
      productionBootstrap={bootstrap}
      productionRuntimeFactory={(options) => {
        runtime = new ReplayResetRuntime(options, boundaries);
        return runtime;
      }}
    />,
  );
  const replayMode = await screen.findByRole("radio", { name: "Isolated replay" });
  fireEvent.click(replayMode);
  const startReplay = await screen.findByRole("button", { name: "Start replay" });
  fireEvent.click(startReplay);
  await vi.waitUntil(() => runtime?.acceptedMutation.mock.calls.length === 1);
  await runtime?.emit(replayBundle);
  const newSession = await screen.findByRole("button", { name: "New replay session" });
  const digestBefore = screen.getByRole("status", { name: "Current mission digest" }).textContent;

  // Act
  fireEvent.click(newSession);
  const dialog = screen.getByRole("dialog", { name: "Start a new replay session" });
  const consequences = dialog.textContent;
  fireEvent.click(within(dialog).getByRole("button", { name: "Create new replay session" }));
  await vi.waitUntil(() => runtime?.acceptedMutation.mock.calls.length === 2);

  // Assert
  expect(consequences).toMatch(/fresh cursor-zero replay session/i);
  expect(consequences).toMatch(/does not mutate an operational mission/i);
  expect(fetcher.mock.calls.map(([input]) => input)).toEqual([
    "/api/v1/scenarios/wilderness-missing-person/start",
    "/api/v1/scenarios/current/reset",
  ]);
  expect(fetcher.mock.calls[1]?.[1]).toMatchObject({ body: "{}", method: "POST" });
  expect(runtime?.acceptedMutation).toHaveBeenLastCalledWith(
    expect.objectContaining({ mode: "replay", sessionId: "session-replay-fresh" }),
  );
  expect(screen.getByRole("status", { name: "Current mission digest" }).textContent).toBe(
    digestBefore,
  );
  expect(screen.getByRole("status", { name: "Mutation outcome" }).textContent).toContain(
    "session-replay-fresh",
  );
});
