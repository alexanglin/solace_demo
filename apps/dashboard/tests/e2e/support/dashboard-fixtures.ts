import { createHash } from "node:crypto";

export type DashboardMode = "degradedLive" | "replay";

export type DashboardViewState =
  | "loading"
  | "empty"
  | "ready"
  | "starting"
  | "running"
  | "resetting"
  | "retrying"
  | "offline"
  | "recovered"
  | "staleRuntime"
  | "contractFailure"
  | "exhausted"
  | "aborted"
  | "replay";

export type MissionLifecycle = "PLANNED" | "SEARCHING" | "EXHAUSTED" | "ABORTED";

export type Connectivity = "CONNECTED" | "DEGRADED" | "OFFLINE";

export type Participation = "SIMULATED" | "DECLARED_ONLY";

export type SectorState = "UNASSIGNED" | "ASSIGNED" | "AT_RISK" | "SEARCHED";

export interface TelemetryFixture {
  readonly altitudeMetres: number;
  readonly batteryPercent: number;
  readonly groundSpeedCentimetresPerSecond: number;
  readonly headingDegrees: number;
  readonly latitudeMicrodegrees: number;
  readonly longitudeMicrodegrees: number;
}

export interface SimulatedFleetMemberFixture {
  readonly connectivity: Connectivity;
  readonly identifier: string;
  readonly participation: "SIMULATED";
  readonly telemetry: TelemetryFixture | null;
}

export interface DeclaredOnlyFleetMemberFixture {
  readonly identifier: string;
  readonly participation: "DECLARED_ONLY";
}

export type FleetMemberFixture = SimulatedFleetMemberFixture | DeclaredOnlyFleetMemberFixture;

export interface SectorFixture {
  readonly assignedMemberId: string | null;
  readonly identifier: string;
  readonly state: SectorState;
}

export interface SimulatedScenarioMemberFixture {
  readonly identifier: string;
  readonly participation: "SIMULATED";
}

export interface DeclaredOnlyScenarioMemberFixture {
  readonly executionLabel: "DECLARED ONLY — NOT EXECUTED";
  readonly identifier: string;
  readonly participation: "DECLARED_ONLY";
  readonly role: "communications" | "navigation" | "vision";
}

export type ScenarioMemberFixture =
  SimulatedScenarioMemberFixture | DeclaredOnlyScenarioMemberFixture;

export interface ScenarioVertexFixture {
  readonly latitudeMicrodegrees: number;
  readonly longitudeMicrodegrees: number;
}

export interface SectorGeometryFixture {
  readonly identifier: string;
  readonly vertices: readonly ScenarioVertexFixture[];
}

export interface ScenarioFixture {
  readonly declaredCount: 23;
  readonly declaredOnlyCount: 3;
  readonly identifier: "wilderness-missing-person";
  readonly lastKnownLocation: {
    readonly label: string;
    readonly latitudeMicrodegrees: number;
    readonly longitudeMicrodegrees: number;
  };
  readonly members: readonly ScenarioMemberFixture[];
  readonly revision: 1;
  readonly searchAreaSquareMetres: number;
  readonly searchPolygon: {
    readonly vertices: readonly ScenarioVertexFixture[];
  };
  readonly sectors: readonly SectorGeometryFixture[];
  readonly simulatedCount: 20;
  readonly summary: string;
  readonly title: "Wilderness Missing Person";
}

export interface MissionFixture {
  readonly identifier: string;
  readonly lifecycle: MissionLifecycle;
  readonly predecessorIdentifier: string | null;
}

export interface DashboardReducedState {
  readonly canonicalizationVersion: 1;
  readonly currentMission: MissionFixture | null;
  readonly fleet: readonly FleetMemberFixture[];
  readonly latestAuditOrdinal: number;
  readonly sectors: readonly SectorFixture[];
  readonly stateVersion: 1;
}

interface DashboardEventBaseFixture {
  readonly mission: string;
  readonly time: string;
}

export interface ConnectivityChangedEventFixture extends DashboardEventBaseFixture {
  readonly data: {
    readonly connectivity: Connectivity;
    readonly droneId: string;
  };
  readonly eventClass: "CONNECTIVITY";
  readonly kind: "connectivityChanged";
}

export interface DroneTelemetryEventFixture extends DashboardEventBaseFixture {
  readonly data: TelemetryFixture & {
    readonly droneId: string;
  };
  readonly eventClass: "TELEMETRY";
  readonly kind: "droneTelemetry";
}

export interface MissionLifecycleEventFixture extends DashboardEventBaseFixture {
  readonly data: {
    readonly lifecycle: MissionLifecycle;
  };
  readonly eventClass: "MISSION";
  readonly kind: "missionLifecycle";
}

export interface SectorLifecycleEventFixture extends DashboardEventBaseFixture {
  readonly data: {
    readonly assignedMemberId: string | null;
    readonly sectorId: string;
    readonly state: SectorState;
  };
  readonly eventClass: "MISSION";
  readonly kind: "sectorLifecycle";
}

