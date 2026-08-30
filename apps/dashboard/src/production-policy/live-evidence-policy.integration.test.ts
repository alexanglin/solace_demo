import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

import { expect, test } from "vitest";

import {
  parseFleetCompletionEvidence,
  parseRecorderTelemetryReceiptCount,
  parseResetHistoryEvidence,
} from "../../tests/production/support/live-evidence";
import {
  buildResetHistoryQuery,
  buildTelemetryReceiptQuery,
} from "../../tests/production/support/mission-control-runtime";

const support = resolve(
  import.meta.dirname,
  "../../tests/production/support/mission-control-runtime.ts",
);
const liveWorkflow = resolve(import.meta.dirname, "../../tests/production/mission-control.spec.ts");

test("keeps fleet publication and recorder receipt as separate validated instruments", () => {
  // Arrange
  const fleetStatus = JSON.stringify({
    completedTickCount: 14,
    controlVersion: 1,
    missionId: "mission-synthetic-0001",
    runId: "run-synthetic-0001",
    state: "EXHAUSTED",
    telemetryPublicationCount: 280,
  });
  const receiptOutput = "273\n";

  // Act
  const fleet = parseFleetCompletionEvidence(
    fleetStatus,
    "mission-synthetic-0001",
    "run-synthetic-0001",
  );
  const recorderTelemetryReceiptCount = parseRecorderTelemetryReceiptCount(receiptOutput);

  // Assert
  expect(fleet).toEqual({
    completedTickCount: 14,
    telemetryPublicationCount: 280,
  });
  expect(recorderTelemetryReceiptCount).toBe(273);
  expect(recorderTelemetryReceiptCount).not.toBe(fleet.telemetryPublicationCount);
});

test("refuses malformed, cross-run, and out-of-bound live evidence", () => {
  // Arrange
  const wrongRun = JSON.stringify({
    completedTickCount: 14,
    controlVersion: 1,
    missionId: "mission-synthetic-0001",
    runId: "run-other",
    state: "EXHAUSTED",
    telemetryPublicationCount: 280,
  });
  const unexpectedField = JSON.stringify({
    completedTickCount: 14,
    controlVersion: 1,
    missionId: "mission-synthetic-0001",
    runId: "run-synthetic-0001",
    state: "EXHAUSTED",
    telemetryPublicationCount: 280,
    untrusted: true,
  });

  // Act
  const wrongRunParse = () =>
    parseFleetCompletionEvidence(wrongRun, "mission-synthetic-0001", "run-synthetic-0001");
  const unexpectedFieldParse = () =>
    parseFleetCompletionEvidence(unexpectedField, "mission-synthetic-0001", "run-synthetic-0001");
  const oversizedReceiptParse = () => parseRecorderTelemetryReceiptCount("281\n");

  // Assert
  expect(wrongRunParse).toThrow("fleet completion evidence identity was malformed");
  expect(unexpectedFieldParse).toThrow("fleet completion evidence shape was malformed");
  expect(oversizedReceiptParse).toThrow("recorder telemetry receipt count was malformed");
});

test("builds a directly executable recorder receipt query from bounded identities", () => {
  // Arrange
  const missionId = "mission-synthetic-0001";
  const runId = "run-synthetic-0001";

  // Act
  const query = buildTelemetryReceiptQuery(missionId, runId);
  const maliciousIdentity = () =>
    buildTelemetryReceiptQuery("mission'; DROP TABLE audit_record", runId);

  // Assert
  expect(query).toContain("live_run.run_id = 'run-synthetic-0001'");
  expect(query).toContain("broker_event.audit_mission_id = 'mission-synthetic-0001'");
  expect(query).not.toContain(":'run_id'");
  expect(query).not.toContain(":'mission_id'");
  expect(maliciousIdentity).toThrow("live evidence identity was malformed");
});

test("collects private fleet status and recorder-linked audit evidence without a public probe", async () => {
  // Arrange
  const [runtimeSource, workflowSource] = await Promise.all([
    readFile(support, "utf8"),
    readFile(liveWorkflow, "utf8"),
  ]);

  // Act
  const evidenceBoundary = `${runtimeSource}\n${workflowSource}`;

  // Assert
  expect(runtimeSource).toContain('"scenario-service"');
  expect(runtimeSource).toContain("settings_from_environment");
  expect(runtimeSource).toContain("FleetHttpClient");
  expect(runtimeSource).toContain("FROM dashboard_broker_event AS broker_event");
  expect(runtimeSource).toContain("JOIN audit_record AS audit");
  expect(runtimeSource).toContain("audit.kind = 'aerial-rescue.v1.drone.telemetry'");
  expect(workflowSource).toContain("collectLiveMissionEvidence");
  expect(workflowSource).toContain("completedTickCount).toBe(14)");
  expect(workflowSource).toContain("telemetryPublicationCount).toBe(280)");
  expect(workflowSource).toContain("bestEffortTelemetryReceiptCount).toBeGreaterThan(0)");
  expect(evidenceBoundary).not.toContain("fleet-control-secret");
  expect(evidenceBoundary).not.toContain("/api/v1/acceptance");
  expect(evidenceBoundary).not.toContain("page.route");
});

