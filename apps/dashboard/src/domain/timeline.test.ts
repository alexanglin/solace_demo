import { describe, expect, test } from "vitest";

import { appendMeaningfulTimelineEvent, replaceTimelineFromSnapshot } from "./timeline";
import {
  connectivityEvent,
  missionEvent,
  sectorEvent,
  telemetryEvent,
} from "../../tests/unit-support/reducer-fixtures";

describe("snapshot timeline replacement", () => {
  test("replaces, copies, orders, and excludes telemetry from the snapshot timeline", () => {
    // Arrange
    const snapshotTimeline = [missionEvent(4), telemetryEvent(2), connectivityEvent(3)];

    // Act
    const timeline = replaceTimelineFromSnapshot(snapshotTimeline);
    snapshotTimeline.pop();

    // Assert
    expect(timeline.map(({ auditOrdinal }) => auditOrdinal)).toEqual([3, 4]);
    expect(timeline.every(({ event }) => event.eventClass !== "TELEMETRY")).toBe(true);
  });

  test("retains only the first entry for a repeated snapshot ordinal", () => {
    // Arrange
    const first = connectivityEvent(3);
    const repeated = sectorEvent(3);

    // Act
    const timeline = replaceTimelineFromSnapshot([first, repeated]);

    // Assert
    expect(timeline).toEqual([first]);
  });
});

describe("meaningful suffix append", () => {
  test("appends a non-telemetry event in audit order without mutating prior state", () => {
    // Arrange
    const prior = [missionEvent(1), sectorEvent(3)];

    // Act
    const timeline = appendMeaningfulTimelineEvent(prior, connectivityEvent(2));

    // Assert
    expect(timeline.map(({ auditOrdinal }) => auditOrdinal)).toEqual([1, 2, 3]);
    expect(timeline).not.toBe(prior);
    expect(prior.map(({ auditOrdinal }) => auditOrdinal)).toEqual([1, 3]);
  });

  test("keeps the same timeline for telemetry and repeated ordinals", () => {
    // Arrange
    const prior = [missionEvent(1), sectorEvent(2)];

    // Act
    const outcomes = [
      appendMeaningfulTimelineEvent(prior, telemetryEvent(3)),
      appendMeaningfulTimelineEvent(prior, connectivityEvent(2)),
    ];

    // Assert
    expect(outcomes).toEqual([prior, prior]);
    expect(outcomes[0]).toBe(prior);
    expect(outcomes[1]).toBe(prior);
  });
});