export type DashboardEventFixture =
  | ConnectivityChangedEventFixture
  | DroneTelemetryEventFixture
  | MissionLifecycleEventFixture
  | SectorLifecycleEventFixture;

export interface OrderedDashboardEventFixture {
  readonly auditOrdinal: number;
  readonly event: DashboardEventFixture;
}

export type DashboardSourceInputChannel =
  | "bootstrap"
  | "http-response"
  | "mutation-result"
  | "replay-bundle"
  | "source-signal"
  | "sse-frame";

export interface DashboardSourceInput {
  readonly channel: DashboardSourceInputChannel;
  readonly name: string;
  readonly raw: string;
}

export interface DashboardSourceScript {
  readonly fixtureVersion: "dashboard-source-script/v1";
  readonly inputs: readonly DashboardSourceInput[];
}

export interface ReplayCheckpoint {
  readonly auditOrdinal: number;
  readonly digest: string;
  readonly drone07Connectivity: Connectivity;
  readonly lifecycle: MissionLifecycle;
  readonly sector07State: SectorState;
}

export interface HeartbeatInputBatch {
  readonly inputs: readonly DashboardSourceInput[];
  readonly stage: "degraded" | "offline" | "recovered" | "exhausted";
}

export type ResilienceFault =
  | "digestDivergence"
  | "exactDuplicate"
  | "malformedFrame"
  | "ordinalGap"
  | "ordinalRegression"
  | "streamOverloaded";

export type ValidationBoundary = "bootstrap" | "readiness" | "replayBundle" | "scenarioCatalog";

export interface ReplayFixtureOverrides {
  readonly checksum?: string;
  readonly expectedFinalDigest?: string;
}

interface ReplayIntegrityFixture {
  readonly algorithm: "sha256";
  readonly checksum: string;
  readonly expectedFinalDigest: string;
  readonly integrityVersion: "dashboard-replay-integrity/v1";
}

export const syntheticBearerSentinel = "synthetic-browser-bearer-do-not-persist";

export const declaredOnlyAgentIds = [
  "drone-vision-01",
  "drone-navigation-02",
  "drone-comms-03",
] as const;

function identifierFor(index: number): string {
  return `drone-sim-${String(index).padStart(2, "0")}`;
}

function sectorIdentifierFor(index: number): string {
  return `sector-${String(index).padStart(2, "0")}`;
}

function byteOrder(left: string, right: string): number {
  return Buffer.from(left).compare(Buffer.from(right));
}

function sectorGeometry(index: number): SectorGeometryFixture {
  const column = (index - 1) % 5;
  const row = Math.floor((index - 1) / 5);
  const west = -79_250_000 + column * 10_000;
  const south = 44_470_000 + row * 10_000;
  const east = west + 9_000;
  const north = south + 9_000;
  return {
    identifier: sectorIdentifierFor(index),
    vertices: [
      { latitudeMicrodegrees: south, longitudeMicrodegrees: west },
      { latitudeMicrodegrees: south, longitudeMicrodegrees: east },
      { latitudeMicrodegrees: north, longitudeMicrodegrees: east },
      { latitudeMicrodegrees: north, longitudeMicrodegrees: west },
      { latitudeMicrodegrees: south, longitudeMicrodegrees: west },
    ],
  };
}

function declaredOnlyRole(
  identifier: (typeof declaredOnlyAgentIds)[number],
): DeclaredOnlyScenarioMemberFixture["role"] {
  if (identifier === "drone-comms-03") {
    return "communications";
  }
  if (identifier === "drone-navigation-02") {
    return "navigation";
  }
  return "vision";
}

function scenarioMembers(): readonly ScenarioMemberFixture[] {
  const declaredOnlyMembers = declaredOnlyAgentIds
    .map((identifier) => ({
      executionLabel: "DECLARED ONLY — NOT EXECUTED" as const,
      identifier,
      participation: "DECLARED_ONLY" as const,
      role: declaredOnlyRole(identifier),
    }))
    .sort((left, right) => byteOrder(left.identifier, right.identifier));
  const simulatedMembers = Array.from({ length: 20 }, (_, offset) => ({
    identifier: identifierFor(offset + 1),
    participation: "SIMULATED" as const,
  })).sort((left, right) => byteOrder(left.identifier, right.identifier));
  return [...declaredOnlyMembers, ...simulatedMembers];
}

const scenario: ScenarioFixture = {
  declaredCount: 23,
  declaredOnlyCount: 3,
  identifier: "wilderness-missing-person",
  lastKnownLocation: {
    label: "North ridge trail",
    latitudeMicrodegrees: 44_493_100,
    longitudeMicrodegrees: -79_228_400,
  },
  members: scenarioMembers(),
  revision: 1,
  searchAreaSquareMetres: 18_400_000,
  searchPolygon: {
    vertices: [
      { latitudeMicrodegrees: 44_470_000, longitudeMicrodegrees: -79_250_000 },
      { latitudeMicrodegrees: 44_470_000, longitudeMicrodegrees: -79_201_000 },
      { latitudeMicrodegrees: 44_509_000, longitudeMicrodegrees: -79_201_000 },
      { latitudeMicrodegrees: 44_509_000, longitudeMicrodegrees: -79_250_000 },
      { latitudeMicrodegrees: 44_470_000, longitudeMicrodegrees: -79_250_000 },
    ],
  },
  sectors: Array.from({ length: 20 }, (_, offset) => sectorGeometry(offset + 1)),
  simulatedCount: 20,
  summary: "Twenty simulated aircraft sweep twenty bounded synthetic wilderness sectors.",
  title: "Wilderness Missing Person",
};

