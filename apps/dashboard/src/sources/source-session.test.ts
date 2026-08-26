import { expect, test, vi } from "vitest";

import type { DashboardReducedState, OrderedDashboardEvent } from "../contracts/generated";
import { replayStateDigest } from "../domain/canonical";
import { foldOrderedDashboardEvent, type ReducerCheckpoint } from "../domain/reducer";
import { missionEvent, preparedState } from "../../tests/unit-support/reducer-fixtures";
import type {
  DashboardEventSource,
  DashboardSourceConsumer,
  DashboardSourceInput,
  DashboardSourceSubscription,
} from "./event-source";
import { DashboardSourceSession } from "./source-session";

class ManualSource implements DashboardEventSource {
  disposeCount = 0;
  private consumer: DashboardSourceConsumer | null = null;

  open(consumer: DashboardSourceConsumer): DashboardSourceSubscription {
    this.consumer = consumer;
    return {
      dispose: () => {
        this.disposeCount += 1;
      },
    };
  }

  emit(input: DashboardSourceInput): Promise<void> {
    if (this.consumer === null) {
      throw new Error("manual source is not open");
    }
    return this.consumer(input);
  }
}

function input(channel: string, name: string, value: unknown): DashboardSourceInput {
  return { channel, name, raw: JSON.stringify(value) };
}

async function snapshotInput(
  state: DashboardReducedState = preparedState(),
  latestEventDigest: string | null = null,
): Promise<DashboardSourceInput> {
  return input("sse-frame", "snapshot", {
    currentRun: {
      missionId: state.currentMission?.identifier ?? "mission-synthetic-0001",
      mode: "degradedLive",
      runId: "run-synthetic-0001",
    },
    cursor: "opaque-snapshot-cursor",
    digest: await replayStateDigest(state),
    latestEventDigest,
    runtimeId: "runtime-synthetic-0001",
    snapshotVersion: "dashboard-snapshot/v1",
    state,
    timeline: [],
  });
}

async function eventFrameInput(
  checkpoint: ReducerCheckpoint,
  orderedEvent: OrderedDashboardEvent,
  digestOverride?: string,
): Promise<DashboardSourceInput> {
  const folded = await foldOrderedDashboardEvent(checkpoint, orderedEvent);
  if (!folded.ok) {
    throw new Error(`test event refused: ${folded.failure.code}`);
  }
  return input("sse-frame", "dashboard-event", {
    cursor: `opaque-event-cursor-${String(orderedEvent.auditOrdinal)}`,
    digest: digestOverride ?? (await replayStateDigest(folded.checkpoint.state)),
    event: orderedEvent,
    frameVersion: "ordered-dashboard-event-frame/v1",
  });
}

function sourceSignal(
  signal: "connecting" | "disconnected" | "offline" | "recovered",
): DashboardSourceInput {
  return input("source-signal", signal, {
    signal,
    signalVersion: "dashboard-source-signal/v1",
  });
}

test("validates a snapshot before replacing mission and server state", async () => {
  // Arrange
  const source = new ManualSource();
  const onState = vi.fn();
  const session = new DashboardSourceSession({ onState });
  const snapshot = await snapshotInput();
  session.replaceSource(source);

  // Act
  await source.emit(snapshot);

  // Assert
  expect(session.state.mission.checkpoint.state).toEqual(preparedState());
  expect(session.state.mission.timeline).toEqual([]);
  expect(session.state.server).toMatchObject({
    refusal: null,
    runtimeId: "runtime-synthetic-0001",
    status: "connected",
  });
  expect(session.state.server).not.toHaveProperty("cursor");
  expect(onState).toHaveBeenCalledTimes(1);
});

test("accepts a replay resume only for the validated current replay session", async () => {
  // Arrange
  const source = new ManualSource();
  const session = new DashboardSourceSession();
  const liveSnapshot = await snapshotInput();
  const replaySnapshot = JSON.parse(liveSnapshot.raw) as Record<string, unknown>;
  replaySnapshot["currentRun"] = {
    mode: "replay",
    sessionId: "session-production-resume",
  };
  session.replaceSource(source, "degradedLive");
  await source.emit({ ...liveSnapshot, raw: JSON.stringify(replaySnapshot) });
  const mismatchedState = session.state;

  // Act
  const wrongSessionAccepted = session.acceptReplayResume("session-different");
  const matchingSessionAccepted = session.acceptReplayResume("session-production-resume");

  // Assert
  expect(wrongSessionAccepted).toBe(false);
  expect(mismatchedState.server.status).toBe("modeMismatch");
  expect(matchingSessionAccepted).toBe(true);
  expect(session.state.server).toMatchObject({
    currentRun: { mode: "replay", sessionId: "session-production-resume" },
    refusal: null,
    status: "connected",
  });
});

