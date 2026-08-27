import type {
  DashboardEvent,
  DashboardReducedState,
  DashboardReplayBundle,
  DashboardSnapshot,
  OrderedDashboardEvent,
} from "../contracts/generated";
import {
  digestMatches,
  orderedDashboardEventDigest,
  replayStateDigest,
  validateOrdinalWitness,
} from "./canonical";

const LOWERCASE_SHA256 = /^[0-9a-f]{64}$/u;
const IDENTIFIER = /^(?:[a-z0-9]|[a-z0-9][a-z0-9-]{0,62}[a-z0-9])$/u;
const INSTANT =
  /^(?!0000)[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]\.[0-9]{3}Z$/u;
const MISSION_LIFECYCLES = new Set(["PLANNED", "SEARCHING", "EXHAUSTED", "ABORTED"]);
const CONNECTIVITY_STATES = new Set(["CONNECTED", "DEGRADED", "OFFLINE"]);
const SECTOR_STATES = new Set(["UNASSIGNED", "ASSIGNED", "AT_RISK", "SEARCHED"]);
const DASHBOARD_EVENT_KINDS = new Set([
  "droneTelemetry",
  "connectivityChanged",
  "missionLifecycle",
  "sectorLifecycle",
]);
const textEncoder = new TextEncoder();

type FleetMember = DashboardReducedState["fleet"][number];
type Sector = DashboardReducedState["sectors"][number];
type CurrentMission = NonNullable<DashboardReducedState["currentMission"]>;

export interface PreparedReducerMission {
  readonly identifier: string;
  readonly predecessorIdentifier: string | null;
  readonly simulatedMemberIds: readonly string[];
  readonly declaredOnlyMemberIds: readonly string[];
  readonly sectorIds: readonly string[];
}

export interface ReducerCheckpoint {
  readonly state: DashboardReducedState;
  readonly latestEventDigest: string | null;
}

export type ReducerRefusal =
  | "DUPLICATE_MEMBER"
  | "DUPLICATE_SECTOR"
  | "WITNESS_FORM"
  | "ORDINAL_WITNESS"
  | "NONCANONICAL_ANCHOR_STATE"
  | "ORDINAL_DIVERGENCE"
  | "ORDINAL_REGRESSION"
  | "ORDINAL_GAP"
  | "MISSION_UNPREPARED"
  | "MISSION_MISMATCH"
  | "UNKNOWN_MEMBER"
  | "DECLARED_ONLY_MEMBER"
  | "UNKNOWN_SECTOR"
  | "ASSIGNMENT_FORBIDDEN"
  | "ASSIGNMENT_REQUIRED"
  | "INVALID_ASSIGNEE"
  | "EVENT_DATA"
  | "UNPROJECTED"
  | "SERVER_DIGEST_FORM"
  | "SERVER_DIGEST_MISMATCH";

export interface ReducerFailure {
  readonly code: ReducerRefusal;
  readonly attribute: string;
  readonly value: unknown;
}

export type CheckpointAnchorResult =
  | { readonly ok: true; readonly checkpoint: ReducerCheckpoint }
  | { readonly ok: false; readonly failure: ReducerFailure };

export type FoldResult =
  | {
      readonly ok: true;
      readonly disposition: "APPLIED" | "DUPLICATE";
      readonly checkpoint: ReducerCheckpoint;
    }
  | {
      readonly ok: false;
      readonly checkpoint: ReducerCheckpoint;
      readonly failure: ReducerFailure;
    };

function failure(code: ReducerRefusal, attribute: string, value: unknown): ReducerFailure {
  return { attribute, code, value };
}

function refused(checkpoint: ReducerCheckpoint, detail: ReducerFailure): FoldResult {
  return { checkpoint, failure: detail, ok: false };
}

