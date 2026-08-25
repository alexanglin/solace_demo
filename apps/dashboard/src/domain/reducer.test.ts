import { describe, expect, test } from "vitest";

import type { OrderedDashboardEvent } from "../contracts/generated";
import { replayStateDigest } from "./canonical";
import {
  checkpointFromAnchor,
  checkpointFromReplayBundle,
  checkpointFromSnapshot,
  emptyReducerCheckpoint,
  foldOrderedDashboardEvent,
  foldVerifiedOrderedDashboardEvent,
  initializePreparedReducerCheckpoint,
} from "./reducer";
import {
  DECLARED_ONLY_MEMBER_ID,
  MISSION_ID,
  SECOND_SECTOR_ID,
  SECOND_SIMULATED_MEMBER_ID,
  SECTOR_ID,
  SIMULATED_MEMBER_ID,
  checkpointAt,
  connectivityEvent,
  initialCheckpoint,
  missionEvent,
  preparedState,
  refusalOf,
  sectorEvent,
  telemetryEvent,
} from "../../tests/unit-support/reducer-fixtures";

const LOWERCASE_DIGEST = "a".repeat(64);

describe("reducer checkpoint anchors", () => {
  test("constructs the production empty checkpoint", () => {
    // Arrange
    const expectedState = {
      canonicalizationVersion: 1,
      currentMission: null,
      fleet: [],
      latestAuditOrdinal: 0,
      sectors: [],
      stateVersion: 1,
    };

    // Act
    const checkpoint = emptyReducerCheckpoint();

    // Assert
    expect(checkpoint).toEqual({ latestEventDigest: null, state: expectedState });
  });

  test("constructs a sorted prepared checkpoint from production inputs", () => {
    // Arrange
    const simulatedMemberIds = [SECOND_SIMULATED_MEMBER_ID, SIMULATED_MEMBER_ID];
    const declaredOnlyMemberIds = [DECLARED_ONLY_MEMBER_ID];
    const sectorIds = [SECOND_SECTOR_ID, SECTOR_ID];

    // Act
    const outcome = initializePreparedReducerCheckpoint({
      identifier: MISSION_ID,
      predecessorIdentifier: "mission-synthetic-0000",
      simulatedMemberIds,
      declaredOnlyMemberIds,
      sectorIds,
    });

    // Assert
    expect(outcome.ok).toBe(true);
    if (!outcome.ok) {
      throw new Error(outcome.failure.code);
    }
    expect(outcome.checkpoint.state.currentMission).toEqual({
      identifier: MISSION_ID,
      lifecycle: "PLANNED",
      predecessorIdentifier: "mission-synthetic-0000",
    });
    expect(outcome.checkpoint.state.fleet).toEqual([
      { identifier: DECLARED_ONLY_MEMBER_ID, participation: "DECLARED_ONLY" },
      {
        connectivity: "CONNECTED",
        identifier: SIMULATED_MEMBER_ID,
        participation: "SIMULATED",
        telemetry: null,
      },
      {
        connectivity: "CONNECTED",
        identifier: SECOND_SIMULATED_MEMBER_ID,
        participation: "SIMULATED",
        telemetry: null,
      },
    ]);
    expect(outcome.checkpoint.state.sectors).toEqual([
      { assignedMemberId: null, identifier: SECTOR_ID, state: "UNASSIGNED" },
      { assignedMemberId: null, identifier: SECOND_SECTOR_ID, state: "UNASSIGNED" },
    ]);
    expect(outcome.checkpoint.state.latestAuditOrdinal).toBe(0);
    expect(outcome.checkpoint.latestEventDigest).toBeNull();
  });

  test("snapshots prepared identity lists without retaining mutable inputs", () => {
    // Arrange
    const simulatedMemberIds = [SIMULATED_MEMBER_ID];
    const declaredOnlyMemberIds = [DECLARED_ONLY_MEMBER_ID];
    const sectorIds = [SECTOR_ID];
    const prepared = {
      identifier: MISSION_ID,
      predecessorIdentifier: null,
      simulatedMemberIds,
      declaredOnlyMemberIds,
      sectorIds,
    };

    // Act
    const outcome = initializePreparedReducerCheckpoint(prepared);
    prepared.identifier = "mission-mutated";
    simulatedMemberIds[0] = "drone-mutated-sim";
    declaredOnlyMemberIds[0] = "drone-mutated-declared";
    sectorIds[0] = "sector-mutated";

    // Assert
    expect(outcome).toMatchObject({
      checkpoint: {
        latestEventDigest: null,
        state: {
          currentMission: { identifier: MISSION_ID },
          fleet: [{ identifier: DECLARED_ONLY_MEMBER_ID }, { identifier: SIMULATED_MEMBER_ID }],
          sectors: [{ identifier: SECTOR_ID }],
        },
      },
      ok: true,
    });
  });

  test("sorts prepared member and sector identifiers by UTF-8 bytes", () => {
    // Arrange
    const lowerUtf8Identifier = "\u{e000}";
    const higherUtf8Identifier = "\u{10000}";

    // Act
    const outcome = initializePreparedReducerCheckpoint({
      identifier: MISSION_ID,
      predecessorIdentifier: null,
      simulatedMemberIds: [higherUtf8Identifier, lowerUtf8Identifier],
      declaredOnlyMemberIds: [],
      sectorIds: [higherUtf8Identifier, lowerUtf8Identifier],
    });

    // Assert
    expect(outcome.ok).toBe(true);
    if (!outcome.ok) {
      throw new Error(outcome.failure.code);
    }
    expect(outcome.checkpoint.state.fleet.map(({ identifier }) => identifier)).toEqual([
      lowerUtf8Identifier,
      higherUtf8Identifier,
    ]);
    expect(outcome.checkpoint.state.sectors.map(({ identifier }) => identifier)).toEqual([
      lowerUtf8Identifier,
      higherUtf8Identifier,
    ]);
  });

  test("accepts the empty and witnessed ordinal anchors", () => {
    // Arrange
    const emptyState = preparedState();
    const witnessedState = preparedState({ latestAuditOrdinal: 4 });

    // Act
    const outcomes = [
      checkpointFromAnchor(emptyState, null),
      checkpointFromAnchor(witnessedState, LOWERCASE_DIGEST),
    ];

    // Assert
    expect(outcomes).toEqual([
      { checkpoint: { latestEventDigest: null, state: emptyState }, ok: true },
      {
        checkpoint: { latestEventDigest: LOWERCASE_DIGEST, state: witnessedState },
        ok: true,
      },
    ]);
  });

  test("refuses a malformed witness before the ordinal pairing", () => {
    // Arrange
    const state = preparedState();

    // Act
    const outcome = checkpointFromAnchor(state, "A".repeat(64));

    // Assert
    expect(outcome).toEqual({
      failure: { attribute: "latestEventDigest", code: "WITNESS_FORM", value: "A".repeat(64) },
      ok: false,
    });
  });

  test("refuses malformed witness form before noncanonical state and pairing", () => {
    // Arrange
    const state = preparedState({
      fleet: [...preparedState().fleet].reverse(),
      latestAuditOrdinal: 1,
    });
    const malformedWitness = "A".repeat(64);

    // Act
    const outcome = checkpointFromAnchor(state, malformedWitness);

    // Assert
    expect(outcome).toEqual({
      failure: {
        attribute: "latestEventDigest",
        code: "WITNESS_FORM",
        value: malformedWitness,
      },
      ok: false,
    });
  });

  test.each([
    { latestAuditOrdinal: 0, witness: LOWERCASE_DIGEST },
    { latestAuditOrdinal: 1, witness: null },
  ])("refuses the invalid ordinal-witness pairing %#", ({ latestAuditOrdinal, witness }) => {
    // Arrange
    const state = preparedState({ latestAuditOrdinal });

    // Act
    const outcome = checkpointFromAnchor(state, witness);

    // Assert
    expect(outcome).toEqual({
      failure: {
        attribute: "latestEventDigest",
        code: "ORDINAL_WITNESS",
        value: witness,
      },
      ok: false,
    });
  });

  test.each([
    {
      label: "unsorted fleet",
      state: preparedState({ fleet: [...preparedState().fleet].reverse() }),
    },
    {
      label: "duplicate sector",
      state: preparedState({
        sectors: [
          { identifier: SECTOR_ID, state: "UNASSIGNED", assignedMemberId: null },
          { identifier: SECTOR_ID, state: "UNASSIGNED", assignedMemberId: null },
        ],
      }),
    },
    {
      label: "invalid held assignment",
      state: preparedState({
        sectors: [
          {
            identifier: SECTOR_ID,
            state: "ASSIGNED",
            assignedMemberId: DECLARED_ONLY_MEMBER_ID,
          },
        ],
      }),
    },
    {
      label: "missing held assignment",
      state: preparedState({
        sectors: [
          {
            identifier: SECTOR_ID,
            state: "AT_RISK",
            assignedMemberId: null,
          },
        ],
      }),
    },
    {
      label: "fleet without a mission",
      state: preparedState({ currentMission: null, sectors: [] }),
    },
    {
      label: "sectors without a mission",
      state: preparedState({ currentMission: null, fleet: [] }),
    },
    {
      label: "advanced empty state without a mission",
      state: preparedState({
        currentMission: null,
        fleet: [],
        latestAuditOrdinal: 1,
        sectors: [],
      }),
    },
  ])("refuses a noncanonical anchor with $label", ({ state }) => {
    // Arrange
    const witness = state.latestAuditOrdinal === 0 ? null : LOWERCASE_DIGEST;

    // Act
    const outcome = checkpointFromAnchor(state, witness);

    // Assert
    expect(outcome).toEqual({
      failure: { attribute: "state", code: "NONCANONICAL_ANCHOR_STATE", value: state },
      ok: false,
    });
  });

  test("accepts a truly empty unprepared anchor", () => {
    // Arrange
    const checkpoint = emptyReducerCheckpoint();

    // Act
    const outcome = checkpointFromAnchor(checkpoint.state, checkpoint.latestEventDigest);

    // Assert
    expect(outcome).toEqual({ checkpoint, ok: true });
  });

  test.each([
    {
      code: "DUPLICATE_MEMBER",
      declaredOnlyMemberIds: [DECLARED_ONLY_MEMBER_ID],
      sectorIds: [SECTOR_ID],
      simulatedMemberIds: [SIMULATED_MEMBER_ID, SIMULATED_MEMBER_ID],
      value: SIMULATED_MEMBER_ID,
    },
    {
      code: "DUPLICATE_MEMBER",
      declaredOnlyMemberIds: [SIMULATED_MEMBER_ID],
      sectorIds: [SECTOR_ID],
      simulatedMemberIds: [SIMULATED_MEMBER_ID],
      value: SIMULATED_MEMBER_ID,
    },
    {
      code: "DUPLICATE_SECTOR",
      declaredOnlyMemberIds: [DECLARED_ONLY_MEMBER_ID],
      sectorIds: [SECTOR_ID, SECTOR_ID],
      simulatedMemberIds: [SIMULATED_MEMBER_ID],
      value: SECTOR_ID,
    },
  ] as const)(
    "refuses $code while preparing semantic identities %#",
    ({ code, declaredOnlyMemberIds, sectorIds, simulatedMemberIds, value }) => {
      // Arrange
      const prepared = {
        identifier: MISSION_ID,
        predecessorIdentifier: null,
        simulatedMemberIds,
        declaredOnlyMemberIds,
        sectorIds,
      };

      // Act
      const outcome = initializePreparedReducerCheckpoint(prepared);

      // Assert
      expect(outcome).toEqual({
        failure: { attribute: "identifier", code, value },
        ok: false,
      });
    },
  );

  test("refuses a noncanonical anchor before its invalid ordinal-witness pairing", () => {
    // Arrange
    const state = preparedState({
      fleet: [...preparedState().fleet].reverse(),
      latestAuditOrdinal: 1,
    });

    // Act
    const outcome = checkpointFromAnchor(state, null);

    // Assert
    expect(outcome).toEqual({
      failure: { attribute: "state", code: "NONCANONICAL_ANCHOR_STATE", value: state },
      ok: false,
    });
  });

  test("constructs equivalent snapshot and replay anchors from generated wire types", async () => {
    // Arrange
    const state = preparedState();
    const digest = await replayStateDigest(state);

    // Act
    const snapshot = await checkpointFromSnapshot({ digest, latestEventDigest: null, state });
    const replay = checkpointFromReplayBundle({ initialState: state, latestEventDigest: null });

    // Assert
    expect(snapshot).toEqual({ checkpoint: { latestEventDigest: null, state }, ok: true });
    expect(replay).toEqual(snapshot);
  });

  test.each([
    { code: "SERVER_DIGEST_FORM", digest: "INVALID" },
    { code: "SERVER_DIGEST_MISMATCH", digest: "f".repeat(64) },
  ] as const)("refuses snapshot $code", async ({ code, digest }) => {
    // Arrange
    const state = preparedState();

    // Act
    const outcome = await checkpointFromSnapshot({ digest, latestEventDigest: null, state });

    // Assert
    expect(outcome).toEqual({
      failure: { attribute: "digest", code, value: digest },
      ok: false,
    });
  });

  test("retains an anchor refusal without evaluating snapshot digest", async () => {
    // Arrange
    const state = preparedState({ fleet: [...preparedState().fleet].reverse() });

    // Act
    const outcome = await checkpointFromSnapshot({
      digest: "INVALID",
      latestEventDigest: null,
      state,
    });

    // Assert
    expect(outcome).toEqual({
      failure: { attribute: "state", code: "NONCANONICAL_ANCHOR_STATE", value: state },
      ok: false,
    });
  });
});