test("refuses a snapshot whose native SSE identifier differs from its body cursor", async () => {
  // Arrange
  const source = new ManualSource();
  const session = new DashboardSourceSession();
  session.replaceSource(source);
  await source.emit(await snapshotInput());
  const missionBefore = session.state.mission;
  const candidate = await snapshotInput(
    preparedState({
      currentMission: {
        identifier: "mission-successor-0001",
        lifecycle: "PLANNED",
        predecessorIdentifier: "mission-synthetic-0001",
      },
    }),
  );

  // Act
  await source.emit({ ...candidate, lastEventId: "different-native-cursor" });

  // Assert
  expect(session.state.mission).toBe(missionBefore);
  expect(session.state.server.refusal).toEqual({
    code: "CURSOR_WITNESS_MISMATCH",
    inputName: "snapshot",
  });
});

test("refuses a live snapshot whose current run names a different mission", async () => {
  // Arrange
  const source = new ManualSource();
  const session = new DashboardSourceSession();
  session.replaceSource(source);
  await source.emit(await snapshotInput());
  const missionBefore = session.state.mission;
  const candidate = await snapshotInput();
  const document = JSON.parse(candidate.raw) as {
    currentRun: { missionId: string };
  };
  document.currentRun.missionId = "mission-crossed-run-0001";

  // Act
  await source.emit({ ...candidate, raw: JSON.stringify(document) });

  // Assert
  expect(session.state.mission).toBe(missionBefore);
  expect(session.state.server.refusal).toEqual({
    code: "RUN_IDENTITY_MISMATCH",
    inputName: "snapshot",
  });
});

test("refuses a snapshot that does not confirm the accepted live run", async () => {
  // Arrange
  const source = new ManualSource();
  const session = new DashboardSourceSession();
  session.replaceSource(source);
  await source.emit(await snapshotInput());
  const missionBefore = session.state.mission;
  session.expectLiveRun("mission-synthetic-0001", "run-accepted-0001");

  // Act
  await source.emit(await snapshotInput());

  // Assert
  expect(session.state.mission).toBe(missionBefore);
  expect(session.state.server.refusal).toEqual({
    code: "RUN_IDENTITY_MISMATCH",
    inputName: "snapshot",
  });
});

test("locks a changed runtime snapshot while retaining the last mission and timeline", async () => {
  // Arrange
  const source = new ManualSource();
  const session = new DashboardSourceSession();
  session.replaceSource(source);
  await source.emit(await snapshotInput());
  const checkpoint = session.state.mission.checkpoint;
  await source.emit(await eventFrameInput(checkpoint, missionEvent(1, "SEARCHING")));
  const missionBefore = session.state.mission;
  const changedSnapshot = await snapshotInput(
    preparedState({
      currentMission: {
        identifier: "mission-from-another-runtime",
        lifecycle: "PLANNED",
        predecessorIdentifier: null,
      },
    }),
  );
  const changedDocument = JSON.parse(changedSnapshot.raw) as Record<string, unknown>;
  changedDocument["runtimeId"] = "runtime-synthetic-0002";

  // Act
  await source.emit({ ...changedSnapshot, raw: JSON.stringify(changedDocument) });

  // Assert
  expect(session.state.mission).toBe(missionBefore);
  expect(session.state.server.runtimeId).toBe("runtime-synthetic-0001");
  expect(session.state.server.status).toBe("runtimeChanged");
  expect(source.disposeCount).toBe(1);
});

test("locks a first snapshot that differs from the validated bootstrap runtime anchor", async () => {
  // Arrange
  const source = new ManualSource();
  const session = new DashboardSourceSession();
  session.anchorRuntime("runtime-bootstrap-0001");
  session.replaceSource(source);
  const snapshot = await snapshotInput();
  const document = JSON.parse(snapshot.raw) as Record<string, unknown>;
  document["runtimeId"] = "runtime-restarted-0002";

  // Act
  await source.emit({ ...snapshot, raw: JSON.stringify(document) });

  // Assert
  expect(session.state.mission.checkpoint.state.currentMission).toBeNull();
  expect(session.state.server.runtimeId).toBe("runtime-bootstrap-0001");
  expect(session.state.server.status).toBe("runtimeChanged");
  expect(source.disposeCount).toBe(1);
});