export function checkpointFromAnchor(
  state: DashboardReducedState,
  latestEventDigest: string | null,
): CheckpointAnchorResult {
  if (latestEventDigest !== null && !LOWERCASE_SHA256.test(latestEventDigest)) {
    return {
      failure: failure("WITNESS_FORM", "latestEventDigest", latestEventDigest),
      ok: false,
    };
  }
  if (!anchorStateIsCanonical(state)) {
    return {
      failure: failure("NONCANONICAL_ANCHOR_STATE", "state", state),
      ok: false,
    };
  }
  if (!validateOrdinalWitness(state.latestAuditOrdinal, latestEventDigest).ok) {
    return {
      failure: failure("ORDINAL_WITNESS", "latestEventDigest", latestEventDigest),
      ok: false,
    };
  }
  return { checkpoint: { latestEventDigest, state }, ok: true };
}

export function emptyReducerCheckpoint(): ReducerCheckpoint {
  return {
    latestEventDigest: null,
    state: {
      canonicalizationVersion: 1,
      currentMission: null,
      fleet: [],
      latestAuditOrdinal: 0,
      sectors: [],
      stateVersion: 1,
    },
  };
}

export function initializePreparedReducerCheckpoint(
  prepared: PreparedReducerMission,
): CheckpointAnchorResult {
  const repeatedMember = duplicateIdentifier([
    ...prepared.simulatedMemberIds,
    ...prepared.declaredOnlyMemberIds,
  ]);
  if (repeatedMember !== null) {
    return {
      failure: failure("DUPLICATE_MEMBER", "identifier", repeatedMember),
      ok: false,
    };
  }
  const repeatedSector = duplicateIdentifier(prepared.sectorIds);
  if (repeatedSector !== null) {
    return {
      failure: failure("DUPLICATE_SECTOR", "identifier", repeatedSector),
      ok: false,
    };
  }
  const fleet: FleetMember[] = [
    ...prepared.simulatedMemberIds.map((identifier): FleetMember => ({
      connectivity: "CONNECTED",
      identifier,
      participation: "SIMULATED",
      telemetry: null,
    })),
    ...prepared.declaredOnlyMemberIds.map((identifier): FleetMember => ({
      identifier,
      participation: "DECLARED_ONLY",
    })),
  ];
  const sectors: Sector[] = prepared.sectorIds.map((identifier) => ({
    assignedMemberId: null,
    identifier,
    state: "UNASSIGNED",
  }));
  const state: DashboardReducedState = {
    canonicalizationVersion: 1,
    currentMission: {
      identifier: prepared.identifier,
      lifecycle: "PLANNED",
      predecessorIdentifier: prepared.predecessorIdentifier,
    },
    fleet: sortedFleet(fleet),
    latestAuditOrdinal: 0,
    sectors: sortedSectors(sectors),
    stateVersion: 1,
  };
  return checkpointFromAnchor(state, null);
}

function duplicateIdentifier(identifiers: readonly string[]): string | null {
  const seen = new Set<string>();
  for (const identifier of identifiers) {
    if (seen.has(identifier)) {
      return identifier;
    }
    seen.add(identifier);
  }
  return null;
}

export async function checkpointFromSnapshot(
  snapshot: Pick<DashboardSnapshot, "digest" | "latestEventDigest" | "state">,
): Promise<CheckpointAnchorResult> {
  const anchored = checkpointFromAnchor(snapshot.state, snapshot.latestEventDigest);
  if (!anchored.ok) {
    return anchored;
  }
  const computedDigest = await replayStateDigest(snapshot.state);
  const comparison = digestMatches(snapshot.digest, computedDigest);
  if (!comparison.ok) {
    return {
      failure: failure("SERVER_DIGEST_FORM", "digest", snapshot.digest),
      ok: false,
    };
  }
  return comparison.matches
    ? anchored
    : {
        failure: failure("SERVER_DIGEST_MISMATCH", "digest", snapshot.digest),
        ok: false,
      };
}