describe("ordered event discipline", () => {
  test("applies a successor and advances both ordinal witnesses immutably", async () => {
    // Arrange
    const checkpoint = initialCheckpoint();
    const event = missionEvent(1);
    const stateBefore = structuredClone(checkpoint.state);

    // Act
    const result = await foldOrderedDashboardEvent(checkpoint, event);

    // Assert
    expect(result.ok).toBe(true);
    if (!result.ok) {
      throw new Error(result.failure.code);
    }
    expect(result.disposition).toBe("APPLIED");
    expect(result.checkpoint.state.currentMission?.lifecycle).toBe("SEARCHING");
    expect(result.checkpoint.state.latestAuditOrdinal).toBe(1);
    expect(result.checkpoint.latestEventDigest).toMatch(/^[0-9a-f]{64}$/u);
    expect(result.checkpoint).not.toBe(checkpoint);
    expect(checkpoint.state).toEqual(stateBefore);
  });

  test("recognizes an exact duplicate before evaluating its invalid target", async () => {
    // Arrange
    const duplicate = connectivityEvent(4, "missing-member");
    const checkpoint = await checkpointAt(duplicate);

    // Act
    const result = await foldOrderedDashboardEvent(checkpoint, duplicate);

    // Assert
    expect(result).toEqual({ checkpoint, disposition: "DUPLICATE", ok: true });
  });

  test("refuses malformed duplicate input before ordinal handling", async () => {
    // Arrange
    const prior = missionEvent(4);
    const checkpoint = await checkpointAt(prior);
    const malformed = {
      ...prior,
      event: { ...prior.event, data: { lifecycle: "SEARCHING", unexpected: true } },
    } as unknown as OrderedDashboardEvent;

    // Act
    const result = await foldOrderedDashboardEvent(checkpoint, malformed);

    // Assert
    expect(refusalOf(result)).toEqual({
      attribute: "data",
      code: "EVENT_DATA",
      value: malformed.event.data,
    });
  });

  test("refuses malformed event input before checkpoint-anchor and gap handling", async () => {
    // Arrange
    const checkpoint = initialCheckpoint(
      preparedState({ fleet: [...preparedState().fleet].reverse() }),
    );
    const malformed = {
      ...missionEvent(7),
      event: { ...missionEvent(7).event, data: null },
    } as unknown as OrderedDashboardEvent;

    // Act
    const result = await foldOrderedDashboardEvent(checkpoint, malformed);

    // Assert
    expect(refusalOf(result)).toEqual({ attribute: "data", code: "EVENT_DATA", value: null });
    expect(result.checkpoint).toBe(checkpoint);
  });

  test.each([
    {
      code: "WITNESS_FORM",
      checkpoint: {
        latestEventDigest: "A".repeat(64),
        state: preparedState({ fleet: [...preparedState().fleet].reverse() }),
      },
    },
    {
      code: "NONCANONICAL_ANCHOR_STATE",
      checkpoint: initialCheckpoint(preparedState({ fleet: [...preparedState().fleet].reverse() })),
    },
    {
      code: "ORDINAL_WITNESS",
      checkpoint: initialCheckpoint(preparedState({ latestAuditOrdinal: 1 })),
    },
  ] as const)("refuses an invalid checkpoint with $code", async ({ checkpoint, code }) => {
    // Arrange
    const successor = missionEvent(checkpoint.state.latestAuditOrdinal + 1);

    // Act
    const result = await foldOrderedDashboardEvent(checkpoint, successor);

    // Assert
    expect(refusalOf(result).code).toBe(code);
    expect(result.checkpoint).toBe(checkpoint);
  });

  test.each([
    {
      code: "ASSIGNMENT_FORBIDDEN",
      event: sectorEvent(7, "UNASSIGNED", SIMULATED_MEMBER_ID),
      value: SIMULATED_MEMBER_ID,
    },
    {
      code: "ASSIGNMENT_REQUIRED",
      event: sectorEvent(7, "AT_RISK", null),
      value: null,
    },
  ] as const)("refuses $code at the boundary before a gap", async ({ code, event, value }) => {
    // Arrange
    const checkpoint = await checkpointAt(
      missionEvent(4),
      preparedState({ latestAuditOrdinal: 4 }),
    );

    // Act
    const result = await foldOrderedDashboardEvent(checkpoint, event);

    // Assert
    expect(refusalOf(result)).toEqual({ attribute: "assignedMemberId", code, value });
    expect(result.checkpoint).toBe(checkpoint);
  });

  test("refuses divergent same-ordinal content without changing the checkpoint", async () => {
    // Arrange
    const checkpoint = await checkpointAt(missionEvent(4));
    const divergent = missionEvent(4, "ABORTED", "another-mission");

    // Act
    const result = await foldOrderedDashboardEvent(checkpoint, divergent);

    // Assert
    expect(refusalOf(result)).toEqual({
      attribute: "auditOrdinal",
      code: "ORDINAL_DIVERGENCE",
      value: 4,
    });
    expect(result.checkpoint).toBe(checkpoint);
  });

  test.each([
    {
      code: "ORDINAL_REGRESSION",
      eventOrdinal: 3,
      latestOrdinal: 4,
    },
    {
      code: "ORDINAL_GAP",
      eventOrdinal: 7,
      latestOrdinal: 4,
    },
  ] as const)(
    "refuses $code before mission semantics",
    async ({ code, eventOrdinal, latestOrdinal }) => {
      // Arrange
      const checkpoint = await checkpointAt(
        missionEvent(latestOrdinal),
        preparedState({ latestAuditOrdinal: latestOrdinal }),
      );
      const event = missionEvent(eventOrdinal, "SEARCHING", "another-mission");

      // Act
      const result = await foldOrderedDashboardEvent(checkpoint, event);

      // Assert
      expect(refusalOf(result)).toEqual({
        attribute: "auditOrdinal",
        code,
        value: eventOrdinal,
      });
      expect(result.checkpoint).toBe(checkpoint);
    },
  );

  test("refuses a successor when no mission is prepared", async () => {
    // Arrange
    const checkpoint = emptyReducerCheckpoint();

    // Act
    const result = await foldOrderedDashboardEvent(checkpoint, missionEvent(1));

    // Assert
    expect(refusalOf(result)).toEqual({
      attribute: "currentMission",
      code: "MISSION_UNPREPARED",
      value: null,
    });
  });

  test("refuses a successor from another mission", async () => {
    // Arrange
    const checkpoint = initialCheckpoint();

    // Act
    const result = await foldOrderedDashboardEvent(
      checkpoint,
      missionEvent(1, "SEARCHING", "another-mission"),
    );

    // Assert
    expect(refusalOf(result)).toEqual({
      attribute: "mission",
      code: "MISSION_MISMATCH",
      value: "another-mission",
    });
  });
});

