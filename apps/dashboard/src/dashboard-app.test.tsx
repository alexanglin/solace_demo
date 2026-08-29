import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import {
  fixtureForState,
  malformedBoundaryInputs,
  replayCheckpoints,
  replayFixture,
  resilienceFaultInputs,
  type DashboardSourceInput,
  type DashboardSourceScript,
} from "../tests/e2e/support/dashboard-fixtures";
import type { DashboardTestHarness } from "../tests/e2e/support/dashboard-harness";
import type {
  DashboardEvent,
  DashboardReducedState,
  OrderedDashboardEvent,
} from "./contracts/generated";
import { canonicalBytes, replayStateDigest } from "./domain/canonical";
import { DashboardApplication } from "./dashboard-app";

vi.mock("./components/search-map", () => ({
  SearchMap: ({ onMarkerSample }: { readonly onMarkerSample: (identifier: string) => void }) => (
    <section aria-label="Search map">
      <button
        onClick={() => {
          onMarkerSample("drone-sim-01");
        }}
        type="button"
      >
        Fit mission
      </button>
      <p>Synthetic map data · Rendered with MapLibre GL JS</p>
    </section>
  ),
}));

afterEach(() => {
  cleanup();
  delete window.__AERIAL_RESCUE_DASHBOARD_TEST__;
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

async function renderFixture(sourceScript: DashboardSourceScript): Promise<DashboardTestHarness> {
  const harness: DashboardTestHarness = {
    appliedRevision: 0,
    snapshotRequests: 0,
    sourceDisposals: 0,
    sourceRevision: 1,
    sourceScript,
  };
  window.__AERIAL_RESCUE_DASHBOARD_TEST__ = harness;
  render(<DashboardApplication />);
  await waitFor(() => {
    expect(harness.appliedRevision).toBe(1);
  });
  return harness;
}

async function appendInputs(
  harness: DashboardTestHarness,
  inputs: readonly DashboardSourceInput[],
  replace = false,
): Promise<void> {
  harness.sourceRevision += 1;
  window.dispatchEvent(
    new CustomEvent("aerial-rescue-dashboard:test-source-inputs", {
      detail: { inputs, replace, revision: harness.sourceRevision },
    }),
  );
  await waitFor(() => {
    expect(harness.appliedRevision).toBe(harness.sourceRevision);
  });
}

const PROPOSAL_DIGEST = "e3b6c8a4c2a075031275dc288bad3f780c992338617978dcb5863bc51aa6f761";
const EVIDENCE_DIGEST = "3c3775801fc324695e0f1eca64cf8fa91d6f213eec7968c71ffe8db61ce6abe3";

function applicationTimelineEvents(): readonly DashboardEvent[] {
  const mission = "mission-synthetic-0001";
  const shared = { mission, time: "2026-08-24T12:01:00.000Z" };
  return [
    {
      ...shared,
      data: {
        action: {
          commandType: "assign-sector",
          droneId: "drone-sim-01",
          sectorId: "sector-01",
        },
        commandId: "command-synthetic-0001",
        operatorCommandVersion: 1,
        operatorId: "operator-synthetic-0001",
      },
      eventClass: "COMMAND",
      kind: "operatorCommand",
    },
    {
      ...shared,
      data: {
        action: {
          commandType: "escalate-rescue",
          droneId: "drone-sim-01",
          latitudeMicrodegrees: 44_475_000,
          longitudeMicrodegrees: -79_245_000,
        },
        approvalId: "approval-synthetic-0001",
        decision: "reject",
        evidenceDecisionDigest: EVIDENCE_DIGEST,
        evidenceDecisionId: "decision-synthetic-0001",
        evidenceDecisionVersion: 1,
        issuedAt: "2026-08-24T12:00:59.000Z",
        operatorApprovalVersion: 1,
        operatorId: "operator-synthetic-0001",
        proposalDigest: PROPOSAL_DIGEST,
        proposalId: "proposal-synthetic-0001",
        proposalVersion: 1,
      },
      eventClass: "APPROVAL",
      kind: "operatorApproval",
    },
    {
      ...shared,
      data: {
        agentName: "VisionAgent",
        canonicalizationVersion: 1,
        commandType: "escalate-rescue",
        droneId: "drone-sim-01",
        latitudeMicrodegrees: 44_475_000,
        longitudeMicrodegrees: -79_245_000,
        proposalDigest: PROPOSAL_DIGEST,
        proposalId: "proposal-synthetic-0001",
        proposalType: "candidate-location",
        proposalVersion: 1,
        sourceEventDigest: "9".repeat(64),
        sourceEventId: "0190a1b2-3c4d-7e8f-9a0b-1c2d3e4f5a6c",
        sourceInvocationId: "invocation-synthetic-0001",
      },
      eventClass: "EVIDENCE",
      kind: "agentProposal",
    },
    {
      ...shared,
      data: {
        canonicalizationVersion: 1,
        evidenceDecisionId: "decision-synthetic-0001",
        evidenceDecisionVersion: 1,
        outcome: "manual-review",
        proposalDigest: PROPOSAL_DIGEST,
        proposalId: "proposal-synthetic-0001",
        proposalVersion: 1,
        reason: "insufficient-live-sources",
      },
      eventClass: "EVIDENCE",
      kind: "evidenceDecision",
    },
    {
      ...shared,
      data: {
        detail: "bounded synthetic observation",
        droneId: "drone-sim-01",
        latitudeMicrodegrees: 44_475_000,
        longitudeMicrodegrees: -79_245_000,
        observation: "thermal-anomaly",
      },
      eventClass: "EVIDENCE",
      kind: "salientObservation",
    },
    {
      ...shared,
      data: {
        commandId: "command-synthetic-0001",
        droneId: "drone-sim-01",
        sectorId: "sector-01",
      },
      eventClass: "COMMAND",
      kind: "droneCommand",
    },
    {
      ...shared,
      data: {
        commandId: "command-synthetic-0001",
        droneId: "drone-sim-01",
        outcome: "succeeded",
      },
      eventClass: "COMMAND",
      kind: "commandResult",
    },
    {
      ...shared,
      data: {
        actuated: false,
        commandType: "escalate-rescue",
        operation: "command-authority",
        outcome: "refused",
        refusal: "unknown-operation",
        requestId: "b3f1c2d4-5e6a-4b7c-8d9e-0f1a2b3c4d5e",
        rpcVersion: 1,
      },
      eventClass: "AUDIT",
      kind: "gatewayResponse",
    },
    {
      ...shared,
      data: {
        agentName: "VisionAgent",
        auditVersion: 1,
        correlationId: "correlation-synthetic-0001",
        invocationId: "invocation-synthetic-0001",
        outcome: "abstained",
        reason: "timeout",
        recordId: "audit-synthetic-0001",
        recordType: "proposal-normalization",
      },
      eventClass: "AUDIT",
      kind: "auditRecord",
    },
  ];
}

async function applicationTimelineInputs(
  sourceScript: DashboardSourceScript,
): Promise<readonly DashboardSourceInput[]> {
  const snapshotInput = sourceScript.inputs.find(({ name }) => name === "snapshot");
  if (snapshotInput === undefined) throw new Error("running fixture is missing its snapshot");
  const snapshot = JSON.parse(snapshotInput.raw) as { state: DashboardReducedState };
  let state = snapshot.state;
  const inputs: DashboardSourceInput[] = [];
  for (const event of applicationTimelineEvents()) {
    const ordered: OrderedDashboardEvent = {
      auditOrdinal: state.latestAuditOrdinal + 1,
      event,
    };
    state = { ...state, latestAuditOrdinal: ordered.auditOrdinal };
    inputs.push({
      channel: "sse-frame",
      name: "dashboard-event",
      raw: JSON.stringify({
        cursor: `cursor-application-event-${String(ordered.auditOrdinal)}`,
        digest: await replayStateDigest(state),
        event: ordered,
        frameVersion: "ordered-dashboard-event-frame/v1",
      }),
    });
  }
  return inputs;
}

test("composes live server, mission, and presentation owners through semantic controls", async () => {
  // Arrange
  await renderFixture(fixtureForState("running"));
  const fleet = screen.getByRole("table", { name: "Mission fleet" });

  // Act
  fireEvent.click(within(fleet).getByRole("button", { name: "drone-sim-12" }));
  fireEvent.click(screen.getByRole("button", { name: "Offline" }));
  fireEvent.click(screen.getByRole("button", { name: "Fit mission" }));
  fireEvent.click(screen.getByRole("button", { name: "Collapse fleet rail" }));
  fireEvent.click(screen.getByRole("button", { name: "Expand fleet rail" }));
  fireEvent.click(screen.getByRole("button", { name: "All" }));
  fireEvent.click(
    within(screen.getByRole("table", { name: "Mission fleet" })).getByRole("button", {
      name: "drone-comms-03",
    }),
  );

  // Assert
  expect(screen.getByRole("status", { name: "Dashboard state" }).textContent).toBe(
    "Mission searching",
  );
  expect(screen.getByTestId("mission-id").textContent).toBe("mission-synthetic-0001");
  expect(screen.getByRole("status", { name: "Map focus" }).textContent).toContain("drone-comms-03");
  expect(screen.getByRole("region", { name: "Drone detail" }).textContent).toContain(
    "No telemetry expected",
  );
  expect(screen.getByRole("region", { name: "Drone detail" }).textContent).toContain(
    "DECLARED ONLY — NOT EXECUTED",
  );
  expect(screen.getByRole("region", { name: "Drone detail" }).textContent).toContain(
    "communications",
  );
  expect(screen.getByRole("status", { name: "Marker interpolation" }).textContent).toBe(
    "Sample applied for drone-sim-01",
  );
});

test("projects every validated application event as a timeline-only broker fact", async () => {
  // Arrange
  const source = fixtureForState("running");
  const harness = await renderFixture(source);
  const inputs = await applicationTimelineInputs(source);

  // Act
  await appendInputs(harness, inputs);
  const timeline = screen.getByRole("list", { name: "Ordered mission events" });

  // Assert
  expect(timeline.textContent).toContain("Operator command · command-synthetic-0001");
  expect(timeline.textContent).toContain("Rejection · proposal-synthetic-0001");
  expect(timeline.textContent).toContain("Proposal · proposal-synthetic-0001");
  expect(timeline.textContent).toContain("Evidence · decision-synthetic-0001 · manual-review");
  expect(timeline.textContent).toContain("drone-sim-01 · thermal-anomaly");
  expect(timeline.textContent).toContain("Drone command · command-synthetic-0001");
  expect(timeline.textContent).toContain("Command result · command-synthetic-0001 · succeeded");
  expect(timeline.textContent).toContain(
    "Gateway · b3f1c2d4-5e6a-4b7c-8d9e-0f1a2b3c4d5e · refused",
  );
  expect(timeline.textContent).toContain("Audit · proposal-normalization · audit-synthetic-0001");
  expect(screen.getByRole("status", { name: "Latest audit ordinal" }).textContent).toBe("60");
});

test.each([
  ["loading", "Loading scenario catalog"],
  ["empty", "No scenarios available"],
  ["ready", "Ready to start"],
  ["starting", "Starting wilderness mission"],
  ["resetting", "Resetting mission"],
  ["retrying", "Connection interrupted · retrying"],
  ["offline", "Dashboard offline"],
  ["recovered", "Connection recovered"],
  ["staleRuntime", "Runtime changed · reload required"],
  ["contractFailure", "Contract validation failed"],
  ["exhausted", "Mission exhausted"],
  ["aborted", "Mission aborted"],
] as const)("renders the %s dashboard state", async (fixtureState, label) => {
  // Arrange
  await renderFixture(fixtureForState(fixtureState));

  // Act
  const state = screen.getByRole("status", { name: "Dashboard state" }).textContent;

  // Assert
  expect(state).toBe(label);
});

test("retains the live checkpoint across malformed boundaries and reducer refusals", async () => {
  // Arrange
  const harness = await renderFixture(fixtureForState("running"));
  const missionBefore = screen.getByRole("status", { name: "Current mission" }).textContent;

  // Act
  for (const boundary of ["bootstrap", "readiness", "scenarioCatalog", "replayBundle"] as const) {
    await appendInputs(harness, malformedBoundaryInputs(boundary));
  }
  for (const fault of ["ordinalGap", "ordinalRegression", "digestDivergence"] as const) {
    await appendInputs(harness, resilienceFaultInputs(fault));
  }

  // Assert
  expect(screen.getByRole("status", { name: "Current mission" }).textContent).toBe(missionBefore);
  expect(screen.getByRole("alert").textContent).toMatch(/contract validation failed/i);
});

test("acknowledges overload disposal and resnapshot without discarding mission state", async () => {
  // Arrange
  const harness = await renderFixture(fixtureForState("running"));

  // Act
  await appendInputs(harness, resilienceFaultInputs("streamOverloaded"));

  // Assert
  expect(harness.snapshotRequests).toBe(1);
  expect(harness.sourceDisposals).toBe(1);
  expect(screen.getByRole("status", { name: "Dashboard state" }).textContent).toContain(
    "resynchronizing",
  );
});

test("steps, seeks, restarts, and paces an integrity-verified replay", async () => {
  // Arrange
  await renderFixture(replayFixture());
  vi.useFakeTimers();
  const controls = screen.getByRole("region", { name: "Replay controls" });
  const progress = within(controls).getByRole("slider", { name: "Replay progress" });

  // Act
  fireEvent.click(within(controls).getByRole("button", { name: "Step forward" }));
  fireEvent.change(progress, { target: { value: "47" } });
  fireEvent.click(within(controls).getByRole("button", { name: "Restart replay" }));
  fireEvent.click(within(controls).getByRole("button", { name: "2×" }));
  fireEvent.click(within(controls).getByRole("button", { name: "Play replay" }));
  await vi.advanceTimersByTimeAsync(1_000);
  fireEvent.click(within(controls).getByRole("button", { name: "Pause replay" }));

  // Assert
  expect((progress as HTMLInputElement).value).toBe("2");
  expect(screen.getByRole("status", { name: "Expected final digest" }).textContent).toBe(
    replayCheckpoints.at(-1)?.digest,
  );
});

test("refuses invalid replay integrity and divergent final state witnesses", async () => {
  // Arrange
  const harness = await renderFixture(replayFixture({ checksum: "0".repeat(64) }));

  // Act
  const integrityAlert = screen.getByRole("alert").textContent;
  await appendInputs(harness, replayFixture({ expectedFinalDigest: "f".repeat(64) }).inputs, true);
  const progress = within(screen.getByRole("region", { name: "Replay controls" })).getByRole(
    "slider",
    { name: "Replay progress" },
  );
  fireEvent.change(progress, { target: { value: "47" } });

  // Assert
  expect(integrityAlert).toContain("integrity check failed");
  expect(screen.getByRole("alert").textContent).toContain("digest mismatch");
  expect(screen.getByRole("status", { name: "Replay digest verification" }).textContent).toBe(
    "Refused",
  );
});

test("submits accepted start and reset operations without mutating reduced mission state", async () => {
  // Arrange
  const responses = [
    {
      declaredCount: 23,
      declaredOnlyCount: 3,
      missionId: "mission-accepted",
      mode: "degradedLive",
      operationVersion: "dashboard-start-response/v1",
      runId: "run-accepted",
      simulatedCount: 20,
    },
    {
      declaredCount: 23,
      declaredOnlyCount: 3,
      missionId: "mission-successor",
      mode: "degradedLive",
      operationVersion: "dashboard-reset-response/v1",
      predecessorMissionId: "mission-synthetic-0001",
      runId: "run-successor",
      simulatedCount: 20,
    },
  ];
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.resolve(new Response(JSON.stringify(responses.shift()), { status: 202 }))),
  );
  await renderFixture(fixtureForState("ready"));
  const missionBefore = screen.getByRole("status", { name: "Current mission" }).textContent;

  // Act
  fireEvent.click(screen.getByRole("button", { name: "Start wilderness mission" }));
  await vi.waitUntil(() => screen.queryByRole("status", { name: "Mutation outcome" }) !== null);
  fireEvent.click(screen.getByRole("button", { name: "Reset mission" }));
  fireEvent.click(screen.getByRole("button", { name: "Confirm reset" }));
  await vi.waitUntil(
    () =>
      screen
        .queryByRole("status", { name: "Mutation outcome" })
        ?.textContent.includes("Reset accepted") === true,
  );

  // Assert
  expect(screen.getByRole("status", { name: "Current mission" }).textContent).toBe(missionBefore);
  expect(fetch).toHaveBeenCalledTimes(2);
});