export function checkpointFromReplayBundle(
  bundle: Pick<DashboardReplayBundle, "initialState" | "latestEventDigest">,
): CheckpointAnchorResult {
  return checkpointFromAnchor(bundle.initialState, bundle.latestEventDigest);
}

function identifierOrder(
  left: { readonly identifier: string },
  right: { readonly identifier: string },
) {
  const leftBytes = textEncoder.encode(left.identifier);
  const rightBytes = textEncoder.encode(right.identifier);
  const sharedLength = Math.min(leftBytes.length, rightBytes.length);
  for (let index = 0; index < sharedLength; index += 1) {
    const leftByte = leftBytes[index];
    const rightByte = rightBytes[index];
    if (leftByte !== rightByte) {
      return (leftByte ?? 0) - (rightByte ?? 0);
    }
  }
  return leftBytes.length - rightBytes.length;
}

function sortedFleet(fleet: readonly FleetMember[]): FleetMember[] {
  return [...fleet].sort(identifierOrder);
}

function sortedSectors(sectors: readonly Sector[]): Sector[] {
  return [...sectors].sort(identifierOrder);
}

function identifiersAreSortedAndUnique(
  values: readonly { readonly identifier: string }[],
): boolean {
  for (let index = 1; index < values.length; index += 1) {
    const previous = values[index - 1];
    const current = values[index];
    if (
      previous === undefined ||
      current === undefined ||
      identifierOrder(previous, current) >= 0
    ) {
      return false;
    }
  }
  return true;
}

function heldAssignmentIsValid(state: DashboardReducedState, sector: Sector): boolean {
  if (sector.state === "UNASSIGNED") {
    return sector.assignedMemberId === null;
  }
  if (sector.assignedMemberId === null) {
    return false;
  }
  return state.fleet.some(
    (member) =>
      member.identifier === sector.assignedMemberId && member.participation === "SIMULATED",
  );
}

function anchorStateIsCanonical(state: DashboardReducedState): boolean {
  if (
    state.currentMission === null &&
    (state.fleet.length !== 0 || state.sectors.length !== 0 || state.latestAuditOrdinal !== 0)
  ) {
    return false;
  }
  return (
    identifiersAreSortedAndUnique(state.fleet) &&
    identifiersAreSortedAndUnique(state.sectors) &&
    state.sectors.every((sector) => heldAssignmentIsValid(state, sector))
  );
}

function dataRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function safeInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value);
}

function integerInRange(value: unknown, minimum: number, maximum: number): value is number {
  return safeInteger(value) && value >= minimum && value <= maximum;
}

function hasExactMembers(value: Record<string, unknown>, members: readonly string[]): boolean {
  const observed = Object.keys(value).sort();
  const expected = [...members].sort();
  return (
    observed.length === expected.length && observed.every((key, index) => key === expected[index])
  );
}

function identifier(value: unknown): value is string {
  return typeof value === "string" && IDENTIFIER.test(value);
}

function telemetryDataIsValid(data: Record<string, unknown>): boolean {
  return (
    hasExactMembers(data, [
      "droneId",
      "latitudeMicrodegrees",
      "longitudeMicrodegrees",
      "batteryPercent",
      "altitudeMetres",
      "headingDegrees",
      "groundSpeedCentimetresPerSecond",
    ]) &&
    identifier(data["droneId"]) &&
    integerInRange(data["latitudeMicrodegrees"], -90_000_000, 90_000_000) &&
    integerInRange(data["longitudeMicrodegrees"], -180_000_000, 180_000_000) &&
    integerInRange(data["batteryPercent"], 0, 100) &&
    integerInRange(data["altitudeMetres"], -500, 20_000) &&
    integerInRange(data["headingDegrees"], 0, 359) &&
    integerInRange(data["groundSpeedCentimetresPerSecond"], 0, 10_000)
  );
}