describe("event-owned state updates", () => {
  test("telemetry supersedes only one simulated member reading and preserves connectivity", async () => {
    // Arrange
    const checkpoint = initialCheckpoint();
    const event = telemetryEvent(1);

    // Act
    const result = await foldOrderedDashboardEvent(checkpoint, event);

    // Assert
    expect(result.ok).toBe(true);
    if (!result.ok) {
      throw new Error(result.failure.code);
    }
    const member = result.checkpoint.state.fleet.find(
      ({ identifier }) => identifier === SIMULATED_MEMBER_ID,
    );
    expect(member).toEqual({
      identifier: SIMULATED_MEMBER_ID,
      participation: "SIMULATED",
      connectivity: "CONNECTED",
      telemetry: {
        latitudeMicrodegrees: 44_475_000,
        longitudeMicrodegrees: -79_245_000,
        batteryPercent: 87,
        altitudeMetres: 92,
        headingDegrees: 145,
        groundSpeedCentimetresPerSecond: 960,
      },
    });
  });

  test("connectivity changes no telemetry or sector assignment", async () => {
    // Arrange
    const telemetry = {
      latitudeMicrodegrees: 1,
      longitudeMicrodegrees: 2,
      batteryPercent: 3,
      altitudeMetres: 4,
      headingDegrees: 5,
      groundSpeedCentimetresPerSecond: 6,
    };
    const state = preparedState({
      fleet: preparedState().fleet.map((member) =>
        member.identifier === SIMULATED_MEMBER_ID && member.participation === "SIMULATED"
          ? { ...member, telemetry }
          : member,
      ),
      sectors: [
        { identifier: SECTOR_ID, state: "ASSIGNED", assignedMemberId: SIMULATED_MEMBER_ID },
      ],
    });

    // Act
    const result = await foldOrderedDashboardEvent(initialCheckpoint(state), connectivityEvent(1));

    // Assert
    expect(result.ok).toBe(true);
    if (!result.ok) {
      throw new Error(result.failure.code);
    }
    const member = result.checkpoint.state.fleet.find(
      ({ identifier }) => identifier === SIMULATED_MEMBER_ID,
    );
    expect(member).toMatchObject({ connectivity: "OFFLINE", telemetry });
    expect(result.checkpoint.state.sectors).toEqual(state.sectors);
  });

  test("sector lifecycle changes only the sector authority", async () => {
    // Arrange
    const checkpoint = initialCheckpoint();
    const event = sectorEvent(1);

    // Act
    const result = await foldOrderedDashboardEvent(checkpoint, event);

    // Assert
    expect(result.ok).toBe(true);
    if (!result.ok) {
      throw new Error(result.failure.code);
    }
    expect(result.checkpoint.state.sectors[0]).toEqual({
      assignedMemberId: SIMULATED_MEMBER_ID,
      identifier: SECTOR_ID,
      state: "ASSIGNED",
    });
    expect(result.checkpoint.state.fleet).toEqual(checkpoint.state.fleet);
  });

  test("an unchanged sector value still advances a valid successor witness", async () => {
    // Arrange
    const checkpoint = initialCheckpoint();
    const event = sectorEvent(1, "UNASSIGNED", null);

    // Act
    const result = await foldOrderedDashboardEvent(checkpoint, event);

    // Assert
    expect(result).toMatchObject({ disposition: "APPLIED", ok: true });
    expect(result.checkpoint.state.latestAuditOrdinal).toBe(1);
    expect(result.checkpoint.state.sectors).toEqual(checkpoint.state.sectors);
  });

  test("mission lifecycle changes only the current mission", async () => {
    // Arrange
    const checkpoint = initialCheckpoint();

    // Act
    const result = await foldOrderedDashboardEvent(checkpoint, missionEvent(1, "EXHAUSTED"));

    // Assert
    expect(result.ok).toBe(true);
    if (!result.ok) {
      throw new Error(result.failure.code);
    }
    expect(result.checkpoint.state.currentMission).toEqual({
      identifier: MISSION_ID,
      lifecycle: "EXHAUSTED",
      predecessorIdentifier: null,
    });
    expect(result.checkpoint.state.fleet).toEqual(checkpoint.state.fleet);
    expect(result.checkpoint.state.sectors).toEqual(checkpoint.state.sectors);
  });

  test("preserves canonical fleet and sector byte order after a fold", async () => {
    // Arrange
    const checkpoint = initialCheckpoint();

    // Act
    const result = await foldOrderedDashboardEvent(checkpoint, missionEvent(1));

    // Assert
    expect(result.ok).toBe(true);
    if (!result.ok) {
      throw new Error(result.failure.code);
    }
    expect(result.checkpoint.state.fleet.map(({ identifier }) => identifier)).toEqual([
      DECLARED_ONLY_MEMBER_ID,
      SIMULATED_MEMBER_ID,
      SECOND_SIMULATED_MEMBER_ID,
    ]);
    expect(result.checkpoint.state.sectors.map(({ identifier }) => identifier)).toEqual([
      SECTOR_ID,
      SECOND_SECTOR_ID,
    ]);
  });
});