function connectivityFor(index: number): Connectivity {
  if (index === 7) {
    return "OFFLINE";
  }
  if (index === 4 || index === 12) {
    return "DEGRADED";
  }
  return "CONNECTED";
}

function sectorStateFor(index: number): SectorState {
  if (index <= 4) {
    return "SEARCHED";
  }
  if (index === 7) {
    return "AT_RISK";
  }
  return "ASSIGNED";
}

function simulatedMember(index: number): SimulatedFleetMemberFixture {
  return {
    connectivity: connectivityFor(index),
    identifier: identifierFor(index),
    participation: "SIMULATED",
    telemetry: {
      altitudeMetres: 82 + index,
      batteryPercent: 96 - index,
      groundSpeedCentimetresPerSecond: 850 + index * 10,
      headingDegrees: (index * 17) % 360,
      latitudeMicrodegrees: 44_490_000 + index * 800,
      longitudeMicrodegrees: -79_240_000 + index * 1_100,
    },
  };
}

function declaredOnlyMember(identifier: string): DeclaredOnlyFleetMemberFixture {
  return {
    identifier,
    participation: "DECLARED_ONLY",
  };
}

function fleet(): readonly FleetMemberFixture[] {
  return [
    ...declaredOnlyAgentIds.map((identifier) => declaredOnlyMember(identifier)),
    ...Array.from({ length: 20 }, (_, offset) => simulatedMember(offset + 1)),
  ].sort((left, right) => byteOrder(left.identifier, right.identifier));
}

function sectors(): readonly SectorFixture[] {
  return Array.from({ length: 20 }, (_, offset) => {
    const index = offset + 1;
    return {
      assignedMemberId: identifierFor(index),
      identifier: sectorIdentifierFor(index),
      state: sectorStateFor(index),
    };
  });
}

function liveAuditEvents(): readonly OrderedDashboardEventFixture[] {
  const mission = "mission-synthetic-0001";
  const auditEvents: OrderedDashboardEventFixture[] = [
    event(1, "missionLifecycle", "MISSION", { lifecycle: "PLANNED" }, mission),
    event(2, "missionLifecycle", "MISSION", { lifecycle: "SEARCHING" }, mission),
  ];
  for (let index = 1; index <= 20; index += 1) {
    auditEvents.push(
      event(
        index + 2,
        "sectorLifecycle",
        "MISSION",
        {
          assignedMemberId: identifierFor(index),
          sectorId: sectorIdentifierFor(index),
          state: "ASSIGNED",
        },
        mission,
      ),
    );
  }
  for (let index = 1; index <= 20; index += 1) {
    const telemetry = simulatedMember(index).telemetry;
    if (telemetry === null) {
      throw new Error("live fixture telemetry is missing");
    }
    auditEvents.push(
      event(
        index + 22,
        "droneTelemetry",
        "TELEMETRY",
        { ...telemetry, droneId: identifierFor(index) },
        mission,
      ),
    );
  }
  for (let index = 1; index <= 4; index += 1) {
    auditEvents.push(
      event(
        index + 42,
        "sectorLifecycle",
        "MISSION",
        {
          assignedMemberId: identifierFor(index),
          sectorId: sectorIdentifierFor(index),
          state: "SEARCHED",
        },
        mission,
      ),
    );
  }
  auditEvents.push(
    event(
      47,
      "connectivityChanged",
      "CONNECTIVITY",
      { connectivity: "DEGRADED", droneId: "drone-sim-04" },
      mission,
    ),
    event(
      48,
      "connectivityChanged",
      "CONNECTIVITY",
      { connectivity: "DEGRADED", droneId: "drone-sim-12" },
      mission,
    ),
    event(
      49,
      "connectivityChanged",
      "CONNECTIVITY",
      { connectivity: "DEGRADED", droneId: "drone-sim-07" },
      mission,
    ),
    event(
      50,
      "connectivityChanged",
      "CONNECTIVITY",
      { connectivity: "OFFLINE", droneId: "drone-sim-07" },
      mission,
    ),
    event(
      51,
      "sectorLifecycle",
      "MISSION",
      {
        assignedMemberId: "drone-sim-07",
        sectorId: "sector-07",
        state: "AT_RISK",
      },
      mission,
    ),
  );
  return auditEvents;
}

function liveTimeline(): readonly OrderedDashboardEventFixture[] {
  return liveAuditEvents().filter((orderedEvent) => orderedEvent.event.eventClass !== "TELEMETRY");
}

function liveState(): DashboardReducedState {
  return liveAuditEvents().reduce(applyOrderedEventForOracle, plannedState());
}