function canonicalInstant(value: unknown): value is string {
  if (typeof value !== "string" || !INSTANT.test(value)) {
    return false;
  }
  const parsed = new Date(value);
  return Number.isFinite(parsed.getTime()) && parsed.toISOString() === value;
}

function commonEventFailure(event: unknown): ReducerFailure | null {
  const document = dataRecord(event);
  if (
    document === null ||
    !hasExactMembers(document, ["kind", "eventClass", "mission", "time", "data"])
  ) {
    return failure("EVENT_DATA", "event", event);
  }
  if (!identifier(document["mission"])) {
    return failure("EVENT_DATA", "mission", document["mission"]);
  }
  return canonicalInstant(document["time"])
    ? null
    : failure("EVENT_DATA", "time", document["time"]);
}

function sectorDataFailure(data: Record<string, unknown>): ReducerFailure | null {
  if (
    !hasExactMembers(data, ["sectorId", "state", "assignedMemberId"]) ||
    !identifier(data["sectorId"]) ||
    typeof data["state"] !== "string" ||
    !SECTOR_STATES.has(data["state"]) ||
    (data["assignedMemberId"] !== null && !identifier(data["assignedMemberId"]))
  ) {
    return failure("EVENT_DATA", "data", data);
  }
  if (data["state"] === "UNASSIGNED" && data["assignedMemberId"] !== null) {
    return failure("ASSIGNMENT_FORBIDDEN", "assignedMemberId", data["assignedMemberId"]);
  }
  if (data["state"] !== "UNASSIGNED" && data["assignedMemberId"] === null) {
    return failure("ASSIGNMENT_REQUIRED", "assignedMemberId", null);
  }
  return null;
}

function eventDataFailure(event: unknown): ReducerFailure | null {
  const eventDocument = dataRecord(event);
  if (eventDocument === null) {
    return failure("EVENT_DATA", "event", event);
  }
  const kind = eventDocument["kind"];
  if (typeof kind !== "string" || !DASHBOARD_EVENT_KINDS.has(kind)) {
    return failure("UNPROJECTED", "kind", kind);
  }
  const commonFailure = commonEventFailure(event);
  if (commonFailure !== null) {
    return commonFailure;
  }
  const validatedEvent = event as DashboardEvent;
  const data = dataRecord(validatedEvent.data);
  if (data === null) {
    return failure("EVENT_DATA", "data", validatedEvent.data);
  }
  const eventClass = (validatedEvent as unknown as Record<string, unknown>)["eventClass"];
  switch (validatedEvent.kind) {
    case "droneTelemetry":
      return eventClass === "TELEMETRY" && telemetryDataIsValid(data)
        ? null
        : failure("EVENT_DATA", "data", validatedEvent.data);
    case "connectivityChanged":
      return eventClass === "CONNECTIVITY" &&
        hasExactMembers(data, ["droneId", "connectivity"]) &&
        identifier(data["droneId"]) &&
        typeof data["connectivity"] === "string" &&
        CONNECTIVITY_STATES.has(data["connectivity"])
        ? null
        : failure("EVENT_DATA", "data", validatedEvent.data);
    case "missionLifecycle":
      return eventClass === "MISSION" &&
        hasExactMembers(data, ["lifecycle"]) &&
        typeof data["lifecycle"] === "string" &&
        MISSION_LIFECYCLES.has(data["lifecycle"])
        ? null
        : failure("EVENT_DATA", "data", validatedEvent.data);
    case "sectorLifecycle":
      return eventClass === "MISSION"
        ? sectorDataFailure(data)
        : failure("EVENT_DATA", "data", validatedEvent.data);
  }
}

function orderedEventBoundaryFailure(orderedEvent: unknown): ReducerFailure | null {
  const document = dataRecord(orderedEvent);
  if (
    document === null ||
    !hasExactMembers(document, ["auditOrdinal", "event"]) ||
    !integerInRange(document["auditOrdinal"], 1, Number.MAX_SAFE_INTEGER)
  ) {
    return failure("EVENT_DATA", "auditOrdinal", document?.["auditOrdinal"]);
  }
  return eventDataFailure(document["event"]);
}

