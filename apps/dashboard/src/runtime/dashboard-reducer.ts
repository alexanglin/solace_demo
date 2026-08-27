import type {
  DashboardEvent,
  DashboardEventFrame,
  DashboardReducedState,
  DashboardSnapshot,
} from "../contracts/generated";
import { digestMatches, orderedDashboardEventDigest, replayStateDigest } from "../domain/canonical";

export type DashboardFrameResult =
  | { readonly ok: true; readonly snapshot: DashboardSnapshot }
  | {
      readonly ok: false;
      readonly reason:
        | "DIGEST_MISMATCH"
        | "DIVERGENT_DUPLICATE"
        | "EVENT_BINDING_REFUSED"
        | "ORDINAL_GAP_OR_REGRESSION"
        | "TIMELINE_CAPACITY";
    };

type TimelineEvent = DashboardSnapshot["timeline"][number];
type SimulatedFleetMember = Extract<
  DashboardReducedState["fleet"][number],
  { participation: "SIMULATED" }
>;

function eventBelongsToMission(state: DashboardReducedState, event: DashboardEvent): boolean {
  return state.currentMission !== null && state.currentMission.identifier === event.mission;
}

function updateMission(
  state: DashboardReducedState,
  event: Extract<DashboardEvent, { kind: "missionLifecycle" }>,
): DashboardReducedState | undefined {
  if (!eventBelongsToMission(state, event) || state.currentMission === null) {
    return undefined;
  }
  return {
    ...state,
    currentMission: { ...state.currentMission, lifecycle: event.data.lifecycle },
  };
}

function updateTelemetry(
  state: DashboardReducedState,
  event: Extract<DashboardEvent, { kind: "droneTelemetry" }>,
): DashboardReducedState | undefined {
  if (!eventBelongsToMission(state, event)) {
    return undefined;
  }
  const selected = state.fleet.find(
    (member): member is SimulatedFleetMember =>
      member.identifier === event.data.droneId && member.participation === "SIMULATED",
  );
  if (selected === undefined) {
    return undefined;
  }
  const updated: SimulatedFleetMember = {
    ...selected,
    telemetry: {
      altitudeMetres: event.data.altitudeMetres,
      batteryPercent: event.data.batteryPercent,
      groundSpeedCentimetresPerSecond: event.data.groundSpeedCentimetresPerSecond,
      headingDegrees: event.data.headingDegrees,
      latitudeMicrodegrees: event.data.latitudeMicrodegrees,
      longitudeMicrodegrees: event.data.longitudeMicrodegrees,
    },
  };
  return {
    ...state,
    fleet: state.fleet.map((member) => (member === selected ? updated : member)),
  };
}

function updateConnectivity(
  state: DashboardReducedState,
  event: Extract<DashboardEvent, { kind: "connectivityChanged" }>,
): DashboardReducedState | undefined {
  if (!eventBelongsToMission(state, event)) {
    return undefined;
  }
  const selected = state.fleet.find(
    (member): member is SimulatedFleetMember =>
      member.identifier === event.data.droneId && member.participation === "SIMULATED",
  );
  if (selected === undefined) {
    return undefined;
  }
  const updated: SimulatedFleetMember = {
    ...selected,
    connectivity: event.data.connectivity,
  };
  return {
    ...state,
    fleet: state.fleet.map((member) => (member === selected ? updated : member)),
  };
}

function updateSector(
  state: DashboardReducedState,
  event: Extract<DashboardEvent, { kind: "sectorLifecycle" }>,
): DashboardReducedState | undefined {
  if (!eventBelongsToMission(state, event)) {
    return undefined;
  }
  const selected = state.sectors.find((sector) => sector.identifier === event.data.sectorId);
  if (selected === undefined) {
    return undefined;
  }
  const updated = {
    ...selected,
    assignedMemberId: event.data.assignedMemberId,
    state: event.data.state,
  };
  return {
    ...state,
    sectors: state.sectors.map((sector) => (sector === selected ? updated : sector)),
  };
}

function foldEvent(
  state: DashboardReducedState,
  event: DashboardEvent,
): DashboardReducedState | undefined {
  if (event.kind === "missionLifecycle") {
    return updateMission(state, event);
  }
  if (event.kind === "droneTelemetry") {
    return updateTelemetry(state, event);
  }
  if (event.kind === "connectivityChanged") {
    return updateConnectivity(state, event);
  }
  if (event.kind === "sectorLifecycle") {
    return updateSector(state, event);
  }
  return eventBelongsToMission(state, event) ? state : undefined;
}

function timelineEvent(auditOrdinal: number, event: DashboardEvent): TimelineEvent | undefined {
  if (event.kind === "droneTelemetry") {
    return undefined;
  }
  return { auditOrdinal, event };
}

async function isExactDuplicate(
  snapshot: DashboardSnapshot,
  frame: DashboardEventFrame,
): Promise<boolean> {
  if (snapshot.latestEventDigest === null) {
    return false;
  }
  const candidateDigest = await orderedDashboardEventDigest(frame.event);
  const comparison = digestMatches(snapshot.latestEventDigest, candidateDigest);
  return comparison.ok && comparison.matches;
}

export async function applyDashboardEventFrame(
  snapshot: DashboardSnapshot,
  frame: DashboardEventFrame,
): Promise<DashboardFrameResult> {
  const ordinal = frame.event.auditOrdinal;
  if (ordinal === snapshot.state.latestAuditOrdinal) {
    return (await isExactDuplicate(snapshot, frame))
      ? { ok: true, snapshot }
      : { ok: false, reason: "DIVERGENT_DUPLICATE" };
  }
  if (ordinal !== snapshot.state.latestAuditOrdinal + 1) {
    return { ok: false, reason: "ORDINAL_GAP_OR_REGRESSION" };
  }

  const folded = foldEvent(snapshot.state, frame.event.event);
  if (folded === undefined) {
    return { ok: false, reason: "EVENT_BINDING_REFUSED" };
  }
  const state = { ...folded, latestAuditOrdinal: ordinal };
  const calculatedDigest = await replayStateDigest(state);
  const comparison = digestMatches(frame.digest, calculatedDigest);
  if (!comparison.ok || !comparison.matches) {
    return { ok: false, reason: "DIGEST_MISMATCH" };
  }

  const nextTimelineEvent = timelineEvent(ordinal, frame.event.event);
  const timeline =
    nextTimelineEvent === undefined ? snapshot.timeline : [...snapshot.timeline, nextTimelineEvent];
  if (timeline.length > 256) {
    return { ok: false, reason: "TIMELINE_CAPACITY" };
  }
  return {
    ok: true,
    snapshot: {
      ...snapshot,
      cursor: frame.cursor,
      digest: frame.digest,
      latestEventDigest: await orderedDashboardEventDigest(frame.event),
      state,
      timeline,
    },
  };
}