function plannedState(): DashboardReducedState {
  return {
    canonicalizationVersion: 1,
    currentMission: {
      identifier: "mission-synthetic-0001",
      lifecycle: "PLANNED",
      predecessorIdentifier: null,
    },
    fleet: fleet().map((member) =>
      member.participation === "SIMULATED"
        ? { ...member, connectivity: "CONNECTED", telemetry: null }
        : member,
    ),
    latestAuditOrdinal: 0,
    sectors: sectors().map((sector) => ({
      ...sector,
      assignedMemberId: null,
      state: "UNASSIGNED",
    })),
    stateVersion: 1,
  };
}

function heartbeatBaseState(): DashboardReducedState {
  return heartbeatRecoveryEvents().reduce(applyOrderedEventForOracle, liveState());
}

function heartbeatRecoveryEvents(): readonly OrderedDashboardEventFixture[] {
  const mission = "mission-synthetic-0001";
  return [
    event(
      52,
      "connectivityChanged",
      "CONNECTIVITY",
      { connectivity: "CONNECTED", droneId: "drone-sim-07" },
      mission,
    ),
    event(
      53,
      "sectorLifecycle",
      "MISSION",
      {
        assignedMemberId: "drone-sim-07",
        sectorId: "sector-07",
        state: "ASSIGNED",
      },
      mission,
    ),
  ];
}

function heartbeatTimeline(): readonly OrderedDashboardEventFixture[] {
  return [...liveTimeline(), ...heartbeatRecoveryEvents()];
}

function sourceInput(
  channel: DashboardSourceInputChannel,
  name: string,
  document: object,
): DashboardSourceInput {
  return { channel, name, raw: JSON.stringify(document) };
}

function opaqueLiveCursor(material: object): string {
  const runBoundMaterial = `run-synthetic-0001\n${canonicalJson(material)}`;
  const opaqueToken = createHash("sha256").update(runBoundMaterial, "utf8").digest("hex");
  return `cursor-${opaqueToken}`;
}

export function malformedSourceInput(
  channel: DashboardSourceInputChannel,
  name: string,
  raw: string,
): DashboardSourceInput {
  return { channel, name, raw };
}

function bootstrap(): DashboardSourceInput {
  return sourceInput("bootstrap", "bootstrap", {
    bearer: syntheticBearerSentinel,
    bootstrapVersion: "dashboard-bootstrap/v1",
    runtimeId: "runtime-synthetic-0001",
  });
}

function readiness(mode: DashboardMode = "degradedLive"): DashboardSourceInput {
  return sourceInput("http-response", "readiness", {
    mode,
    readinessVersion: "dashboard-readiness/v1",
    ready: true,
    reasons: [],
  });
}

function scenarioCatalog(scenarios: readonly ScenarioFixture[] = [scenario]): DashboardSourceInput {
  return sourceInput("http-response", "scenario-catalog", {
    catalogVersion: "scenario-catalog/v1",
    scenarios,
  });
}

function snapshot(
  state: DashboardReducedState,
  timeline: readonly OrderedDashboardEventFixture[],
): DashboardSourceInput {
  const digest = replayStateDigest(state);
  return sourceInput("sse-frame", "snapshot", {
    currentRun: {
      missionId: "mission-synthetic-0001",
      mode: "degradedLive",
      runId: "run-synthetic-0001",
    },
    cursor: opaqueLiveCursor({ digest, frame: "snapshot" }),
    digest,
    runtimeId: "runtime-synthetic-0001",
    snapshotVersion: "dashboard-snapshot/v1",
    state,
    timeline,
  });
}

function sourceSignal(
  signal: "connecting" | "disconnected" | "offline" | "recovered" | "runtimeChanged",
): DashboardSourceInput {
  return sourceInput("source-signal", signal, {
    signal,
    signalVersion: "dashboard-source-signal/v1",
  });
}

function operationSignal(operation: "reset" | "start", phase: "pending"): DashboardSourceInput {
  return sourceInput("mutation-result", `${operation}-${phase}`, {
    operation,
    phase,
    resultVersion: "dashboard-mutation-result/v1",
  });
}

function orderedEventInput(
  orderedEvent: OrderedDashboardEventFixture,
  stateAfter: DashboardReducedState,
): DashboardSourceInput {
  return orderedEventFrameInput(orderedEvent, replayStateDigest(stateAfter));
}

function orderedEventFrameInput(
  orderedEvent: OrderedDashboardEventFixture,
  digest: string,
): DashboardSourceInput {
  return sourceInput("sse-frame", "dashboard-event", {
    cursor: opaqueLiveCursor({ digest, event: orderedEvent, frame: "dashboardEvent" }),
    digest,
    event: orderedEvent,
    frameVersion: "ordered-dashboard-event-frame/v1",
  });
}