function simulatedMember(
  state: DashboardReducedState,
  droneId: string,
): FleetMember | ReducerFailure {
  const member = state.fleet.find(({ identifier }) => identifier === droneId);
  if (member === undefined) {
    return failure("UNKNOWN_MEMBER", "droneId", droneId);
  }
  if (member.participation === "DECLARED_ONLY") {
    return failure("DECLARED_ONLY_MEMBER", "droneId", droneId);
  }
  return member;
}

function isFailure(value: object): value is ReducerFailure {
  return "code" in value;
}

function applyTelemetry(
  state: DashboardReducedState,
  event: Extract<DashboardEvent, { readonly kind: "droneTelemetry" }>,
): DashboardReducedState | ReducerFailure {
  const member = simulatedMember(state, event.data.droneId);
  if (isFailure(member)) {
    return member;
  }
  const telemetry = {
    altitudeMetres: event.data.altitudeMetres,
    batteryPercent: event.data.batteryPercent,
    groundSpeedCentimetresPerSecond: event.data.groundSpeedCentimetresPerSecond,
    headingDegrees: event.data.headingDegrees,
    latitudeMicrodegrees: event.data.latitudeMicrodegrees,
    longitudeMicrodegrees: event.data.longitudeMicrodegrees,
  };
  return {
    ...state,
    fleet: state.fleet.map((held) =>
      held.identifier === member.identifier && held.participation === "SIMULATED"
        ? { ...held, telemetry }
        : held,
    ),
  };
}

function applyConnectivity(
  state: DashboardReducedState,
  event: Extract<DashboardEvent, { readonly kind: "connectivityChanged" }>,
): DashboardReducedState | ReducerFailure {
  const member = simulatedMember(state, event.data.droneId);
  if (isFailure(member)) {
    return member;
  }
  return {
    ...state,
    fleet: state.fleet.map((held) =>
      held.identifier === member.identifier && held.participation === "SIMULATED"
        ? { ...held, connectivity: event.data.connectivity }
        : held,
    ),
  };
}

function applyMission(
  state: DashboardReducedState,
  currentMission: CurrentMission,
  event: Extract<DashboardEvent, { readonly kind: "missionLifecycle" }>,
): DashboardReducedState {
  return {
    ...state,
    currentMission: { ...currentMission, lifecycle: event.data.lifecycle },
  };
}

function sectorWithIdentifier(
  state: DashboardReducedState,
  sectorId: string,
): Sector | ReducerFailure {
  const sector = state.sectors.find(({ identifier }) => identifier === sectorId);
  return sector ?? failure("UNKNOWN_SECTOR", "sectorId", sectorId);
}

function assignmentFailure(
  state: DashboardReducedState,
  assignedMemberId: string | null,
): ReducerFailure | null {
  if (assignedMemberId === null) {
    return null;
  }
  const assignee = state.fleet.find(({ identifier }) => identifier === assignedMemberId);
  return assignee === undefined || assignee.participation === "DECLARED_ONLY"
    ? failure("INVALID_ASSIGNEE", "assignedMemberId", assignedMemberId)
    : null;
}

function applySector(
  state: DashboardReducedState,
  event: Extract<DashboardEvent, { readonly kind: "sectorLifecycle" }>,
): DashboardReducedState | ReducerFailure {
  const sector = sectorWithIdentifier(state, event.data.sectorId);
  if (isFailure(sector)) {
    return sector;
  }
  const assignment = assignmentFailure(state, event.data.assignedMemberId);
  if (assignment !== null) {
    return assignment;
  }
  return {
    ...state,
    sectors: state.sectors.map((held) =>
      held.identifier === sector.identifier
        ? {
            ...held,
            assignedMemberId: event.data.assignedMemberId,
            state: event.data.state,
          }
        : held,
    ),
  };
}

