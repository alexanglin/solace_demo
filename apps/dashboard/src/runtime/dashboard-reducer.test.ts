import { expect, test } from "vitest";

import type {
  DashboardEvent,
  DashboardEventFrame,
  DashboardReducedState,
  DashboardSnapshot,
  OrderedDashboardEvent,
} from "../contracts/generated";
import { orderedDashboardEventDigest, replayStateDigest } from "../domain/canonical";
import { applyDashboardEventFrame } from "./dashboard-reducer";

function initialState(): DashboardReducedState & { latestAuditOrdinal: 0 } {
  return {
    canonicalizationVersion: 1,
    stateVersion: 1,
    currentMission: {
      identifier: "mission-synthetic-0001",
      lifecycle: "PLANNED",
      predecessorIdentifier: null,
    },
    fleet: [
      {
        identifier: "drone-sim-01",
        participation: "SIMULATED",
        connectivity: "CONNECTED",
        telemetry: null,
      },
      { identifier: "drone-vision-01", participation: "DECLARED_ONLY" },
    ],
    latestAuditOrdinal: 0,
    sectors: [{ identifier: "sector-01", state: "UNASSIGNED", assignedMemberId: null }],
  };
}

async function initialSnapshot(): Promise<DashboardSnapshot> {
  const state = initialState();
  return {
    snapshotVersion: "dashboard-snapshot/v1",
    runtimeId: "runtime-synthetic-0001",
    cursor: "cursor-initial",
    digest: await replayStateDigest(state),
    latestEventDigest: null,
    currentRun: {
      mode: "degradedLive",
      missionId: "mission-synthetic-0001",
      runId: "run-synthetic-0001",
    },
    state,
    timeline: [],
  };
}

async function frameFor(
  snapshot: DashboardSnapshot,
  event: OrderedDashboardEvent,
): Promise<DashboardEventFrame> {
  const nextState = structuredClone(snapshot.state);
  nextState.latestAuditOrdinal = event.auditOrdinal;
  if (event.event.kind === "missionLifecycle" && nextState.currentMission !== null) {
    nextState.currentMission.lifecycle = event.event.data.lifecycle;
  }
  if (event.event.kind === "droneTelemetry") {
    const telemetry = event.event.data;
    const member = nextState.fleet.find((candidate) => candidate.identifier === telemetry.droneId);
    if (member?.participation === "SIMULATED") {
      member.telemetry = {
        altitudeMetres: telemetry.altitudeMetres,
        batteryPercent: telemetry.batteryPercent,
        groundSpeedCentimetresPerSecond: telemetry.groundSpeedCentimetresPerSecond,
        headingDegrees: telemetry.headingDegrees,
        latitudeMicrodegrees: telemetry.latitudeMicrodegrees,
        longitudeMicrodegrees: telemetry.longitudeMicrodegrees,
      };
    }
  }
  if (event.event.kind === "connectivityChanged") {
    const connectivity = event.event.data;
    const member = nextState.fleet.find(
      (candidate) => candidate.identifier === connectivity.droneId,
    );
    if (member?.participation === "SIMULATED") {
      member.connectivity = connectivity.connectivity;
    }
  }
  if (event.event.kind === "sectorLifecycle") {
    const sectorEvent = event.event.data;
    const sector = nextState.sectors.find(
      (candidate) => candidate.identifier === sectorEvent.sectorId,
    );
    if (sector !== undefined) {
      sector.assignedMemberId = sectorEvent.assignedMemberId;
      sector.state = sectorEvent.state;
    }
  }
  return {
    frameVersion: "ordered-dashboard-event-frame/v1",
    cursor: `cursor-${String(event.auditOrdinal)}`,
    digest: await replayStateDigest(nextState),
    event,
  };
}

test("folds the next durable mission fact and ignores its exact redelivery", async () => {
  // Arrange
  const snapshot = await initialSnapshot();
  const ordered: OrderedDashboardEvent = {
    auditOrdinal: 1,
    event: {
      kind: "missionLifecycle",
      eventClass: "MISSION",
      mission: "mission-synthetic-0001",
      time: "2026-08-26T12:00:00.000Z",
      data: { lifecycle: "SEARCHING" },
    },
  };
  const frame = await frameFor(snapshot, ordered);

  // Act
  const accepted = await applyDashboardEventFrame(snapshot, frame);
  const duplicate = accepted.ok
    ? await applyDashboardEventFrame(accepted.snapshot, frame)
    : accepted;

  // Assert
  expect(accepted).toMatchObject({
    ok: true,
    snapshot: {
      cursor: "cursor-1",
      state: { currentMission: { lifecycle: "SEARCHING" }, latestAuditOrdinal: 1 },
      timeline: [{ auditOrdinal: 1 }],
    },
  });
  expect(duplicate).toEqual(accepted);
});