test("validates retained predecessor history and the current planned successor", () => {
  // Arrange
  const output = JSON.stringify({
    auditEventCount: 47,
    currentMissionId: "mission-successor",
    currentRunId: "run-successor",
    latestAuditOrdinal: 47,
    predecessorLifecycle: "EXHAUSTED",
    predecessorMissionId: "mission-predecessor",
    predecessorRunCount: 1,
    retainedAuditOrdinal: 46,
    successorLifecycle: "PLANNED",
    successorMissionId: "mission-successor",
    successorPredecessorMissionId: "mission-predecessor",
    successorRunId: "run-successor",
  });

  // Act
  const evidence = parseResetHistoryEvidence(
    output,
    "mission-predecessor",
    "mission-successor",
    "run-successor",
    46,
  );
  const query = buildResetHistoryQuery(
    "mission-predecessor",
    "mission-successor",
    "run-successor",
    46,
  );

  // Assert
  expect(evidence).toEqual({
    auditEventCount: 47,
    currentMissionId: "mission-successor",
    currentRunId: "run-successor",
    latestAuditOrdinal: 47,
    predecessorLifecycle: "EXHAUSTED",
    predecessorMissionId: "mission-predecessor",
    predecessorRunCount: 1,
    retainedAuditOrdinal: 46,
    successorLifecycle: "PLANNED",
    successorMissionId: "mission-successor",
    successorPredecessorMissionId: "mission-predecessor",
    successorRunId: "run-successor",
  });
  expect(query).toContain("FROM dashboard_mission");
  expect(query).toContain("FROM dashboard_run");
  expect(query).toContain("FROM dashboard_current_run");
  expect(query).toContain("FROM audit_record");
  expect(query).toContain("ordinal = 46");
});

test("refuses missing history, cross-successor evidence, and unsafe reset-history inputs", () => {
  // Arrange
  const validDocument = {
    auditEventCount: 47,
    currentMissionId: "mission-successor",
    currentRunId: "run-successor",
    latestAuditOrdinal: 47,
    predecessorLifecycle: "EXHAUSTED",
    predecessorMissionId: "mission-predecessor",
    predecessorRunCount: 1,
    retainedAuditOrdinal: 46,
    successorLifecycle: "PLANNED",
    successorMissionId: "mission-successor",
    successorPredecessorMissionId: "mission-predecessor",
    successorRunId: "run-successor",
  };
  const missingAnchor = JSON.stringify({ ...validDocument, retainedAuditOrdinal: null });
  const crossSuccessor = JSON.stringify({
    ...validDocument,
    currentMissionId: "mission-other",
    successorMissionId: "mission-other",
  });

  // Act
  const missingAnchorParse = () =>
    parseResetHistoryEvidence(
      missingAnchor,
      "mission-predecessor",
      "mission-successor",
      "run-successor",
      46,
    );
  const crossSuccessorParse = () =>
    parseResetHistoryEvidence(
      crossSuccessor,
      "mission-predecessor",
      "mission-successor",
      "run-successor",
      46,
    );
  const unsafeQuery = () =>
    buildResetHistoryQuery(
      "mission-predecessor'; DROP TABLE audit_record",
      "mission-successor",
      "run-successor",
      46,
    );

  // Assert
  expect(missingAnchorParse).toThrow("reset history evidence values were malformed");
  expect(crossSuccessorParse).toThrow("reset history evidence identity was malformed");
  expect(unsafeQuery).toThrow("reset history evidence input was malformed");
});

test("wires private reset-history evidence into the guarded production workflow", async () => {
  // Arrange
  const [runtimeSource, workflowSource] = await Promise.all([
    readFile(support, "utf8"),
    readFile(liveWorkflow, "utf8"),
  ]);

  // Act
  const evidenceBoundary = `${runtimeSource}\n${workflowSource}`;

  // Assert
  expect(runtimeSource).toContain("collectResetHistoryEvidence");
  expect(runtimeSource).toContain("FROM dashboard_current_run");
  expect(runtimeSource).toContain("FROM dashboard_mission");
  expect(runtimeSource).toContain("FROM audit_record");
  expect(workflowSource).toContain("collectResetHistoryEvidence");
  expect(workflowSource).toContain("retainedAuditOrdinal");
  expect(workflowSource).toContain("predecessorRunCount).toBe(1)");
  expect(evidenceBoundary).not.toContain("/api/v1/acceptance");
  expect(evidenceBoundary).not.toContain("page.route");
});