function canonicalJson(value: unknown): string {
  if (value === null) {
    return "null";
  }
  if (typeof value === "boolean") {
    return value ? "true" : "false";
  }
  if (typeof value === "number") {
    if (!Number.isSafeInteger(value)) {
      throw new TypeError("replay oracle accepts only safe integers");
    }
    return String(value);
  }
  if (typeof value === "string") {
    return JSON.stringify(value.normalize("NFC"));
  }
  if (Array.isArray(value)) {
    return `[${value.map((item) => canonicalJson(item)).join(",")}]`;
  }
  if (typeof value === "object") {
    const entries = Object.entries(value).sort(([left], [right]) => byteOrder(left, right));
    return `{${entries
      .map(([key, item]) => `${JSON.stringify(key)}:${canonicalJson(item)}`)
      .join(",")}}`;
  }
  throw new TypeError(`unsupported replay oracle value: ${typeof value}`);
}

export function replayStateDigest(state: DashboardReducedState): string {
  const stateWithoutTopLevelDigest = Object.fromEntries(
    Object.entries(state).filter(([key]) => key !== "digest"),
  );
  const material = `aerial-rescue/canonical/v1\nreplay-state\n${canonicalJson(stateWithoutTopLevelDigest)}`;
  return createHash("sha256").update(material, "utf8").digest("hex");
}

function event(
  auditOrdinal: number,
  kind: "connectivityChanged",
  eventClass: "CONNECTIVITY",
  data: ConnectivityChangedEventFixture["data"],
  mission?: string,
): OrderedDashboardEventFixture;
function event(
  auditOrdinal: number,
  kind: "droneTelemetry",
  eventClass: "TELEMETRY",
  data: DroneTelemetryEventFixture["data"],
  mission?: string,
): OrderedDashboardEventFixture;
function event(
  auditOrdinal: number,
  kind: "missionLifecycle",
  eventClass: "MISSION",
  data: MissionLifecycleEventFixture["data"],
  mission?: string,
): OrderedDashboardEventFixture;
function event(
  auditOrdinal: number,
  kind: "sectorLifecycle",
  eventClass: "MISSION",
  data: SectorLifecycleEventFixture["data"],
  mission?: string,
): OrderedDashboardEventFixture;
function event(
  auditOrdinal: number,
  kind: DashboardEventFixture["kind"],
  eventClass: DashboardEventFixture["eventClass"],
  data: DashboardEventFixture["data"],
  mission = "recorded-mission-synthetic-0001",
): OrderedDashboardEventFixture {
  const time = new Date(Date.UTC(2026, 7, 24, 12, 0, auditOrdinal)).toISOString();
  if (kind === "connectivityChanged" && eventClass === "CONNECTIVITY" && "connectivity" in data) {
    return { auditOrdinal, event: { data, eventClass, kind, mission, time } };
  }
  if (kind === "droneTelemetry" && eventClass === "TELEMETRY" && "latitudeMicrodegrees" in data) {
    return { auditOrdinal, event: { data, eventClass, kind, mission, time } };
  }
  if (kind === "missionLifecycle" && eventClass === "MISSION" && "lifecycle" in data) {
    return { auditOrdinal, event: { data, eventClass, kind, mission, time } };
  }
  if (kind === "sectorLifecycle" && eventClass === "MISSION" && "sectorId" in data) {
    return { auditOrdinal, event: { data, eventClass, kind, mission, time } };
  }
  throw new TypeError("dashboard event fixture variant is inconsistent");
}

function replaceSimulatedMember(
  state: DashboardReducedState,
  droneId: string,
  update: (member: SimulatedFleetMemberFixture) => SimulatedFleetMemberFixture,
): readonly FleetMemberFixture[] {
  return state.fleet.map((member) =>
    member.identifier === droneId && member.participation === "SIMULATED" ? update(member) : member,
  );
}

function replaceSector(
  state: DashboardReducedState,
  sectorId: string,
  update: (sector: SectorFixture) => SectorFixture,
): readonly SectorFixture[] {
  return state.sectors.map((sector) => (sector.identifier === sectorId ? update(sector) : sector));
}

export function applyOrderedEventForOracle(
  state: DashboardReducedState,
  orderedEvent: OrderedDashboardEventFixture,
): DashboardReducedState {
  if (orderedEvent.auditOrdinal !== state.latestAuditOrdinal + 1) {
    throw new RangeError("replay oracle accepts only the next audit ordinal");
  }
  const { event: dashboardEvent } = orderedEvent;
  const nextBase = { ...state, latestAuditOrdinal: orderedEvent.auditOrdinal };
  if (dashboardEvent.kind === "missionLifecycle") {
    return {
      ...nextBase,
      currentMission:
        state.currentMission === null
          ? null
          : { ...state.currentMission, lifecycle: dashboardEvent.data.lifecycle },
    };
  }
  if (dashboardEvent.kind === "connectivityChanged") {
    return {
      ...nextBase,
      fleet: replaceSimulatedMember(state, dashboardEvent.data.droneId, (member) => ({
        ...member,
        connectivity: dashboardEvent.data.connectivity,
      })),
    };
  }
  if (dashboardEvent.kind === "sectorLifecycle") {
    return {
      ...nextBase,
      sectors: replaceSector(state, dashboardEvent.data.sectorId, (sector) => ({
        ...sector,
        assignedMemberId: dashboardEvent.data.assignedMemberId,
        state: dashboardEvent.data.state,
      })),
    };
  }
  return {
    ...nextBase,
    fleet: replaceSimulatedMember(state, dashboardEvent.data.droneId, (member) => ({
      ...member,
      telemetry: {
        altitudeMetres: dashboardEvent.data.altitudeMetres,
        batteryPercent: dashboardEvent.data.batteryPercent,
        groundSpeedCentimetresPerSecond: dashboardEvent.data.groundSpeedCentimetresPerSecond,
        headingDegrees: dashboardEvent.data.headingDegrees,
        latitudeMicrodegrees: dashboardEvent.data.latitudeMicrodegrees,
        longitudeMicrodegrees: dashboardEvent.data.longitudeMicrodegrees,
      },
    })),
  };
}