test("updates telemetry without adding it to the non-telemetry timeline", async () => {
  // Arrange
  const snapshot = await initialSnapshot();
  const ordered: OrderedDashboardEvent = {
    auditOrdinal: 1,
    event: {
      kind: "droneTelemetry",
      eventClass: "TELEMETRY",
      mission: "mission-synthetic-0001",
      time: "2026-08-26T12:00:00.000Z",
      data: {
        droneId: "drone-sim-01",
        latitudeMicrodegrees: 45_100_000,
        longitudeMicrodegrees: -75_100_000,
        batteryPercent: 88,
        altitudeMetres: 90,
        headingDegrees: 180,
        groundSpeedCentimetresPerSecond: 900,
      },
    },
  };
  const frame = await frameFor(snapshot, ordered);

  // Act
  const result = await applyDashboardEventFrame(snapshot, frame);

  // Assert
  expect(result.ok).toBe(true);
  if (result.ok) {
    expect(result.snapshot.state.fleet[0]).toMatchObject({ telemetry: { batteryPercent: 88 } });
    expect(result.snapshot.timeline).toEqual([]);
  }
});

test("refuses gaps, divergent duplicates, and server digest mismatch without changing the snapshot", async () => {
  // Arrange
  const snapshot = await initialSnapshot();
  const firstEvent: OrderedDashboardEvent = {
    auditOrdinal: 1,
    event: {
      kind: "missionLifecycle",
      eventClass: "MISSION",
      mission: "mission-synthetic-0001",
      time: "2026-08-26T12:00:00.000Z",
      data: { lifecycle: "SEARCHING" },
    },
  };
  const firstFrame = await frameFor(snapshot, firstEvent);
  const first = await applyDashboardEventFrame(snapshot, firstFrame);
  if (!first.ok) {
    throw new Error("test setup frame was refused");
  }
  const divergent = {
    ...firstFrame,
    event: {
      ...firstFrame.event,
      event: { ...firstFrame.event.event, time: "2026-08-26T12:00:01.000Z" },
    },
  };
  const gapEvent = { ...firstEvent, auditOrdinal: 3 };
  const gapFrame = { ...firstFrame, event: gapEvent };
  const digestMismatch = {
    ...firstFrame,
    cursor: "cursor-2",
    digest: "f".repeat(64),
    event: { ...firstEvent, auditOrdinal: 2 },
  };

  // Act
  const divergentResult = await applyDashboardEventFrame(first.snapshot, divergent);
  const gapResult = await applyDashboardEventFrame(first.snapshot, gapFrame);
  const digestResult = await applyDashboardEventFrame(first.snapshot, digestMismatch);

  // Assert
  expect(divergentResult).toEqual({ ok: false, reason: "DIVERGENT_DUPLICATE" });
  expect(gapResult).toEqual({ ok: false, reason: "ORDINAL_GAP_OR_REGRESSION" });
  expect(digestResult).toEqual({ ok: false, reason: "DIGEST_MISMATCH" });
  expect(first.snapshot.latestEventDigest).toBe(await orderedDashboardEventDigest(firstEvent));
});