function applyEvent(
  state: DashboardReducedState,
  currentMission: CurrentMission,
  event: DashboardEvent,
): DashboardReducedState | ReducerFailure {
  switch (event.kind) {
    case "droneTelemetry":
      return applyTelemetry(state, event);
    case "connectivityChanged":
      return applyConnectivity(state, event);
    case "missionLifecycle":
      return applyMission(state, currentMission, event);
    case "sectorLifecycle":
      return applySector(state, event);
  }
}

function finalizedState(state: DashboardReducedState, latestAuditOrdinal: number) {
  return {
    ...state,
    fleet: sortedFleet(state.fleet),
    latestAuditOrdinal,
    sectors: sortedSectors(state.sectors),
  };
}

export async function foldOrderedDashboardEvent(
  checkpoint: ReducerCheckpoint,
  orderedEvent: OrderedDashboardEvent,
): Promise<FoldResult> {
  const boundaryFailure = orderedEventBoundaryFailure(orderedEvent);
  if (boundaryFailure !== null) {
    return refused(checkpoint, boundaryFailure);
  }
  const anchor = checkpointFromAnchor(checkpoint.state, checkpoint.latestEventDigest);
  if (!anchor.ok) {
    return refused(checkpoint, anchor.failure);
  }
  const latestOrdinal = checkpoint.state.latestAuditOrdinal;
  const incomingOrdinal = orderedEvent.auditOrdinal;
  if (incomingOrdinal === latestOrdinal) {
    const incomingDigest = await orderedDashboardEventDigest(orderedEvent);
    const comparison = digestMatches(checkpoint.latestEventDigest, incomingDigest);
    return comparison.ok && comparison.matches
      ? { checkpoint, disposition: "DUPLICATE", ok: true }
      : refused(checkpoint, failure("ORDINAL_DIVERGENCE", "auditOrdinal", incomingOrdinal));
  }
  if (incomingOrdinal < latestOrdinal) {
    return refused(checkpoint, failure("ORDINAL_REGRESSION", "auditOrdinal", incomingOrdinal));
  }
  if (incomingOrdinal > latestOrdinal + 1) {
    return refused(checkpoint, failure("ORDINAL_GAP", "auditOrdinal", incomingOrdinal));
  }
  const currentMission = checkpoint.state.currentMission;
  if (currentMission === null) {
    return refused(checkpoint, failure("MISSION_UNPREPARED", "currentMission", null));
  }
  if (orderedEvent.event.mission !== currentMission.identifier) {
    return refused(checkpoint, failure("MISSION_MISMATCH", "mission", orderedEvent.event.mission));
  }
  const applied = applyEvent(checkpoint.state, currentMission, orderedEvent.event);
  if (isFailure(applied)) {
    return refused(checkpoint, applied);
  }
  const state = finalizedState(applied, incomingOrdinal);
  const latestEventDigest = await orderedDashboardEventDigest(orderedEvent);
  return {
    checkpoint: { latestEventDigest, state },
    disposition: "APPLIED",
    ok: true,
  };
}

export async function foldVerifiedOrderedDashboardEvent(
  checkpoint: ReducerCheckpoint,
  orderedEvent: OrderedDashboardEvent,
  serverStateDigest: string,
): Promise<FoldResult> {
  const folded = await foldOrderedDashboardEvent(checkpoint, orderedEvent);
  if (!folded.ok) {
    return folded;
  }
  const computedDigest = await replayStateDigest(folded.checkpoint.state);
  const comparison = digestMatches(serverStateDigest, computedDigest);
  if (!comparison.ok) {
    return refused(checkpoint, failure("SERVER_DIGEST_FORM", "digest", serverStateDigest));
  }
  return comparison.matches
    ? folded
    : refused(checkpoint, failure("SERVER_DIGEST_MISMATCH", "digest", serverStateDigest));
}