function replayAuditEvents(): readonly OrderedDashboardEventFixture[] {
  const auditEvents: OrderedDashboardEventFixture[] = [
    event(1, "missionLifecycle", "MISSION", { lifecycle: "SEARCHING" }),
  ];
  for (let index = 1; index <= 20; index += 1) {
    auditEvents.push(
      event(index + 1, "sectorLifecycle", "MISSION", {
        assignedMemberId: identifierFor(index),
        sectorId: sectorIdentifierFor(index),
        state: "ASSIGNED",
      }),
    );
  }
  auditEvents.push(
    event(22, "connectivityChanged", "CONNECTIVITY", {
      connectivity: "DEGRADED",
      droneId: "drone-sim-07",
    }),
    event(23, "connectivityChanged", "CONNECTIVITY", {
      connectivity: "OFFLINE",
      droneId: "drone-sim-07",
    }),
    event(24, "sectorLifecycle", "MISSION", {
      assignedMemberId: "drone-sim-07",
      sectorId: "sector-07",
      state: "AT_RISK",
    }),
    event(25, "connectivityChanged", "CONNECTIVITY", {
      connectivity: "CONNECTED",
      droneId: "drone-sim-07",
    }),
    event(26, "sectorLifecycle", "MISSION", {
      assignedMemberId: "drone-sim-07",
      sectorId: "sector-07",
      state: "ASSIGNED",
    }),
  );
  for (let index = 1; index <= 20; index += 1) {
    auditEvents.push(
      event(index + 26, "sectorLifecycle", "MISSION", {
        assignedMemberId: identifierFor(index),
        sectorId: sectorIdentifierFor(index),
        state: "SEARCHED",
      }),
    );
  }
  auditEvents.push(event(47, "missionLifecycle", "MISSION", { lifecycle: "EXHAUSTED" }));
  return auditEvents;
}

const replayOrderedEvents = replayAuditEvents();

const replayInitialState: DashboardReducedState = {
  ...plannedState(),
  currentMission: {
    identifier: "recorded-mission-synthetic-0001",
    lifecycle: "PLANNED",
    predecessorIdentifier: null,
  },
};

function replayStates(): readonly DashboardReducedState[] {
  return replayOrderedEvents.reduce<DashboardReducedState[]>(
    (statesSoFar, orderedEvent) => {
      const previous = statesSoFar.at(-1);
      if (previous === undefined) {
        throw new Error("replay oracle has no initial state");
      }
      return [...statesSoFar, applyOrderedEventForOracle(previous, orderedEvent)];
    },
    [replayInitialState],
  );
}

export const replayCheckpoints: readonly ReplayCheckpoint[] = replayStates().map((state) => {
  const drone07 = state.fleet.find((member) => member.identifier === "drone-sim-07");
  const sector07 = state.sectors.find((sector) => sector.identifier === "sector-07");
  if (
    drone07?.participation !== "SIMULATED" ||
    sector07 === undefined ||
    state.currentMission === null
  ) {
    throw new Error("replay oracle state is incomplete");
  }
  return {
    auditOrdinal: state.latestAuditOrdinal,
    digest: replayStateDigest(state),
    drone07Connectivity: drone07.connectivity,
    lifecycle: state.currentMission.lifecycle,
    sector07State: sector07.state,
  };
});

export const expectedReplayDigest = replayCheckpoints.at(-1)?.digest ?? "";

/**
 * Provisional R1 fixture convention: checksum the canonical bundle document with the
 * checksum member itself absent. This covers the bundle and every other integrity
 * member without self-reference. R6 replaces this fixture-owned convention with the
 * replay validator's normative checksum material.
 */
function provisionalR1ReplayChecksum(bundleWithoutChecksum: object): string {
  return createHash("sha256").update(canonicalJson(bundleWithoutChecksum), "utf8").digest("hex");
}

