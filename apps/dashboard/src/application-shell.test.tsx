import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import type { DashboardSnapshot } from "./contracts/generated";
import type { ProposalDecisionSubmitter } from "./operator/mutation-client";
import {
  evidenceFixture,
  PROPOSAL_DIGEST,
  proposalFixture,
  type ApprovalEvent,
  type EvidenceEvent,
  type ProposalEvent,
} from "../tests/unit-support/proposal-fixtures";
import { ApplicationShell, currentProposalBinding } from "./application-shell";

afterEach(() => {
  cleanup();
});

function proposal(): ProposalEvent {
  return proposalFixture({ fleetBound: true });
}

function evidence(): EvidenceEvent {
  return evidenceFixture({ fleetBound: true });
}

function approval(proposalDigest = PROPOSAL_DIGEST): ApprovalEvent {
  return {
    kind: "operatorApproval",
    eventClass: "APPROVAL",
    mission: "mission-synthetic-0001",
    time: "2026-08-25T12:05:00.000Z",
    data: {
      operatorApprovalVersion: 1,
      approvalId: "approval-synthetic-0001",
      operatorId: "operator-synthetic-0001",
      issuedAt: "2026-08-25T12:05:00.000Z",
      proposalId: "proposal-synthetic-0001",
      proposalDigest,
      proposalVersion: 1,
      evidenceDecisionId: "decision-synthetic-0001",
      evidenceDecisionDigest: "7".repeat(64),
      evidenceDecisionVersion: 1,
      action: {
        commandType: "escalate-rescue",
        droneId: "drone-sim-01",
        latitudeMicrodegrees: 45_123_456,
        longitudeMicrodegrees: -75_123_456,
      },
      decision: "reject",
    },
  };
}

function snapshot(mode: "degradedLive" | "replay" = "degradedLive"): DashboardSnapshot {
  return {
    snapshotVersion: "dashboard-snapshot/v1",
    runtimeId: "runtime-synthetic-0001",
    cursor: "cursor-synthetic-0001",
    digest: "1".repeat(64),
    latestEventDigest: "2".repeat(64),
    currentRun:
      mode === "replay"
        ? { mode: "replay", sessionId: "session-synthetic-0001" }
        : {
            mode: "degradedLive",
            missionId: "mission-synthetic-0001",
            runId: "run-synthetic-0001",
          },
    state: {
      canonicalizationVersion: 1,
      stateVersion: 1,
      currentMission: {
        identifier: "mission-synthetic-0001",
        lifecycle: "SEARCHING",
        predecessorIdentifier: null,
      },
      fleet: [
        { identifier: "drone-vision-01", participation: "DECLARED_ONLY" },
        {
          identifier: "drone-sim-02",
          participation: "SIMULATED",
          connectivity: "DEGRADED",
          telemetry: null,
        },
        {
          identifier: "drone-sim-01",
          participation: "SIMULATED",
          connectivity: "CONNECTED",
          telemetry: {
            latitudeMicrodegrees: 45_120_000,
            longitudeMicrodegrees: -75_120_000,
            batteryPercent: 91,
            altitudeMetres: 80,
            headingDegrees: 90,
            groundSpeedCentimetresPerSecond: 850,
          },
        },
      ],
      latestAuditOrdinal: 2,
      sectors: [{ identifier: "sector-01", state: "ASSIGNED", assignedMemberId: "drone-sim-01" }],
    },
    timeline: [
      { auditOrdinal: 1, event: proposal() },
      { auditOrdinal: 2, event: evidence() },
    ],
  };
}

test("renders broker-backed mission, fleet, timeline, and exact operator binding", async () => {
  // Arrange
  const submit = vi.fn<ProposalDecisionSubmitter>();

  // Act
  render(
    <ApplicationShell
      mode="degradedLive"
      snapshot={snapshot()}
      sourceState="connected"
      submitDecision={submit}
    />,
  );

  // Assert
  expect(screen.getByRole("status", { name: "Operating mode" }).textContent).toContain(
    "LIVE BROKER DATA · DEGRADED LIVE SIMULATION",
  );
  expect(screen.getByRole("status", { name: "Dashboard state" }).textContent).toBe(
    "Live broker stream connected",
  );
  expect(screen.getByRole("heading", { name: "mission-synthetic-0001" })).toBeTruthy();
  expect(screen.getByRole("table", { name: "Fleet status" }).textContent).toContain("drone-sim-01");
  const rows = screen.getAllByRole("row").map((row) => row.textContent);
  expect(rows[1]).toContain("drone-sim-01");
  expect(rows[2]).toContain("drone-sim-02");
  expect(rows[3]).toContain("drone-vision-01");
  expect(screen.getByRole("list", { name: "Mission timeline" }).textContent).toContain(
    "proposal-synthetic-0001",
  );
  expect(await screen.findByRole("button", { name: "Approve exact rescue proposal" })).toBeTruthy();
});

test.each([
  ["loading", "Loading broker-backed mission state"],
  ["degraded", "Degraded live · broker delivery unavailable"],
  ["retrying", "Connection interrupted · retrying"],
  ["offline", "Offline · last validated mission state retained"],
  ["recovered", "Recovered · validated broker stream resumed"],
  ["exhausted", "Mission exhausted · critical facts retained"],
  ["contractFailure", "Contract failure · last validated mission state retained"],
] as const)("renders the explicit %s source state", (sourceState, expected) => {
  // Arrange
  const expectedState = expected;

  // Act
  render(<ApplicationShell mode="degradedLive" sourceState={sourceState} />);

  // Assert
  expect(screen.getByRole("status", { name: "Dashboard state" }).textContent).toBe(expectedState);
});

