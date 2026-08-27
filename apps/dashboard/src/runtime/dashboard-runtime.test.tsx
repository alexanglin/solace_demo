import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import type {
  DashboardBootstrap,
  DashboardReducedState,
  DashboardSnapshot,
} from "../contracts/generated";
import { replayStateDigest } from "../domain/canonical";
import type { ProposalDecisionSubmitter } from "../operator/mutation-client";
import { DashboardRuntime } from "./dashboard-runtime";
import type { DashboardEventSourcePort } from "./live-source";

class FakeEventSource implements DashboardEventSourcePort {
  readonly listeners = new Map<string, ((event: { readonly data?: unknown }) => void)[]>();
  closed = false;

  addEventListener(type: string, listener: (event: { readonly data?: unknown }) => void): void {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }

  close(): void {
    this.closed = true;
  }

  emit(type: string, data: unknown): void {
    for (const listener of this.listeners.get(type) ?? []) {
      listener({ data });
    }
  }
}

const bootstrap: DashboardBootstrap = {
  bootstrapVersion: "dashboard-bootstrap/v1",
  bearer: "runtime-bearer-synthetic-0001",
  runtimeId: "runtime-synthetic-0001",
};

afterEach(() => {
  cleanup();
});

async function sourceSnapshot(mode: "degradedLive" | "replay"): Promise<DashboardSnapshot> {
  const state: DashboardReducedState & { latestAuditOrdinal: 0 } = {
    canonicalizationVersion: 1,
    stateVersion: 1,
    currentMission: {
      identifier: "mission-synthetic-0001",
      lifecycle: "SEARCHING",
      predecessorIdentifier: null,
    },
    fleet: [],
    latestAuditOrdinal: 0,
    sectors: [],
  };
  return {
    snapshotVersion: "dashboard-snapshot/v1",
    runtimeId: bootstrap.runtimeId,
    cursor: "cursor-synthetic-0001",
    digest: await replayStateDigest(state),
    latestEventDigest: null,
    currentRun:
      mode === "replay"
        ? { mode: "replay", sessionId: "session-synthetic-0001" }
        : {
            mode: "degradedLive",
            missionId: "mission-synthetic-0001",
            runId: "run-synthetic-0001",
          },
    state,
    timeline: [],
  };
}

test("constructs a mutation writer only after a validated live snapshot", async () => {
  // Arrange
  const stream = new FakeEventSource();
  const submit = vi.fn<ProposalDecisionSubmitter>();
  const createSubmitter = vi.fn(() => submit);
  const liveSnapshot = await sourceSnapshot("degradedLive");
  const rendered = render(
    <DashboardRuntime
      bootstrap={bootstrap}
      createSubmitter={createSubmitter}
      isOnline={() => true}
      openEventSource={() => stream}
    />,
  );

  // Act
  const callsBeforeSnapshot = createSubmitter.mock.calls.length;
  await act(async () => {
    stream.emit("snapshot", JSON.stringify(liveSnapshot));
    await Promise.resolve();
  });
  const mission = await screen.findByRole("heading", { name: "mission-synthetic-0001" });
  rendered.unmount();

  // Assert
  expect(callsBeforeSnapshot).toBe(0);
  expect(mission).toBeTruthy();
  expect(createSubmitter).toHaveBeenCalledOnce();
  expect(createSubmitter).toHaveBeenCalledWith("runtime-bearer-synthetic-0001");
  expect(stream.closed).toBe(true);
});

test("renders replay facts without constructing a mutation writer", async () => {
  // Arrange
  const stream = new FakeEventSource();
  const createSubmitter = vi.fn(() => vi.fn<ProposalDecisionSubmitter>());
  const replaySnapshot = await sourceSnapshot("replay");
  render(
    <DashboardRuntime
      bootstrap={bootstrap}
      createSubmitter={createSubmitter}
      isOnline={() => true}
      openEventSource={() => stream}
    />,
  );

  // Act
  await act(async () => {
    stream.emit("snapshot", JSON.stringify(replaySnapshot));
    await Promise.resolve();
  });
  await waitFor(() => {
    const status = screen.getByRole("status", { name: "Operating mode" });
    if (status.textContent !== "ISOLATED REPLAY · READ ONLY") {
      throw new Error("replay snapshot has not been rendered");
    }
  });
  const mode = screen.getByRole("status", { name: "Operating mode" });

  // Assert
  expect(mode.textContent).toBe("ISOLATED REPLAY · READ ONLY");
  expect(createSubmitter).not.toHaveBeenCalled();
  expect(screen.queryByRole("button", { name: /proposal/iu })).toBeNull();
});
