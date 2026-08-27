const fleetStatusKeys = [
  "completedTickCount",
  "controlVersion",
  "missionId",
  "runId",
  "state",
  "telemetryPublicationCount",
] as const;
const maximumTelemetryPublications = 280;
const unsignedIntegerOutput = /^(?:0|[1-9][0-9]*)\n?$/u;
const applicationIdentifierPattern = /^(?:[a-z0-9]|[a-z0-9][a-z0-9-]{0,62}[a-z0-9])$/u;
const resetHistoryKeys = [
  "auditEventCount",
  "currentMissionId",
  "currentRunId",
  "latestAuditOrdinal",
  "predecessorLifecycle",
  "predecessorMissionId",
  "predecessorRunCount",
  "retainedAuditOrdinal",
  "successorLifecycle",
  "successorMissionId",
  "successorPredecessorMissionId",
  "successorRunId",
] as const;

export interface FleetCompletionEvidence {
  readonly completedTickCount: number;
  readonly telemetryPublicationCount: number;
}

export interface ResetHistoryEvidence {
  readonly auditEventCount: number;
  readonly currentMissionId: string;
  readonly currentRunId: string;
  readonly latestAuditOrdinal: number;
  readonly predecessorLifecycle: "EXHAUSTED";
  readonly predecessorMissionId: string;
  readonly predecessorRunCount: 1;
  readonly retainedAuditOrdinal: number;
  readonly successorLifecycle: "PLANNED";
  readonly successorMissionId: string;
  readonly successorPredecessorMissionId: string;
  readonly successorRunId: string;
}

function objectDocument(value: unknown): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error("fleet completion evidence was not an object");
  }
  return value as Record<string, unknown>;
}

function safeNonnegativeInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}

export function isApplicationIdentifier(value: unknown): value is string {
  return typeof value === "string" && applicationIdentifierPattern.test(value);
}

export function parseFleetCompletionEvidence(
  raw: string,
  expectedMissionId: string,
  expectedRunId: string,
): FleetCompletionEvidence {
  let value: unknown;
  try {
    value = JSON.parse(raw) as unknown;
  } catch (error) {
    throw new Error("fleet completion evidence was not JSON", { cause: error });
  }
  const document = objectDocument(value);
  const keys = Object.keys(document).sort();
  if (
    keys.length !== fleetStatusKeys.length ||
    keys.some((key, index) => key !== fleetStatusKeys[index])
  ) {
    throw new Error("fleet completion evidence shape was malformed");
  }
  if (
    document["controlVersion"] !== 1 ||
    document["state"] !== "EXHAUSTED" ||
    !safeNonnegativeInteger(document["completedTickCount"]) ||
    !safeNonnegativeInteger(document["telemetryPublicationCount"])
  ) {
    throw new Error("fleet completion evidence values were malformed");
  }
  if (document["missionId"] !== expectedMissionId || document["runId"] !== expectedRunId) {
    throw new Error("fleet completion evidence identity was malformed");
  }
  return {
    completedTickCount: document["completedTickCount"],
    telemetryPublicationCount: document["telemetryPublicationCount"],
  };
}

export function parseRecorderTelemetryReceiptCount(raw: string): number {
  if (!unsignedIntegerOutput.test(raw)) {
    throw new Error("recorder telemetry receipt count was malformed");
  }
  const count = Number(raw.trim());
  if (!Number.isSafeInteger(count) || count < 0 || count > maximumTelemetryPublications) {
    throw new Error("recorder telemetry receipt count was malformed");
  }
  return count;
}

export function parseResetHistoryEvidence(
  raw: string,
  expectedPredecessorMissionId: string,
  expectedSuccessorMissionId: string,
  expectedSuccessorRunId: string,
  expectedRetainedAuditOrdinal: number,
): ResetHistoryEvidence {
  if (
    !isApplicationIdentifier(expectedPredecessorMissionId) ||
    !isApplicationIdentifier(expectedSuccessorMissionId) ||
    !isApplicationIdentifier(expectedSuccessorRunId)
  ) {
    throw new Error("reset history evidence identity was malformed");
  }
  let value: unknown;
  try {
    value = JSON.parse(raw) as unknown;
  } catch (error) {
    throw new Error("reset history evidence was not JSON", { cause: error });
  }
  const document = objectDocument(value);
  const keys = Object.keys(document).sort();
  if (
    keys.length !== resetHistoryKeys.length ||
    keys.some((key, index) => key !== resetHistoryKeys[index])
  ) {
    throw new Error("reset history evidence shape was malformed");
  }
  const auditEventCount = document["auditEventCount"];
  const latestAuditOrdinal = document["latestAuditOrdinal"];
  const retainedAuditOrdinal = document["retainedAuditOrdinal"];
  if (
    !safeNonnegativeInteger(auditEventCount) ||
    !safeNonnegativeInteger(latestAuditOrdinal) ||
    !safeNonnegativeInteger(retainedAuditOrdinal) ||
    expectedRetainedAuditOrdinal < 1 ||
    !Number.isSafeInteger(expectedRetainedAuditOrdinal) ||
    retainedAuditOrdinal !== expectedRetainedAuditOrdinal ||
    auditEventCount < retainedAuditOrdinal ||
    latestAuditOrdinal < retainedAuditOrdinal ||
    document["predecessorLifecycle"] !== "EXHAUSTED" ||
    document["predecessorRunCount"] !== 1 ||
    document["successorLifecycle"] !== "PLANNED"
  ) {
    throw new Error("reset history evidence values were malformed");
  }
  if (
    document["predecessorMissionId"] !== expectedPredecessorMissionId ||
    document["successorPredecessorMissionId"] !== expectedPredecessorMissionId ||
    document["successorMissionId"] !== expectedSuccessorMissionId ||
    document["currentMissionId"] !== expectedSuccessorMissionId ||
    document["successorRunId"] !== expectedSuccessorRunId ||
    document["currentRunId"] !== expectedSuccessorRunId
  ) {
    throw new Error("reset history evidence identity was malformed");
  }
  return {
    auditEventCount,
    currentMissionId: expectedSuccessorMissionId,
    currentRunId: expectedSuccessorRunId,
    latestAuditOrdinal,
    predecessorLifecycle: "EXHAUSTED",
    predecessorMissionId: expectedPredecessorMissionId,
    predecessorRunCount: 1,
    retainedAuditOrdinal,
    successorLifecycle: "PLANNED",
    successorMissionId: expectedSuccessorMissionId,
    successorPredecessorMissionId: expectedPredecessorMissionId,
    successorRunId: expectedSuccessorRunId,
  };
}