test("folds a validated named event frame and appends its meaningful timeline event", async () => {
  // Arrange
  const source = new ManualSource();
  const session = new DashboardSourceSession();
  const snapshot = await snapshotInput();
  const orderedEvent = missionEvent(1, "SEARCHING");
  const frame = await eventFrameInput(
    { latestEventDigest: null, state: preparedState() },
    orderedEvent,
  );
  session.replaceSource(source);
  await source.emit(snapshot);

  // Act
  await source.emit(frame);

  // Assert
  expect(session.state.mission.checkpoint.state.currentMission?.lifecycle).toBe("SEARCHING");
  expect(session.state.mission.checkpoint.state.latestAuditOrdinal).toBe(1);
  expect(session.state.mission.timeline).toEqual([orderedEvent]);
  expect(session.state.server).not.toHaveProperty("cursor");
  expect(session.state.server.refusal).toBeNull();
});

test("refuses an event whose native SSE identifier differs from its body cursor", async () => {
  // Arrange
  const source = new ManualSource();
  const session = new DashboardSourceSession();
  session.replaceSource(source);
  await source.emit(await snapshotInput());
  const missionBefore = session.state.mission;
  const frame = await eventFrameInput(missionBefore.checkpoint, missionEvent(1, "SEARCHING"));

  // Act
  await source.emit({ ...frame, lastEventId: "different-native-cursor" });

  // Assert
  expect(session.state.mission).toBe(missionBefore);
  expect(session.state.server.refusal).toEqual({
    code: "CURSOR_WITNESS_MISMATCH",
    inputName: "dashboard-event",
  });
});

test("retains the last validated checkpoint and timeline after malformed input", async () => {
  // Arrange
  const source = new ManualSource();
  const session = new DashboardSourceSession();
  session.replaceSource(source);
  await source.emit(await snapshotInput());
  const checkpointBefore = session.state.mission.checkpoint;
  const timelineBefore = session.state.mission.timeline;
  const malformed: DashboardSourceInput = {
    channel: "sse-frame",
    name: "dashboard-event",
    raw: "{",
  };

  // Act
  await source.emit(malformed);

  // Assert
  expect(session.state.mission.checkpoint).toBe(checkpointBefore);
  expect(session.state.mission.timeline).toBe(timelineBefore);
  expect(session.state.server.refusal).toEqual({
    code: "MALFORMED_JSON",
    inputName: "dashboard-event",
  });
});

test("retains the last validated mission state after a server digest mismatch", async () => {
  // Arrange
  const source = new ManualSource();
  const session = new DashboardSourceSession();
  const checkpoint = { latestEventDigest: null, state: preparedState() };
  const divergent = await eventFrameInput(checkpoint, missionEvent(1), "0".repeat(64));
  session.replaceSource(source);
  await source.emit(await snapshotInput());
  const checkpointBefore = session.state.mission.checkpoint;

  // Act
  await source.emit(divergent);

  // Assert
  expect(session.state.mission.checkpoint).toBe(checkpointBefore);
  expect(session.state.server.refusal).toEqual({
    code: "REDUCER_REFUSED",
    inputName: "dashboard-event",
    reducerCode: "SERVER_DIGEST_MISMATCH",
  });
});

test("updates transport state without changing mission state", async () => {
  // Arrange
  const source = new ManualSource();
  const session = new DashboardSourceSession();
  session.replaceSource(source);
  await source.emit(await snapshotInput());
  const checkpointBefore = session.state.mission.checkpoint;

  // Act
  await source.emit(sourceSignal("disconnected"));

  // Assert
  expect(session.state.server.status).toBe("disconnected");
  expect(session.state.mission.checkpoint).toBe(checkpointBefore);
});