test.each([
  [
    401,
    { errorCode: "AUTHENTICATION_FAILED", errorVersion: "dashboard-error/v1", message: "stale" },
    "reload required",
  ],
  [
    409,
    {
      errorCode: "CANCELLATION_NOT_ESTABLISHED",
      errorVersion: "dashboard-error/v1",
      message: "bounded",
    },
    "Cancellation was not established",
  ],
] as const)("renders a typed mutation refusal with status %s", async (status, body, message) => {
  // Arrange
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.resolve(new Response(JSON.stringify(body), { status }))),
  );
  await renderFixture(fixtureForState(status === 401 ? "ready" : "running"));

  // Act
  if (status === 401) {
    fireEvent.click(screen.getByRole("button", { name: "Start wilderness mission" }));
  } else {
    fireEvent.click(screen.getByRole("button", { name: "Reset mission" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm reset" }));
  }
  await vi.waitUntil(() => screen.queryByRole("alert")?.textContent.includes(message) === true);

  // Assert
  expect(screen.getByRole("alert").textContent).toContain(message);
});

test.each([
  ["ordinalGap", /audit ordinal gap/i],
  ["ordinalRegression", /audit ordinal regression/i],
  ["digestDivergence", /state digest divergence/i],
] as const)("maps the %s reducer refusal without losing the checkpoint", async (fault, message) => {
  // Arrange
  const harness = await renderFixture(fixtureForState("running"));
  const ordinalBefore = screen.getByRole("status", { name: "Latest audit ordinal" }).textContent;

  // Act
  await appendInputs(harness, resilienceFaultInputs(fault));

  // Assert
  expect(screen.getByRole("alert").textContent).toMatch(message);
  expect(screen.getByRole("status", { name: "Latest audit ordinal" }).textContent).toBe(
    ordinalBefore,
  );
});

test("covers reduced-motion, prepared telemetry, and dialog keyboard presentation branches", async () => {
  // Arrange
  vi.stubGlobal("matchMedia", () => ({ matches: true }));
  await renderFixture(fixtureForState("ready"));
  const fleet = screen.getByRole("table", { name: "Mission fleet" });

  // Act
  fireEvent.focus(within(fleet).getByRole("button", { name: "drone-sim-01" }));
  fireEvent.click(screen.getByRole("button", { name: "Reset mission" }));
  const dialog = screen.getByRole("dialog", { name: "Reset current mission" });
  const buttons = within(dialog).getAllByRole("button");
  buttons.at(-1)?.focus();
  fireEvent.keyDown(document, { key: "Tab" });
  buttons[0]?.focus();
  fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
  fireEvent.keyDown(document, { key: "Unrelated" });
  fireEvent.keyDown(document, { key: "Escape" });

  // Assert
  expect(screen.getByRole("status", { name: "Telemetry motion" }).textContent).toContain(
    "Reduced motion",
  );
  expect(screen.getByRole("region", { name: "Drone detail" }).textContent).toContain(
    "Waiting for first sample",
  );
  expect(screen.queryByRole("dialog", { name: "Reset current mission" })).toBeNull();
});

test.each([
  [
    202,
    {
      declaredCount: 23,
      declaredOnlyCount: 3,
      missionId: "invalid",
      mode: "degradedLive",
      operationVersion: "dashboard-start-response/v1",
      runId: "invalid",
      simulatedCount: 19,
    },
    /contract validation failed/i,
  ],
  [
    503,
    {
      errorCode: "DEPENDENCY_UNAVAILABLE",
      errorVersion: "dashboard-error/v1",
      message: "scenario dependency is not ready",
    },
    /dependency is not ready/i,
  ],
  [
    202,
    {
      declaredCount: 23,
      declaredOnlyCount: 3,
      mode: "replay",
      operationVersion: "dashboard-start-response/v1",
      sessionId: "session-replay-accepted",
      simulatedCount: 20,
    },
    /session-replay-accepted/i,
  ],
] as const)(
  "renders additional validated start outcome branches",
  async (status, body, expected) => {
    // Arrange
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(new Response(JSON.stringify(body), { status }))),
    );
    await renderFixture(fixtureForState("ready"));

    // Act
    fireEvent.click(screen.getByRole("button", { name: "Start wilderness mission" }));
    await vi.waitUntil(() => {
      const outcome = screen.queryByRole("status", { name: "Mutation outcome" });
      const alert = screen.queryByRole("alert");
      return expected.test(`${outcome?.textContent ?? ""} ${alert?.textContent ?? ""}`);
    });

    // Assert
    expect(fetch).toHaveBeenCalledOnce();
  },
);