function replayBundle(overrides: ReplayFixtureOverrides): DashboardSourceInput {
  const expectedFinalDigest = overrides.expectedFinalDigest ?? expectedReplayDigest;
  const coveredBundle = {
    bundleVersion: "dashboard-replay-bundle/v1",
    events: replayOrderedEvents,
    initialState: replayInitialState,
    scenarioId: "wilderness-missing-person",
    scenarioRevision: 1,
    sessionId: "replay-session-0001",
  };
  const provisionalR1ChecksumMaterial = {
    ...coveredBundle,
    integrity: {
      algorithm: "sha256",
      expectedFinalDigest,
      integrityVersion: "dashboard-replay-integrity/v1",
    },
  };
  const checksum = provisionalR1ReplayChecksum(provisionalR1ChecksumMaterial);
  const integrity: ReplayIntegrityFixture = {
    algorithm: "sha256",
    checksum: overrides.checksum ?? checksum,
    expectedFinalDigest,
    integrityVersion: "dashboard-replay-integrity/v1",
  };
  return sourceInput("replay-bundle", "validated-replay-bundle", {
    ...coveredBundle,
    integrity,
  });
}

function script(inputs: readonly DashboardSourceInput[]): DashboardSourceScript {
  return { fixtureVersion: "dashboard-source-script/v1", inputs };
}

export function fixtureForState(
  viewState: Exclude<DashboardViewState, "replay">,
): DashboardSourceScript {
  const common = [bootstrap(), readiness(), scenarioCatalog()];
  if (viewState === "loading") {
    return script([bootstrap(), sourceSignal("connecting")]);
  }
  if (viewState === "empty") {
    return script([bootstrap(), readiness(), scenarioCatalog([])]);
  }
  if (viewState === "ready") {
    return script([...common, snapshot(plannedState(), [])]);
  }
  if (viewState === "starting") {
    return script([...common, snapshot(plannedState(), []), operationSignal("start", "pending")]);
  }
  const currentLiveState = liveState();
  const running = snapshot(currentLiveState, liveTimeline());
  if (viewState === "resetting") {
    return script([...common, running, operationSignal("reset", "pending")]);
  }
  if (viewState === "retrying") {
    return script([...common, running, sourceSignal("disconnected")]);
  }
  if (viewState === "offline") {
    return script([...common, running, sourceSignal("offline")]);
  }
  if (viewState === "recovered") {
    return script([...common, running, sourceSignal("disconnected"), sourceSignal("recovered")]);
  }
  if (viewState === "staleRuntime") {
    return script([...common, running, sourceSignal("runtimeChanged")]);
  }
  if (viewState === "contractFailure") {
    return script([
      ...common,
      running,
      malformedSourceInput("sse-frame", "dashboard-event", '{"auditOrdinal":7,"event":'),
    ]);
  }
  if (viewState === "exhausted" || viewState === "aborted") {
    const lifecycle = viewState === "exhausted" ? "EXHAUSTED" : "ABORTED";
    const orderedEvent = event(
      currentLiveState.latestAuditOrdinal + 1,
      "missionLifecycle",
      "MISSION",
      { lifecycle },
      "mission-synthetic-0001",
    );
    const stateAfter = applyOrderedEventForOracle(currentLiveState, orderedEvent);
    return script([...common, running, orderedEventInput(orderedEvent, stateAfter)]);
  }
  return script([...common, running]);
}

export function replayFixture(overrides: ReplayFixtureOverrides = {}): DashboardSourceScript {
  return script([bootstrap(), readiness("replay"), scenarioCatalog(), replayBundle(overrides)]);
}

export function malformedBoundaryInputs(
  boundary: ValidationBoundary,
): readonly DashboardSourceInput[] {
  if (boundary === "bootstrap") {
    return [
      sourceInput("bootstrap", "bootstrap", {
        bearer: 17,
        bootstrapVersion: "dashboard-bootstrap/v1",
        runtimeId: "runtime-synthetic-0001",
      }),
    ];
  }
  if (boundary === "readiness") {
    return [
      sourceInput("http-response", "readiness", {
        mode: "degradedLive",
        readinessVersion: "dashboard-readiness/v1",
        ready: "yes",
        reasons: [],
      }),
    ];
  }
  if (boundary === "scenarioCatalog") {
    return [
      sourceInput("http-response", "scenario-catalog", {
        catalogVersion: "scenario-catalog/v1",
        scenarios: "not-an-array",
      }),
    ];
  }
  const malformedCoveredBundle = {
    bundleVersion: "dashboard-replay-bundle/v1",
    events: "not-an-array",
    initialState: replayInitialState,
    scenarioId: "wilderness-missing-person",
    scenarioRevision: 1,
    sessionId: "replay-session-malformed",
  };
  const malformedIntegrity = {
    algorithm: "sha256" as const,
    expectedFinalDigest: "0".repeat(64),
    integrityVersion: "dashboard-replay-integrity/v1" as const,
  };
  const checksum = provisionalR1ReplayChecksum({
    ...malformedCoveredBundle,
    integrity: malformedIntegrity,
  });
  return [
    sourceInput("replay-bundle", "validated-replay-bundle", {
      ...malformedCoveredBundle,
      integrity: {
        ...malformedIntegrity,
        checksum,
      },
    }),
  ];
}

