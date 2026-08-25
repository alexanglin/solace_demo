import { expect, test } from "vitest";

import type { OrderedDashboardEvent } from "../contracts/generated";
import { replayStateDigest } from "./canonical";
import { foldOrderedDashboardEvent, foldVerifiedOrderedDashboardEvent } from "./reducer";
import { appendMeaningfulTimelineEvent, replaceTimelineFromSnapshot } from "./timeline";
import {
  connectivityEvent,
  initialCheckpoint,
  missionEvent,
  sectorEvent,
  telemetryEvent,
} from "../../tests/unit-support/reducer-fixtures";

test("folds validated suffix state while timeline composition excludes telemetry", async () => {
  // Arrange
  const checkpoint = initialCheckpoint();
  const snapshotTimeline: OrderedDashboardEvent[] = [];
  const suffix = [
    missionEvent(1, "SEARCHING"),
    telemetryEvent(2),
    connectivityEvent(3),
    sectorEvent(4),
  ];

  // Act
  let current = checkpoint;
  let timeline = replaceTimelineFromSnapshot(snapshotTimeline);
  for (const event of suffix) {
    const speculative = await foldOrderedDashboardEvent(current, event);
    if (!speculative.ok) {
      throw new Error(speculative.failure.code);
    }
    const serverDigest = await replayStateDigest(speculative.checkpoint.state);
    const verified = await foldVerifiedOrderedDashboardEvent(current, event, serverDigest);
    if (!verified.ok) {
      throw new Error(verified.failure.code);
    }
    current = verified.checkpoint;
    if (verified.disposition === "APPLIED") {
      timeline = appendMeaningfulTimelineEvent(timeline, event);
    }
  }

  // Assert
  expect(current.state.latestAuditOrdinal).toBe(4);
  expect(current.state.currentMission?.lifecycle).toBe("SEARCHING");
  expect(current.state.sectors[0]?.state).toBe("ASSIGNED");
  expect(timeline.map(({ auditOrdinal }) => auditOrdinal)).toEqual([1, 3, 4]);
  expect(timeline.some(({ event }) => event.eventClass === "TELEMETRY")).toBe(false);
});

test("appends timeline suffix only after verified applied success", async () => {
  // Arrange
  const checkpoint = initialCheckpoint();
  const appliedEvent = missionEvent(1, "SEARCHING");
  const speculative = await foldOrderedDashboardEvent(checkpoint, appliedEvent);
  if (!speculative.ok) {
    throw new Error(speculative.failure.code);
  }
  const serverDigest = await replayStateDigest(speculative.checkpoint.state);

  // Act
  const applied = await foldVerifiedOrderedDashboardEvent(checkpoint, appliedEvent, serverDigest);
  let timeline = replaceTimelineFromSnapshot([]);
  if (applied.ok && applied.disposition === "APPLIED") {
    timeline = appendMeaningfulTimelineEvent(timeline, appliedEvent);
  }
  const duplicate = await foldVerifiedOrderedDashboardEvent(
    applied.ok ? applied.checkpoint : checkpoint,
    appliedEvent,
    serverDigest,
  );
  if (duplicate.ok && duplicate.disposition === "APPLIED") {
    timeline = appendMeaningfulTimelineEvent(timeline, appliedEvent);
  }
  const refusedEvent = sectorEvent(3);
  const refused = await foldVerifiedOrderedDashboardEvent(
    applied.ok ? applied.checkpoint : checkpoint,
    refusedEvent,
    serverDigest,
  );
  if (refused.ok && refused.disposition === "APPLIED") {
    timeline = appendMeaningfulTimelineEvent(timeline, refusedEvent);
  }

  // Assert
  expect(applied).toMatchObject({ disposition: "APPLIED", ok: true });
  expect(duplicate).toMatchObject({ disposition: "DUPLICATE", ok: true });
  expect(refused).toMatchObject({ failure: { code: "ORDINAL_GAP" }, ok: false });
  expect(timeline).toEqual([appliedEvent]);
});