test("disposes the current source and requests one resnapshot after overload", async () => {
  // Arrange
  const source = new ManualSource();
  const requestSnapshot = vi.fn();
  const session = new DashboardSourceSession({ requestSnapshot });
  session.replaceSource(source);
  await source.emit(await snapshotInput());
  const checkpointBefore = session.state.mission.checkpoint;
  const overload = input("sse-frame", "stream-overloaded", {
    controlVersion: "dashboard-stream-overloaded/v1",
    reason: "NON_DROPPABLE_BUFFER_FULL",
  });

  // Act
  const first = source.emit(overload);
  const duplicate = source.emit(overload);
  await Promise.all([first, duplicate]);
  await session.whenIdle();

  // Assert
  expect(source.disposeCount).toBe(1);
  expect(requestSnapshot).toHaveBeenCalledTimes(1);
  expect(session.state.server.status).toBe("resynchronizing");
  expect(session.state.mission.checkpoint).toBe(checkpointBefore);
});

test("ignores callbacks queued by a source that has been replaced", async () => {
  // Arrange
  const staleSource = new ManualSource();
  const currentSource = new ManualSource();
  const session = new DashboardSourceSession();
  const staleSnapshot = await snapshotInput();
  const currentState = preparedState({
    currentMission: {
      identifier: "mission-current-0001",
      lifecycle: "PLANNED",
      predecessorIdentifier: "mission-synthetic-0001",
    },
  });
  const currentSnapshot = await snapshotInput(currentState);
  session.replaceSource(staleSource);

  // Act
  const staleDelivery = staleSource.emit(staleSnapshot);
  session.replaceSource(currentSource);
  const currentDelivery = currentSource.emit(currentSnapshot);
  await Promise.all([staleDelivery, currentDelivery]);

  // Assert
  expect(staleSource.disposeCount).toBe(1);
  expect(session.state.mission.checkpoint.state.currentMission?.identifier).toBe(
    "mission-current-0001",
  );
  expect(session.state.server).not.toHaveProperty("cursor");
});

test("disposes idempotently and refuses later source callbacks", async () => {
  // Arrange
  const source = new ManualSource();
  const session = new DashboardSourceSession();
  session.replaceSource(source);
  await source.emit(await snapshotInput());
  const checkpointBefore = session.state.mission.checkpoint;

  // Act
  session.dispose();
  session.dispose();
  await source.emit(await eventFrameInput(checkpointBefore, missionEvent(1)));

  // Assert
  expect(source.disposeCount).toBe(1);
  expect(session.state.server.status).toBe("disposed");
  expect(session.state.mission.checkpoint).toBe(checkpointBefore);
});

test("validates source signal names and rejects unsupported channels without retaining raw data", async () => {
  // Arrange
  const source = new ManualSource();
  const session = new DashboardSourceSession();
  session.replaceSource(source);
  const mismatchedSignal = input("source-signal", "offline", {
    signal: "recovered",
    signalVersion: "dashboard-source-signal/v1",
  });
  const unsupported: DashboardSourceInput = {
    channel: "credential-channel",
    name: "secret-name",
    raw: "sensitive-raw-value",
  };

  // Act
  await source.emit(mismatchedSignal);
  await source.emit(unsupported);

  // Assert
  expect(session.state.server.refusal).toEqual({
    code: "UNSUPPORTED_CHANNEL",
    inputName: "secret-name",
  });
  expect(JSON.stringify(session.state)).not.toContain("sensitive-raw-value");
});

test("delegates non-stream fixture inputs to an injected validated boundary owner", async () => {
  // Arrange
  const source = new ManualSource();
  const consumeUnhandledInput = vi.fn<(input: DashboardSourceInput) => Promise<void>>(() =>
    Promise.resolve(),
  );
  const session = new DashboardSourceSession({ consumeUnhandledInput });
  const bootstrapInput = input("bootstrap", "bootstrap", {
    bearer: "memory-only",
    bootstrapVersion: "dashboard-bootstrap/v1",
    runtimeId: "runtime-synthetic-0001",
  });
  session.replaceSource(source);

  // Act
  await source.emit(bootstrapInput);

  // Assert
  expect(consumeUnhandledInput).toHaveBeenCalledWith(bootstrapInput);
  expect(session.state.server.refusal).toBeNull();
});