export function telemetryInterpolationInputs(): readonly DashboardSourceInput[] {
  const currentLiveState = liveState();
  const telemetryEvent = event(
    currentLiveState.latestAuditOrdinal + 1,
    "droneTelemetry",
    "TELEMETRY",
    {
      altitudeMetres: 91,
      batteryPercent: 94,
      droneId: "drone-sim-01",
      groundSpeedCentimetresPerSecond: 940,
      headingDegrees: 35,
      latitudeMicrodegrees: 44_497_800,
      longitudeMicrodegrees: -79_229_600,
    },
    "mission-synthetic-0001",
  );
  const stateAfter = applyOrderedEventForOracle(currentLiveState, telemetryEvent);
  return [orderedEventInput(telemetryEvent, stateAfter)];
}

function heartbeatEvents(): Readonly<
  Record<HeartbeatInputBatch["stage"], readonly OrderedDashboardEventFixture[]>
> {
  const mission = "mission-synthetic-0001";
  return {
    degraded: [
      event(
        54,
        "connectivityChanged",
        "CONNECTIVITY",
        { connectivity: "DEGRADED", droneId: "drone-sim-07" },
        mission,
      ),
    ],
    exhausted: [
      ...Array.from({ length: 16 }, (_, offset) => {
        const index = offset + 5;
        return event(
          index + 54,
          "sectorLifecycle",
          "MISSION",
          {
            assignedMemberId: identifierFor(index),
            sectorId: sectorIdentifierFor(index),
            state: "SEARCHED",
          },
          mission,
        );
      }),
      event(75, "missionLifecycle", "MISSION", { lifecycle: "EXHAUSTED" }, mission),
    ],
    offline: [
      event(
        55,
        "connectivityChanged",
        "CONNECTIVITY",
        { connectivity: "OFFLINE", droneId: "drone-sim-07" },
        mission,
      ),
      event(
        56,
        "sectorLifecycle",
        "MISSION",
        {
          assignedMemberId: "drone-sim-07",
          sectorId: "sector-07",
          state: "AT_RISK",
        },
        mission,
      ),
    ],
    recovered: [
      event(
        57,
        "connectivityChanged",
        "CONNECTIVITY",
        { connectivity: "CONNECTED", droneId: "drone-sim-07" },
        mission,
      ),
      event(
        58,
        "sectorLifecycle",
        "MISSION",
        {
          assignedMemberId: "drone-sim-07",
          sectorId: "sector-07",
          state: "ASSIGNED",
        },
        mission,
      ),
    ],
  };
}

export function heartbeatSchedule(): readonly HeartbeatInputBatch[] {
  const batches: HeartbeatInputBatch[] = [];
  let state = heartbeatBaseState();
  const eventsByStage = heartbeatEvents();
  for (const stage of ["degraded", "offline", "recovered", "exhausted"] as const) {
    const inputs: DashboardSourceInput[] = [];
    for (const orderedEvent of eventsByStage[stage]) {
      state = applyOrderedEventForOracle(state, orderedEvent);
      inputs.push(orderedEventInput(orderedEvent, state));
    }
    batches.push({ inputs, stage });
  }
  return batches;
}

export function heartbeatInitialFixture(): DashboardSourceScript {
  return script([
    bootstrap(),
    readiness(),
    scenarioCatalog(),
    snapshot(heartbeatBaseState(), heartbeatTimeline()),
  ]);
}

export function resilienceFaultInputs(fault: ResilienceFault): readonly DashboardSourceInput[] {
  if (fault === "malformedFrame") {
    return [
      malformedSourceInput(
        "sse-frame",
        "dashboard-event",
        '{"frameVersion":"ordered-dashboard-event-frame/v1","event":{"auditOrdinal":7',
      ),
    ];
  }
  if (fault === "streamOverloaded") {
    return [
      sourceInput("sse-frame", "stream-overloaded", {
        controlVersion: "dashboard-stream-overloaded/v1",
        reason: "NON_DROPPABLE_BUFFER_FULL",
      }),
    ];
  }
  const currentLiveState = liveState();
  const successorOrdinal = currentLiveState.latestAuditOrdinal + 1;
  const nextEvent = event(
    successorOrdinal,
    "missionLifecycle",
    "MISSION",
    { lifecycle: "EXHAUSTED" },
    "mission-synthetic-0001",
  );
  const nextState = applyOrderedEventForOracle(currentLiveState, nextEvent);
  const acceptedFrame = orderedEventInput(nextEvent, nextState);
  if (fault === "exactDuplicate") {
    return [acceptedFrame, acceptedFrame];
  }
  if (fault === "digestDivergence") {
    return [orderedEventFrameInput(nextEvent, "0".repeat(64))];
  }
  const invalidOrdinal =
    fault === "ordinalGap" ? successorOrdinal + 1 : currentLiveState.latestAuditOrdinal - 1;
  const invalidEvent = event(
    invalidOrdinal,
    "missionLifecycle",
    "MISSION",
    { lifecycle: "EXHAUSTED" },
    "mission-synthetic-0001",
  );
  const hypotheticalState = { ...nextState, latestAuditOrdinal: invalidOrdinal };
  return [orderedEventFrameInput(invalidEvent, replayStateDigest(hypotheticalState))];
}
