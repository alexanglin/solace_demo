import { expect, test } from "vitest";

import { replayStateDigest } from "../domain/canonical";
import { foldOrderedDashboardEvent } from "../domain/reducer";
import { missionEvent, preparedState } from "../../tests/unit-support/reducer-fixtures";
import { LiveSseSource, type LiveEventStream } from "./live-sse-source";
import { DashboardSourceSession } from "./source-session";

class IntegrationEventStream implements LiveEventStream {
  closeCount = 0;
  private readonly listeners = new Map<string, Set<(event: Event) => void>>();

  addEventListener(name: string, listener: (event: Event) => void): void {
    const held = this.listeners.get(name) ?? new Set();
    held.add(listener);
    this.listeners.set(name, held);
  }

  removeEventListener(name: string, listener: (event: Event) => void): void {
    this.listeners.get(name)?.delete(listener);
  }

  close(): void {
    this.closeCount += 1;
  }

  dispatch(name: string, raw: string, lastEventId: string): void {
    for (const listener of this.listeners.get(name) ?? []) {
      listener(new MessageEvent(name, { data: raw, lastEventId }));
    }
  }
}

test("integrates named live frames through validation, reduction, and timeline composition", async () => {
  // Arrange
  const stream = new IntegrationEventStream();
  const source = new LiveSseSource({ factory: () => stream, url: "/api/v1/events" });
  const session = new DashboardSourceSession();
  const initialState = preparedState();
  const orderedEvent = missionEvent(1, "SEARCHING");
  const folded = await foldOrderedDashboardEvent(
    { latestEventDigest: null, state: initialState },
    orderedEvent,
  );
  if (!folded.ok) {
    throw new Error(`integration fixture refused: ${folded.failure.code}`);
  }
  const snapshot = JSON.stringify({
    currentRun: {
      missionId: "mission-synthetic-0001",
      mode: "degradedLive",
      runId: "run-synthetic-0001",
    },
    cursor: "snapshot-cursor",
    digest: await replayStateDigest(initialState),
    latestEventDigest: null,
    runtimeId: "runtime-synthetic-0001",
    snapshotVersion: "dashboard-snapshot/v1",
    state: initialState,
    timeline: [],
  });
  const eventFrame = JSON.stringify({
    cursor: "event-cursor",
    digest: await replayStateDigest(folded.checkpoint.state),
    event: orderedEvent,
    frameVersion: "ordered-dashboard-event-frame/v1",
  });
  session.replaceSource(source);

  // Act
  stream.dispatch("snapshot", snapshot, "snapshot-cursor");
  stream.dispatch("dashboard-event", eventFrame, "event-cursor");
  await session.whenIdle();

  // Assert
  expect(session.state.server).toMatchObject({ status: "connected" });
  expect(session.state.server).not.toHaveProperty("cursor");
  expect(session.state.mission.checkpoint).toEqual(folded.checkpoint);
  expect(session.state.mission.timeline).toEqual([orderedEvent]);
});
