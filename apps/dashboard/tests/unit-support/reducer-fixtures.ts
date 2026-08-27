import type { DashboardReducedState, OrderedDashboardEvent } from "../../src/contracts/generated";
import { orderedDashboardEventDigest } from "../../src/domain/canonical";
import type { FoldResult, ReducerCheckpoint, ReducerFailure } from "../../src/domain/reducer";

export const MISSION_ID = "mission-synthetic-0001";
export const SIMULATED_MEMBER_ID = "drone-sim-01";
export const SECOND_SIMULATED_MEMBER_ID = "drone-sim-02";
export const DECLARED_ONLY_MEMBER_ID = "drone-comms-03";
export const SECTOR_ID = "sector-01";
export const SECOND_SECTOR_ID = "sector-02";

export function preparedState(
  overrides: Partial<DashboardReducedState> = {},
): DashboardReducedState {
  return {
    canonicalizationVersion: 1,
    stateVersion: 1,
    currentMission: {
      identifier: MISSION_ID,
      lifecycle: "PLANNED",
      predecessorIdentifier: null,
    },
    fleet: [
      {
        identifier: DECLARED_ONLY_MEMBER_ID,
        participation: "DECLARED_ONLY",
      },
      {
        identifier: SIMULATED_MEMBER_ID,
        participation: "SIMULATED",
        connectivity: "CONNECTED",
        telemetry: null,
      },
      {
        identifier: SECOND_SIMULATED_MEMBER_ID,
        participation: "SIMULATED",
        connectivity: "DEGRADED",
        telemetry: null,
      },
    ],
    latestAuditOrdinal: 0,
    sectors: [
      {
        identifier: SECTOR_ID,
        state: "UNASSIGNED",
        assignedMemberId: null,
      },
      {
        identifier: SECOND_SECTOR_ID,
        state: "UNASSIGNED",
        assignedMemberId: null,
      },
    ],
    ...overrides,
  };
}

export function missionEvent(
  auditOrdinal: number,
  lifecycle: "PLANNED" | "SEARCHING" | "EXHAUSTED" | "ABORTED" = "SEARCHING",
  mission = MISSION_ID,
): OrderedDashboardEvent {
  return {
    auditOrdinal,
    event: {
      kind: "missionLifecycle",
      eventClass: "MISSION",
      mission,
      time: `2026-08-25T12:00:${String(auditOrdinal).padStart(2, "0")}.000Z`,
      data: { lifecycle },
    },
  };
}

export function connectivityEvent(
  auditOrdinal: number,
  droneId = SIMULATED_MEMBER_ID,
  connectivity: "CONNECTED" | "DEGRADED" | "OFFLINE" = "OFFLINE",
  mission = MISSION_ID,
): OrderedDashboardEvent {
  return {
    auditOrdinal,
    event: {
      kind: "connectivityChanged",
      eventClass: "CONNECTIVITY",
      mission,
      time: `2026-08-25T12:01:${String(auditOrdinal).padStart(2, "0")}.000Z`,
      data: { connectivity, droneId },
    },
  };
}

export function telemetryEvent(
  auditOrdinal: number,
  droneId = SIMULATED_MEMBER_ID,
  mission = MISSION_ID,
): OrderedDashboardEvent {
  return {
    auditOrdinal,
    event: {
      kind: "droneTelemetry",
      eventClass: "TELEMETRY",
      mission,
      time: `2026-08-25T12:02:${String(auditOrdinal).padStart(2, "0")}.000Z`,
      data: {
        droneId,
        latitudeMicrodegrees: 44_475_000,
        longitudeMicrodegrees: -79_245_000,
        batteryPercent: 87,
        altitudeMetres: 92,
        headingDegrees: 145,
        groundSpeedCentimetresPerSecond: 960,
      },
    },
  };
}

export function sectorEvent(
  auditOrdinal: number,
  state: "UNASSIGNED" | "ASSIGNED" | "AT_RISK" | "SEARCHED" = "ASSIGNED",
  assignedMemberId: string | null = SIMULATED_MEMBER_ID,
  sectorId = SECTOR_ID,
  mission = MISSION_ID,
): OrderedDashboardEvent {
  return {
    auditOrdinal,
    event: {
      kind: "sectorLifecycle",
      eventClass: "MISSION",
      mission,
      time: `2026-08-25T12:03:${String(auditOrdinal).padStart(2, "0")}.000Z`,
      data: { assignedMemberId, sectorId, state },
    },
  };
}

export function initialCheckpoint(
  state: DashboardReducedState = preparedState(),
): ReducerCheckpoint {
  return { latestEventDigest: null, state };
}

export async function checkpointAt(
  orderedEvent: OrderedDashboardEvent,
  state: DashboardReducedState = preparedState({
    latestAuditOrdinal: orderedEvent.auditOrdinal,
  }),
): Promise<ReducerCheckpoint> {
  return {
    latestEventDigest: await orderedDashboardEventDigest(orderedEvent),
    state,
  };
}

export function refusalOf(result: FoldResult): ReducerFailure {
  if (result.ok) {
    throw new Error(`expected reducer refusal, observed ${result.disposition}`);
  }
  return result.failure;
}