test("refuses unknown fixture channels and malformed mutation progress", async () => {
  // Arrange
  const harness = await renderFixture(fixtureForState("running"));

  // Act
  await appendInputs(harness, [
    { channel: "mutation-result", name: "pending", raw: "{" },
    { channel: "mutation-result", name: "pending", raw: "null" },
    { channel: "mutation-result", name: "pending", raw: '{"operation":"other","phase":"pending"}' },
    { channel: "mutation-result", name: "pending", raw: '{"operation":"start","phase":"done"}' },
    { channel: "http-response", name: "unknown-response", raw: "{}" },
  ]);

  // Assert
  expect(screen.getByRole("alert").textContent).toContain("unknown-response");
});

async function checksum(value: unknown): Promise<string> {
  const bytes = canonicalBytes(value);
  const digest = await crypto.subtle.digest("SHA-256", new Uint8Array(bytes).buffer);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function replayWithMutation(
  mutate: (bundle: Record<string, unknown>) => void,
): Promise<DashboardSourceScript> {
  const source = replayFixture();
  const bundleInput = source.inputs.find(({ channel }) => channel === "replay-bundle");
  if (bundleInput === undefined) throw new Error("replay fixture is incomplete");
  const bundle = JSON.parse(bundleInput.raw) as Record<string, unknown>;
  mutate(bundle);
  const integrity = bundle["integrity"] as Record<string, unknown>;
  const material = {
    ...bundle,
    integrity: Object.fromEntries(Object.entries(integrity).filter(([key]) => key !== "checksum")),
  };
  integrity["checksum"] = await checksum(material);
  return {
    ...source,
    inputs: source.inputs.map((input) =>
      input === bundleInput ? { ...input, raw: JSON.stringify(bundle) } : input,
    ),
  };
}

test.each([
  async () =>
    replayWithMutation((bundle) => {
      const state = bundle["initialState"] as { fleet: unknown[] };
      state.fleet.reverse();
    }),
  async () =>
    replayWithMutation((bundle) => {
      const events = bundle["events"] as { auditOrdinal: number }[];
      if (events[0] !== undefined) events[0].auditOrdinal = 2;
    }),
] as const)("refuses a checksum-valid but semantically invalid replay", async (candidate) => {
  // Arrange
  const source = await candidate();

  // Act
  await renderFixture(source);

  // Assert
  expect(screen.getByRole("alert").textContent).toContain("integrity check failed");
});

test("refuses a checksum-valid replay for a different catalog scenario", async () => {
  // Arrange
  const source = await replayWithMutation((bundle) => {
    bundle["scenarioId"] = "different-wilderness-scenario";
  });

  // Act
  await renderFixture(source);

  // Assert
  expect(screen.getByRole("alert").textContent).toContain(
    "Contract validation failed · replay bundle",
  );
  expect(screen.queryByRole("region", { name: "Replay controls" })).toBeNull();
});