test("folds connectivity, sector assignment, and an operational audit fact in order", async () => {
  // Arrange
  const snapshot = await initialSnapshot();
  const connectivity: OrderedDashboardEvent = {
    auditOrdinal: 1,
    event: {
      kind: "connectivityChanged",
      eventClass: "CONNECTIVITY",
      mission: "mission-synthetic-0001",
      time: "2026-08-26T12:01:00.000Z",
      data: { droneId: "drone-sim-01", connectivity: "OFFLINE" },
    },
  };
  const connectivityFrame = await frameFor(snapshot, connectivity);
  const connected = await applyDashboardEventFrame(snapshot, connectivityFrame);
  if (!connected.ok) {
    throw new Error("connectivity test frame was refused");
  }
  const sector: OrderedDashboardEvent = {
    auditOrdinal: 2,
    event: {
      kind: "sectorLifecycle",
      eventClass: "MISSION",
      mission: "mission-synthetic-0001",
      time: "2026-08-26T12:02:00.000Z",
      data: {
        sectorId: "sector-01",
        state: "AT_RISK",
        assignedMemberId: "drone-sim-01",
      },
    },
  };
  const sectorFrame = await frameFor(connected.snapshot, sector);
  const assigned = await applyDashboardEventFrame(connected.snapshot, sectorFrame);
  if (!assigned.ok) {
    throw new Error("sector test frame was refused");
  }
  const command: OrderedDashboardEvent = {
    auditOrdinal: 3,
    event: {
      kind: "operatorCommand",
      eventClass: "COMMAND",
      mission: "mission-synthetic-0001",
      time: "2026-08-26T12:03:00.000Z",
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
  };
  const commandFrame = await frameFor(assigned.snapshot, command);

  // Act
  const result = await applyDashboardEventFrame(assigned.snapshot, commandFrame);

  // Assert
  expect(result.ok).toBe(true);
  if (result.ok) {
    expect(result.snapshot.state.fleet[0]).toMatchObject({ connectivity: "OFFLINE" });
    expect(result.snapshot.state.latestAuditOrdinal).toBe(3);
    expect(result.snapshot.state.sectors).toEqual([
      { identifier: "sector-01", assignedMemberId: "drone-sim-01", state: "AT_RISK" },
    ]);
    expect(result.snapshot.timeline).toMatchObject([
      { auditOrdinal: 1 },
      { auditOrdinal: 2 },
      { auditOrdinal: 3, event: { kind: "operatorCommand" } },
    ]);
  }
});

test("refuses mission, member, sector, and generic facts that do not bind", async () => {
  // Arrange
  const snapshot = await initialSnapshot();
  const baseTime = "2026-08-26T12:00:00.000Z";
  const refusedEvents: DashboardEvent[] = [
    {
      kind: "missionLifecycle",
      eventClass: "MISSION",
      mission: "mission-foreign-0001",
      time: baseTime,
      data: { lifecycle: "SEARCHING" },
    },
    {
      kind: "droneTelemetry",
      eventClass: "TELEMETRY",
      mission: "mission-foreign-0001",
      time: baseTime,
      data: {
        droneId: "drone-sim-01",
        latitudeMicrodegrees: 45_100_000,
        longitudeMicrodegrees: -75_100_000,
        batteryPercent: 80,
        altitudeMetres: 80,
        headingDegrees: 80,
        groundSpeedCentimetresPerSecond: 800,
      },
    },
    {
      kind: "droneTelemetry",
      eventClass: "TELEMETRY",
      mission: "mission-synthetic-0001",
      time: baseTime,
      data: {
        droneId: "drone-unknown-01",
        latitudeMicrodegrees: 45_100_000,
        longitudeMicrodegrees: -75_100_000,
        batteryPercent: 80,
        altitudeMetres: 80,
        headingDegrees: 80,
        groundSpeedCentimetresPerSecond: 800,
      },
    },
    {
      kind: "connectivityChanged",
      eventClass: "CONNECTIVITY",
      mission: "mission-foreign-0001",
      time: baseTime,
      data: { droneId: "drone-sim-01", connectivity: "OFFLINE" },
    },
    {
      kind: "connectivityChanged",
      eventClass: "CONNECTIVITY",
      mission: "mission-synthetic-0001",
      time: baseTime,
      data: { droneId: "drone-vision-01", connectivity: "OFFLINE" },
    },
    {
      kind: "sectorLifecycle",
      eventClass: "MISSION",
      mission: "mission-foreign-0001",
      time: baseTime,
      data: { sectorId: "sector-01", state: "AT_RISK", assignedMemberId: null },
    },
    {
      kind: "sectorLifecycle",
      eventClass: "MISSION",
      mission: "mission-synthetic-0001",
      time: baseTime,
      data: { sectorId: "sector-unknown-01", state: "AT_RISK", assignedMemberId: null },
    },
    {
      kind: "operatorCommand",
      eventClass: "COMMAND",
      mission: "mission-foreign-0001",
      time: baseTime,
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
  ];

  // Act
  const results = await Promise.all(
    refusedEvents.map((event) =>
      applyDashboardEventFrame(snapshot, {
        frameVersion: "ordered-dashboard-event-frame/v1",
        cursor: "cursor-refused",
        digest: snapshot.digest,
        event: { auditOrdinal: 1, event },
      }),
    ),
  );

  // Assert
  expect(results).toEqual(
    refusedEvents.map(() => ({ ok: false, reason: "EVENT_BINDING_REFUSED" })),
  );
});

test("refuses an ordinal-zero duplicate without a witness, malformed digest, and timeline overflow", async () => {
  // Arrange
  const snapshot = await initialSnapshot();
  const missionEvent: OrderedDashboardEvent = {
    auditOrdinal: 1,
    event: {
      kind: "missionLifecycle",
      eventClass: "MISSION",
      mission: "mission-synthetic-0001",
      time: "2026-08-26T12:00:00.000Z",
      data: { lifecycle: "SEARCHING" },
    },
  };
  const validFrame = await frameFor(snapshot, missionEvent);
  const duplicateWithoutWitness = {
    ...validFrame,
    event: { ...missionEvent, auditOrdinal: 0 },
  };
  const malformedDigest = { ...validFrame, digest: "not-a-digest" };
  const fullTimeline: DashboardSnapshot["timeline"] = Array.from({ length: 256 }, (_, index) => ({
    auditOrdinal: index + 1,
    event: missionEvent.event as Extract<DashboardEvent, { kind: "missionLifecycle" }>,
  }));
  const atCapacity = { ...snapshot, timeline: fullTimeline };

  // Act
  const duplicateResult = await applyDashboardEventFrame(snapshot, duplicateWithoutWitness);
  const malformedDigestResult = await applyDashboardEventFrame(snapshot, malformedDigest);
  const capacityResult = await applyDashboardEventFrame(atCapacity, validFrame);

  // Assert
  expect(duplicateResult).toEqual({ ok: false, reason: "DIVERGENT_DUPLICATE" });
  expect(malformedDigestResult).toEqual({ ok: false, reason: "DIGEST_MISMATCH" });
  expect(capacityResult).toEqual({ ok: false, reason: "TIMELINE_CAPACITY" });
});