describe("event semantic refusals", () => {
  test.each([
    {
      code: "UNKNOWN_MEMBER",
      event: connectivityEvent(1, "missing-member"),
      value: "missing-member",
    },
    {
      code: "DECLARED_ONLY_MEMBER",
      event: connectivityEvent(1, DECLARED_ONLY_MEMBER_ID),
      value: DECLARED_ONLY_MEMBER_ID,
    },
    {
      code: "UNKNOWN_MEMBER",
      event: telemetryEvent(1, "missing-member"),
      value: "missing-member",
    },
    {
      code: "DECLARED_ONLY_MEMBER",
      event: telemetryEvent(1, DECLARED_ONLY_MEMBER_ID),
      value: DECLARED_ONLY_MEMBER_ID,
    },
  ] as const)("refuses $code for a fleet event %#", async ({ code, event, value }) => {
    // Arrange
    const checkpoint = initialCheckpoint();

    // Act
    const result = await foldOrderedDashboardEvent(checkpoint, event);

    // Assert
    expect(refusalOf(result)).toEqual({ attribute: "droneId", code, value });
    expect(result.checkpoint).toBe(checkpoint);
  });

  test("refuses an unknown sector", async () => {
    // Arrange
    const checkpoint = initialCheckpoint();

    // Act
    const result = await foldOrderedDashboardEvent(
      checkpoint,
      sectorEvent(1, "ASSIGNED", SIMULATED_MEMBER_ID, "missing-sector"),
    );

    // Assert
    expect(refusalOf(result)).toEqual({
      attribute: "sectorId",
      code: "UNKNOWN_SECTOR",
      value: "missing-sector",
    });
  });

  test.each([
    {
      attribute: "assignedMemberId",
      code: "ASSIGNMENT_FORBIDDEN",
      event: sectorEvent(1, "UNASSIGNED", SIMULATED_MEMBER_ID),
      value: SIMULATED_MEMBER_ID,
    },
    {
      attribute: "assignedMemberId",
      code: "ASSIGNMENT_REQUIRED",
      event: sectorEvent(1, "AT_RISK", null),
      value: null,
    },
    {
      attribute: "assignedMemberId",
      code: "INVALID_ASSIGNEE",
      event: sectorEvent(1, "ASSIGNED", "missing-member"),
      value: "missing-member",
    },
    {
      attribute: "assignedMemberId",
      code: "INVALID_ASSIGNEE",
      event: sectorEvent(1, "SEARCHED", DECLARED_ONLY_MEMBER_ID),
      value: DECLARED_ONLY_MEMBER_ID,
    },
  ] as const)("refuses $code sector assignment %#", async ({ attribute, code, event, value }) => {
    // Arrange
    const checkpoint = initialCheckpoint();

    // Act
    const result = await foldOrderedDashboardEvent(checkpoint, event);

    // Assert
    expect(refusalOf(result)).toEqual({ attribute, code, value });
    expect(result.checkpoint).toBe(checkpoint);
  });

  test("refuses malformed data before dispatching a known kind", async () => {
    // Arrange
    const malformed = {
      ...telemetryEvent(1),
      event: { ...telemetryEvent(1).event, data: { droneId: SIMULATED_MEMBER_ID } },
    } as unknown as OrderedDashboardEvent;

    // Act
    const result = await foldOrderedDashboardEvent(initialCheckpoint(), malformed);

    // Assert
    expect(refusalOf(result)).toEqual({
      attribute: "data",
      code: "EVENT_DATA",
      value: malformed.event.data,
    });
  });

  test.each([
    {
      attribute: "auditOrdinal",
      candidate: null,
      value: undefined,
    },
    {
      attribute: "auditOrdinal",
      candidate: { auditOrdinal: 0, event: missionEvent(1).event },
      value: 0,
    },
    {
      attribute: "auditOrdinal",
      candidate: { ...missionEvent(1), unexpected: true },
      value: 1,
    },
    {
      attribute: "event",
      candidate: { auditOrdinal: 1, event: null },
      value: null,
    },
    {
      attribute: "mission",
      candidate: {
        ...missionEvent(1),
        event: { ...missionEvent(1).event, mission: "INVALID_MISSION" },
      },
      value: "INVALID_MISSION",
    },
    {
      attribute: "time",
      candidate: {
        ...missionEvent(1),
        event: { ...missionEvent(1).event, time: "not-an-instant" },
      },
      value: "not-an-instant",
    },
    {
      attribute: "data",
      candidate: {
        ...connectivityEvent(1),
        event: {
          ...connectivityEvent(1).event,
          data: { connectivity: "UNKNOWN", droneId: SIMULATED_MEMBER_ID },
        },
      },
      value: { connectivity: "UNKNOWN", droneId: SIMULATED_MEMBER_ID },
    },
    {
      attribute: "data",
      candidate: {
        ...missionEvent(1),
        event: { ...missionEvent(1).event, eventClass: "TELEMETRY" },
      },
      value: missionEvent(1).event.data,
    },
  ])("refuses malformed boundary candidate %#", async ({ attribute, candidate, value }) => {
    // Arrange
    const checkpoint = initialCheckpoint();

    // Act
    const result = await foldOrderedDashboardEvent(
      checkpoint,
      candidate as unknown as OrderedDashboardEvent,
    );

    // Assert
    expect(refusalOf(result)).toEqual({ attribute, code: "EVENT_DATA", value });
    expect(result.checkpoint).toBe(checkpoint);
  });

  test.each(["2026-02-29T12:00:00.000Z", "2026-02-30T12:00:00.000Z"])(
    "refuses impossible calendar instant %s before ordinal and mission semantics",
    async (instant) => {
      // Arrange
      const checkpoint = await checkpointAt(
        missionEvent(4),
        preparedState({ latestAuditOrdinal: 4 }),
      );
      const baseline = missionEvent(7, "SEARCHING", "another-mission");
      const candidate = {
        ...baseline,
        event: { ...baseline.event, time: instant },
      };

      // Act
      const result = await foldOrderedDashboardEvent(checkpoint, candidate);

      // Assert
      expect(refusalOf(result)).toEqual({ attribute: "time", code: "EVENT_DATA", value: instant });
      expect(result.checkpoint).toBe(checkpoint);
    },
  );

  test("accepts an exact leap-day instant with millisecond precision and literal Z", async () => {
    // Arrange
    const baseline = missionEvent(1);
    const event = {
      ...baseline,
      event: { ...baseline.event, time: "2024-02-29T23:59:59.999Z" },
    };

    // Act
    const result = await foldOrderedDashboardEvent(initialCheckpoint(), event);

    // Assert
    expect(result).toMatchObject({ disposition: "APPLIED", ok: true });
  });

  test("refuses telemetry outside a schema-owned range", async () => {
    // Arrange
    const baseline = telemetryEvent(1);
    const malformed = {
      ...baseline,
      event: {
        ...baseline.event,
        data: { ...baseline.event.data, batteryPercent: 101 },
      },
    } as OrderedDashboardEvent;

    // Act
    const result = await foldOrderedDashboardEvent(initialCheckpoint(), malformed);

    // Assert
    expect(refusalOf(result)).toEqual({
      attribute: "data",
      code: "EVENT_DATA",
      value: malformed.event.data,
    });
  });

  test("refuses an unprojected kind after the common checks", async () => {
    // Arrange
    const unprojected = {
      ...missionEvent(1),
      event: { ...missionEvent(1).event, kind: "missionHalo" },
    } as unknown as OrderedDashboardEvent;

    // Act
    const result = await foldOrderedDashboardEvent(initialCheckpoint(), unprojected);

    // Assert
    expect(refusalOf(result)).toEqual({
      attribute: "kind",
      code: "UNPROJECTED",
      value: "missionHalo",
    });
  });

  test("refuses an unprojected kind before a compound invalid mission", async () => {
    // Arrange
    const baseline = missionEvent(1);
    const compoundInvalid = {
      ...baseline,
      event: { ...baseline.event, kind: "unknownKind", mission: "INVALID_MISSION" },
    } as unknown as OrderedDashboardEvent;

    // Act
    const result = await foldOrderedDashboardEvent(initialCheckpoint(), compoundInvalid);

    // Assert
    expect(refusalOf(result)).toEqual({
      attribute: "kind",
      code: "UNPROJECTED",
      value: "unknownKind",
    });
  });
});

