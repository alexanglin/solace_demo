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

export type Connectivity = "CONNECTED" | "DEGRADED" | "OFFLINE" | "NOT_EXECUTED";

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

export interface FleetMemberFixture {
  readonly connectivity: Connectivity;
  readonly identifier: string;
  readonly participation: Participation;
  readonly sectorId: string | null;
  readonly sectorState: SectorState | null;
  readonly telemetry: TelemetryFixture | null;
}

export interface SectorFixture {
  readonly assignedMemberId: string | null;
  readonly identifier: string;
  readonly state: SectorState;
}

export interface TimelineFixture {
  readonly auditOrdinal: number;
  readonly label: string;
  readonly occurredAt: string;
}

export interface ScenarioMemberFixture {
  readonly identifier: string;
  readonly participation: Participation;
}

export interface SectorGeometryFixture {
  readonly geometry: {
    readonly coordinates: readonly (readonly (readonly [number, number])[])[];
    readonly type: "Polygon";
  };
  readonly identifier: string;
  readonly type: "Feature";
}

export interface ScenarioFixture {
  readonly declaredOnlyCount: 3;
  readonly identifier: "wilderness-missing-person";
  readonly lastKnownLocation: {
    readonly label: string;
    readonly latitudeMicrodegrees: number;
    readonly longitudeMicrodegrees: number;
  };
  readonly members: readonly ScenarioMemberFixture[];
  readonly revision: "r1";
  readonly searchAreaSquareKilometres: number;
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
}

export interface DashboardEventFixture {
  readonly data: Readonly<Record<string, boolean | number | string | null>>;
  readonly eventClass: "CONNECTIVITY" | "MISSION" | "TELEMETRY";
  readonly kind: "connectivityChanged" | "droneTelemetry" | "missionLifecycle" | "sectorLifecycle";
  readonly mission: string;
  readonly time: string;
}

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
  const west = -79.25 + column * 0.01;
  const south = 44.47 + row * 0.01;
  const east = west + 0.009;
  const north = south + 0.009;
  return {
    geometry: {
      coordinates: [
        [
          [west, south],
          [east, south],
          [east, north],
          [west, north],
          [west, south],
        ],
      ],
      type: "Polygon",
    },
    identifier: sectorIdentifierFor(index),
    type: "Feature",
  };
}

function scenarioMembers(): readonly ScenarioMemberFixture[] {
  return [
    ...declaredOnlyAgentIds.map((identifier) => ({
      identifier,
      participation: "DECLARED_ONLY" as const,
    })),
    ...Array.from({ length: 20 }, (_, offset) => ({
      identifier: identifierFor(offset + 1),
      participation: "SIMULATED" as const,
    })),
  ].sort((left, right) => byteOrder(left.identifier, right.identifier));
}