test("renders recorded proposal evidence in isolated replay with no writer or mutation control", () => {
  // Arrange
  const replaySnapshot = snapshot("replay");

  // Act
  render(<ApplicationShell mode="replay" snapshot={replaySnapshot} sourceState="connected" />);

  // Assert
  expect(screen.getByRole("status", { name: "Operating mode" }).textContent).toBe(
    "ISOLATED REPLAY · READ ONLY",
  );
  expect(screen.getByText("Recorded proposal — replay is read only")).toBeTruthy();
  expect(screen.queryByRole("button", { name: /proposal/iu })).toBeNull();
});

test("binds only matching evidence and records only an exact operator decision", () => {
  // Arrange
  const selectedProposal = proposal();
  const mismatchedEvidence = {
    ...evidence(),
    data: { ...evidence().data, proposalId: "proposal-synthetic-other" },
  } as EvidenceEvent;
  const mismatchedApproval = approval("8".repeat(64));
  const exactApproval = approval();
  const timeline = [
    { auditOrdinal: 1, event: evidence() },
    { auditOrdinal: 2, event: approval() },
    { auditOrdinal: 3, event: selectedProposal },
    { auditOrdinal: 4, event: mismatchedEvidence },
    { auditOrdinal: 5, event: evidence() },
    { auditOrdinal: 6, event: mismatchedApproval },
    { auditOrdinal: 7, event: exactApproval },
  ];

  // Act
  const binding = currentProposalBinding(timeline);
  const reset = currentProposalBinding([
    ...timeline,
    {
      auditOrdinal: 8,
      event: {
        ...selectedProposal,
        data: { ...selectedProposal.data, proposalId: "proposal-new" },
      },
    },
  ]);

  // Assert
  expect(binding).toMatchObject({
    decisionRecorded: true,
    evidence: { data: { evidenceDecisionId: "decision-synthetic-0001" } },
    proposal: { data: { proposalId: "proposal-synthetic-0001" } },
  });
  expect(reset).toBeUndefined();
});

test("renders an empty mission and the details of non-proposal durable facts", () => {
  // Arrange
  const emptySnapshot = snapshot();
  emptySnapshot.state.currentMission = null;
  emptySnapshot.timeline = [
    { auditOrdinal: 1, event: approval() },
    {
      auditOrdinal: 2,
      event: {
        kind: "operatorCommand",
        eventClass: "COMMAND",
        mission: "mission-synthetic-0001",
        time: "2026-08-25T12:06:00.000Z",
        data: {
          operatorCommandVersion: 1,
          commandId: "command-synthetic-0001",
          operatorId: "operator-synthetic-0001",
          action: {
            commandType: "assign-sector",
            droneId: "drone-sim-01",
            sectorId: "sector-01",
          },
        },
      },
    },
    {
      auditOrdinal: 3,
      event: {
        kind: "commandResult",
        eventClass: "COMMAND",
        mission: "mission-synthetic-0001",
        time: "2026-08-25T12:07:00.000Z",
        data: {
          droneId: "drone-sim-01",
          commandId: "command-synthetic-0001",
          outcome: "succeeded",
        },
      },
    },
    {
      auditOrdinal: 4,
      event: {
        kind: "connectivityChanged",
        eventClass: "CONNECTIVITY",
        mission: "mission-synthetic-0001",
        time: "2026-08-25T12:08:00.000Z",
        data: { droneId: "drone-sim-01", connectivity: "OFFLINE" },
      },
    },
  ];

  // Act
  render(<ApplicationShell mode="degradedLive" snapshot={emptySnapshot} sourceState="retrying" />);
  const timeline = screen.getByRole("list", { name: "Mission timeline" });

  // Assert
  expect(screen.getByRole("heading", { name: "No current mission" })).toBeTruthy();
  expect(timeline.textContent).toContain("reject · proposal-synthetic-0001");
  expect(timeline.textContent).toContain("command-synthetic-0001");
  expect(timeline.textContent).toContain("command-synthetic-0001 · succeeded");
  expect(timeline.textContent).toContain("connectivityChanged");
});

test("shows a recorded decision and disables its exact controls after a contract failure", async () => {
  // Arrange
  const decidedSnapshot = snapshot();
  decidedSnapshot.timeline.push({ auditOrdinal: 3, event: approval() });

  // Act
  render(
    <ApplicationShell
      mode="degradedLive"
      snapshot={decidedSnapshot}
      sourceState="contractFailure"
      submitDecision={vi.fn<ProposalDecisionSubmitter>()}
    />,
  );
  const approve = await screen.findByRole("button", { name: "Approve exact rescue proposal" });

  // Assert
  expect(screen.getByText("A decision is already recorded for this exact proposal.")).toBeTruthy();
  expect(approve).toHaveProperty("disabled", true);
  expect(screen.getByRole("button", { name: "Reject exact rescue proposal" })).toHaveProperty(
    "disabled",
    true,
  );
});