describe("server state digest verification", () => {
  test("accepts a successor whose recomputed state digest matches", async () => {
    // Arrange
    const checkpoint = initialCheckpoint();
    const event = missionEvent(1);
    const folded = await foldOrderedDashboardEvent(checkpoint, event);
    if (!folded.ok) {
      throw new Error(folded.failure.code);
    }
    const serverDigest = await replayStateDigest(folded.checkpoint.state);

    // Act
    const result = await foldVerifiedOrderedDashboardEvent(checkpoint, event, serverDigest);

    // Assert
    expect(result.ok).toBe(true);
    if (!result.ok) {
      throw new Error(result.failure.code);
    }
    expect(result.disposition).toBe("APPLIED");
    expect(result.checkpoint).toEqual(folded.checkpoint);
  });

  test("refuses a malformed server digest without exposing a speculative checkpoint", async () => {
    // Arrange
    const checkpoint = initialCheckpoint();

    // Act
    const result = await foldVerifiedOrderedDashboardEvent(checkpoint, missionEvent(1), "INVALID");

    // Assert
    expect(refusalOf(result)).toEqual({
      attribute: "digest",
      code: "SERVER_DIGEST_FORM",
      value: "INVALID",
    });
    expect(result.checkpoint).toBe(checkpoint);
  });

  test("refuses a server state digest mismatch and rolls back all state", async () => {
    // Arrange
    const checkpoint = initialCheckpoint();

    // Act
    const result = await foldVerifiedOrderedDashboardEvent(
      checkpoint,
      missionEvent(1),
      "f".repeat(64),
    );

    // Assert
    expect(refusalOf(result)).toEqual({
      attribute: "digest",
      code: "SERVER_DIGEST_MISMATCH",
      value: "f".repeat(64),
    });
    expect(result.checkpoint).toBe(checkpoint);
    expect(result.checkpoint.state.latestAuditOrdinal).toBe(0);
  });
});