test("reports unknown names and schema-invalid source documents without changing mission state", async () => {
  // Arrange
  const source = new ManualSource();
  const refusals: unknown[] = [];
  const session = new DashboardSourceSession({
    onState: ({ server }) => {
      if (server.refusal !== null) {
        refusals.push(server.refusal);
      }
    },
  });
  session.replaceSource(source);
  const checkpointBefore = session.state.mission.checkpoint;

  // Act
  await source.emit({ channel: "sse-frame", name: "unknown-frame", raw: "{}" });
  await source.emit({ channel: "sse-frame", name: "snapshot", raw: "{}" });
  await source.emit({ channel: "source-signal", name: "offline", raw: "{}" });
  await source.emit({ channel: "sse-frame", name: "stream-overloaded", raw: "{}" });

  // Assert
  expect(refusals).toEqual([
    { code: "UNKNOWN_FRAME", inputName: "unknown-frame" },
    {
      code: "SCHEMA_VALIDATION_FAILED",
      inputName: "snapshot",
      schemaId: "https://aerial-rescue.invalid/schemas/v1/dashboard/dashboard-snapshot.schema.json",
    },
    {
      code: "SCHEMA_VALIDATION_FAILED",
      inputName: "offline",
      schemaId: "https://aerial-rescue.invalid/schemas/v1/dashboard/source-signal.schema.json",
    },
    {
      code: "SCHEMA_VALIDATION_FAILED",
      inputName: "stream-overloaded",
      schemaId: "https://aerial-rescue.invalid/schemas/v1/dashboard/stream-overloaded.schema.json",
    },
  ]);
  expect(session.state.mission.checkpoint).toBe(checkpointBefore);
});

test("refuses a schema-valid snapshot whose server digest does not match", async () => {
  // Arrange
  const source = new ManualSource();
  const session = new DashboardSourceSession();
  const validSnapshot = await snapshotInput();
  const candidate = JSON.parse(validSnapshot.raw) as Record<string, unknown>;
  candidate["digest"] = "0".repeat(64);
  session.replaceSource(source);

  // Act
  await source.emit({ ...validSnapshot, raw: JSON.stringify(candidate) });

  // Assert
  expect(session.state.mission.checkpoint.state.currentMission).toBeNull();
  expect(session.state.server.refusal).toEqual({
    code: "REDUCER_REFUSED",
    inputName: "snapshot",
    reducerCode: "SERVER_DIGEST_MISMATCH",
  });
});

test("records a typed refusal when the overload resnapshot request fails", async () => {
  // Arrange
  const source = new ManualSource();
  const session = new DashboardSourceSession({
    requestSnapshot: () => {
      throw new Error("synthetic request failure");
    },
  });
  const overload = input("sse-frame", "stream-overloaded", {
    controlVersion: "dashboard-stream-overloaded/v1",
    reason: "NON_DROPPABLE_BUFFER_FULL",
  });
  session.replaceSource(source);

  // Act
  await source.emit(overload);

  // Assert
  expect(source.disposeCount).toBe(1);
  expect(session.state.server.refusal).toEqual({
    code: "RESNAPSHOT_FAILED",
    inputName: "stream-overloaded",
  });
  expect(session.state.server.status).toBe("resynchronizing");
});

test("converts an unexpected source-processing exception into a redacted refusal", async () => {
  // Arrange
  const source = new ManualSource();
  let shouldThrow = true;
  const session = new DashboardSourceSession({
    onState: () => {
      if (shouldThrow) {
        shouldThrow = false;
        throw new Error("synthetic render callback failure");
      }
    },
  });
  session.replaceSource(source);

  // Act
  await source.emit(sourceSignal("connecting"));

  // Assert
  expect(session.state.server.refusal).toEqual({
    code: "PROCESSING_FAILED",
    inputName: "connecting",
  });
  expect(JSON.stringify(session.state)).not.toContain("synthetic render callback failure");
});

test("closes a subscription returned after its source synchronously invalidates the session", () => {
  // Arrange
  const session = new DashboardSourceSession();
  const lateSubscription = { dispose: vi.fn() };
  const invalidatingSource: DashboardEventSource = {
    open: () => {
      session.dispose();
      return lateSubscription;
    },
  };

  // Act
  session.replaceSource(invalidatingSource);

  // Assert
  expect(lateSubscription.dispose).toHaveBeenCalledTimes(1);
  expect(session.state.server.status).toBe("disposed");
});

test("cannot reopen a disposed source session", () => {
  // Arrange
  const session = new DashboardSourceSession();
  const source = new ManualSource();
  session.dispose();
  let observed: unknown;

  // Act
  try {
    session.replaceSource(source);
  } catch (error: unknown) {
    observed = error;
  }

  // Assert
  expect(observed).toEqual(new Error("a disposed dashboard source session cannot be reopened"));
  expect(source.disposeCount).toBe(0);
});