const scenario: ScenarioFixture = {
  declaredOnlyCount: 3,
  identifier: "wilderness-missing-person",
  lastKnownLocation: {
    label: "North ridge trail",
    latitudeMicrodegrees: 44_493_100,
    longitudeMicrodegrees: -79_228_400,
  },
  members: scenarioMembers(),
  revision: "r1",
  searchAreaSquareKilometres: 18.4,
  sectors: Array.from({ length: 20 }, (_, offset) => sectorGeometry(offset + 1)),
  simulatedCount: 20,
  summary: "Twenty simulated aircraft sweep twenty bounded wilderness sectors.",
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

function simulatedMember(index: number): FleetMemberFixture {
  return {
    connectivity: connectivityFor(index),
    identifier: identifierFor(index),
    participation: "SIMULATED",
    sectorId: sectorIdentifierFor(index),
    sectorState: sectorStateFor(index),
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

function declaredOnlyMember(identifier: string): FleetMemberFixture {
  return {
    connectivity: "NOT_EXECUTED",
    identifier,
    participation: "DECLARED_ONLY",
    sectorId: null,
    sectorState: null,
    telemetry: null,
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

function liveTimeline(): readonly TimelineFixture[] {
  return [
    {
      auditOrdinal: 5,
      label: "drone-sim-07 offline",
      occurredAt: "2026-08-24T12:00:04.000Z",
    },
    {
      auditOrdinal: 1,
      label: "Mission planned",
      occurredAt: "2026-08-24T12:00:10.000Z",
    },
    {
      auditOrdinal: 4,
      label: "drone-sim-07 connectivity degraded",
      occurredAt: "2026-08-24T12:00:03.000Z",
    },
    {
      auditOrdinal: 2,
      label: "Mission searching",
      occurredAt: "2026-08-24T12:00:01.000Z",
    },
    {
      auditOrdinal: 6,
      label: "sector-07 at risk",
      occurredAt: "2026-08-24T12:00:05.000Z",
    },
    {
      auditOrdinal: 3,
      label: "Twenty sectors assigned",
      occurredAt: "2026-08-24T12:00:02.000Z",
    },
  ];
}

function liveState(lifecycle: MissionLifecycle = "SEARCHING"): DashboardReducedState {
  return {
    canonicalizationVersion: 1,
    currentMission: {
      identifier: "mission-synthetic-0001",
      lifecycle,
      predecessorIdentifier: null,
    },
    fleet: fleet(),
    latestAuditOrdinal: 6,
    sectors: sectors(),
  };
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
        ? { ...member, connectivity: "CONNECTED", sectorState: "UNASSIGNED", telemetry: null }
        : member,
    ),
    latestAuditOrdinal: 0,
    sectors: sectors().map((sector) => ({
      ...sector,
      assignedMemberId: null,
      state: "UNASSIGNED",
    })),
  };
}

function heartbeatBaseState(): DashboardReducedState {
  const state = liveState();
  return {
    ...state,
    fleet: state.fleet.map((member) =>
      member.identifier === "drone-sim-07"
        ? { ...member, connectivity: "CONNECTED", sectorState: "ASSIGNED" }
        : member,
    ),
    sectors: state.sectors.map((sector) =>
      sector.identifier === "sector-07" ? { ...sector, state: "ASSIGNED" } : sector,
    ),
  };
}

function sourceInput(
  channel: DashboardSourceInputChannel,
  name: string,
  document: object,
): DashboardSourceInput {
  return { channel, name, raw: JSON.stringify(document) };
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
  timeline: readonly TimelineFixture[],
): DashboardSourceInput {
  return sourceInput("sse-frame", "snapshot", {
    currentRun: "run-synthetic-0001",
    cursor: `cursor-${state.latestAuditOrdinal.toString()}`,
    digest: replayStateDigest(state),
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
    cursor: `cursor-${orderedEvent.auditOrdinal.toString()}`,
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
      .filter(([key]) => key !== "digest")
      .map(([key, item]) => `${JSON.stringify(key)}:${canonicalJson(item)}`)
      .join(",")}}`;
  }
  throw new TypeError(`unsupported replay oracle value: ${typeof value}`);
}

export function replayStateDigest(state: DashboardReducedState): string {
  const material = `aerial-rescue/canonical/v1\nreplay-state\n${canonicalJson(state)}`;
  return createHash("sha256").update(material, "utf8").digest("hex");
}

function event(
  auditOrdinal: number,
  kind: DashboardEventFixture["kind"],
  eventClass: DashboardEventFixture["eventClass"],
  data: DashboardEventFixture["data"],
  mission = "recorded-mission-synthetic-0001",
): OrderedDashboardEventFixture {
  return {
    auditOrdinal,
    event: {
      data,
      eventClass,
      kind,
      mission,
      time: `2026-08-24T12:00:${String(auditOrdinal).padStart(2, "0")}.000Z`,
    },
  };
}

function replaceMember(
  state: DashboardReducedState,
  droneId: string,
  update: (member: FleetMemberFixture) => FleetMemberFixture,
): readonly FleetMemberFixture[] {
  return state.fleet.map((member) => (member.identifier === droneId ? update(member) : member));
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
  const { data, kind } = orderedEvent.event;
  const nextBase = { ...state, latestAuditOrdinal: orderedEvent.auditOrdinal };
  if (kind === "missionLifecycle") {
    return {
      ...nextBase,
      currentMission:
        state.currentMission === null
          ? null
          : { ...state.currentMission, lifecycle: data["lifecycle"] as MissionLifecycle },
    };
  }
  if (kind === "connectivityChanged") {
    return {
      ...nextBase,
      fleet: replaceMember(state, String(data["droneId"]), (member) => ({
        ...member,
        connectivity: data["connectivity"] as Connectivity,
      })),
    };
  }
  if (kind === "sectorLifecycle") {
    const sectorState = data["state"] as SectorState;
    return {
      ...nextBase,
      fleet: replaceMember(state, String(data["droneId"]), (member) => ({
        ...member,
        sectorState,
      })),
      sectors: replaceSector(state, String(data["sectorId"]), (sector) => ({
        ...sector,
        state: sectorState,
      })),
    };
  }
  return {
    ...nextBase,
    fleet: replaceMember(state, String(data["droneId"]), (member) => ({
      ...member,
      telemetry: {
        altitudeMetres: Number(data["altitudeMetres"]),
        batteryPercent: Number(data["batteryPercent"]),
        groundSpeedCentimetresPerSecond: Number(data["groundSpeedCentimetresPerSecond"]),
        headingDegrees: Number(data["headingDegrees"]),
        latitudeMicrodegrees: Number(data["latitudeMicrodegrees"]),
        longitudeMicrodegrees: Number(data["longitudeMicrodegrees"]),
      },
    })),
  };
}

const replayOrderedEvents: readonly OrderedDashboardEventFixture[] = [
  event(1, "missionLifecycle", "MISSION", { lifecycle: "SEARCHING" }),
  event(2, "sectorLifecycle", "MISSION", {
    droneId: "drone-sim-07",
    sectorId: "sector-07",
    state: "ASSIGNED",
  }),
  event(3, "connectivityChanged", "CONNECTIVITY", {
    connectivity: "DEGRADED",
    droneId: "drone-sim-07",
  }),
  event(4, "connectivityChanged", "CONNECTIVITY", {
    connectivity: "OFFLINE",
    droneId: "drone-sim-07",
  }),
  event(5, "sectorLifecycle", "MISSION", {
    droneId: "drone-sim-07",
    sectorId: "sector-07",
    state: "AT_RISK",
  }),
  event(6, "connectivityChanged", "CONNECTIVITY", {
    connectivity: "CONNECTED",
    droneId: "drone-sim-07",
  }),
  event(7, "sectorLifecycle", "MISSION", {
    droneId: "drone-sim-07",
    sectorId: "sector-07",
    state: "SEARCHED",
  }),
  event(8, "missionLifecycle", "MISSION", { lifecycle: "EXHAUSTED" }),
];

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
  if (drone07 === undefined || sector07 === undefined || state.currentMission === null) {
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

function replayBundle(overrides: ReplayFixtureOverrides): DashboardSourceInput {
  const coveredBundle = {
    bundleVersion: "dashboard-replay-bundle/v1",
    events: replayOrderedEvents,
    expectedFinalDigest: overrides.expectedFinalDigest ?? expectedReplayDigest,
    initialState: replayInitialState,
    sessionId: "replay-session-0001",
  };
  const checksum = createHash("sha256").update(canonicalJson(coveredBundle), "utf8").digest("hex");
  return sourceInput("replay-bundle", "validated-replay-bundle", {
    ...coveredBundle,
    checksum: overrides.checksum ?? checksum,
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
  const running = snapshot(liveState(), liveTimeline());
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
      7,
      "missionLifecycle",
      "MISSION",
      { lifecycle },
      "mission-synthetic-0001",
    );
    const stateAfter = applyOrderedEventForOracle(liveState(), orderedEvent);
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
  return [
    sourceInput("replay-bundle", "validated-replay-bundle", {
      bundleVersion: "dashboard-replay-bundle/v1",
      checksum: "0".repeat(64),
      events: "not-an-array",
      expectedFinalDigest: "0".repeat(64),
      initialState: replayInitialState,
      sessionId: "replay-session-malformed",
    }),
  ];
}

export function telemetryInterpolationInputs(): readonly DashboardSourceInput[] {
  const telemetryEvent = event(
    7,
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
  const stateAfter = applyOrderedEventForOracle(liveState(), telemetryEvent);
  return [orderedEventInput(telemetryEvent, stateAfter)];
}

function heartbeatEvents(): Readonly<
  Record<HeartbeatInputBatch["stage"], readonly OrderedDashboardEventFixture[]>
> {
  const mission = "mission-synthetic-0001";
  return {
    degraded: [
      event(
        7,
        "connectivityChanged",
        "CONNECTIVITY",
        { connectivity: "DEGRADED", droneId: "drone-sim-07" },
        mission,
      ),
    ],
    exhausted: [
      event(
        12,
        "sectorLifecycle",
        "MISSION",
        { droneId: "drone-sim-07", sectorId: "sector-07", state: "SEARCHED" },
        mission,
      ),
      event(13, "missionLifecycle", "MISSION", { lifecycle: "EXHAUSTED" }, mission),
    ],
    offline: [
      event(
        8,
        "connectivityChanged",
        "CONNECTIVITY",
        { connectivity: "OFFLINE", droneId: "drone-sim-07" },
        mission,
      ),
      event(
        9,
        "sectorLifecycle",
        "MISSION",
        { droneId: "drone-sim-07", sectorId: "sector-07", state: "AT_RISK" },
        mission,
      ),
    ],
    recovered: [
      event(
        10,
        "connectivityChanged",
        "CONNECTIVITY",
        { connectivity: "CONNECTED", droneId: "drone-sim-07" },
        mission,
      ),
      event(
        11,
        "sectorLifecycle",
        "MISSION",
        { droneId: "drone-sim-07", sectorId: "sector-07", state: "ASSIGNED" },
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
    snapshot(heartbeatBaseState(), liveTimeline()),
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
  const nextEvent = event(
    7,
    "missionLifecycle",
    "MISSION",
    { lifecycle: "EXHAUSTED" },
    "mission-synthetic-0001",
  );
  const nextState = applyOrderedEventForOracle(liveState(), nextEvent);
  const acceptedFrame = orderedEventInput(nextEvent, nextState);
  if (fault === "exactDuplicate") {
    return [acceptedFrame, acceptedFrame];
  }
  if (fault === "digestDivergence") {
    return [orderedEventFrameInput(nextEvent, "0".repeat(64))];
  }
  const invalidOrdinal = fault === "ordinalGap" ? 8 : 5;
  const invalidEvent = event(
    invalidOrdinal,
    "missionLifecycle",
    "MISSION",
    { lifecycle: "EXHAUSTED" },
    "mission-synthetic-0001",
  );
  return [orderedEventFrameInput(invalidEvent, "0".repeat(64))];
}
