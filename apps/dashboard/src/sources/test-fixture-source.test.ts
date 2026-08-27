import { expect, test, vi } from "vitest";

import type { DashboardSourceInput } from "./event-source";
import {
  TEST_FIXTURE_EVENT_NAME,
  TestFixtureSource,
  type TestFixtureSourceRefusal,
} from "./test-fixture-source";

interface MutableHarness {
  appliedRevision: number;
  snapshotRequests: number;
  sourceDisposals: number;
  sourceRevision: number;
  sourceScript: unknown;
}

interface HarnessWindow {
  __AERIAL_RESCUE_DASHBOARD_TEST__?: MutableHarness;
}

function installHarness(sourceScript: unknown, sourceRevision = 1): MutableHarness {
  const harness: MutableHarness = {
    appliedRevision: 0,
    snapshotRequests: 0,
    sourceDisposals: 0,
    sourceRevision,
    sourceScript,
  };
  (window as unknown as HarnessWindow).__AERIAL_RESCUE_DASHBOARD_TEST__ = harness;
  return harness;
}

function script(inputs: readonly DashboardSourceInput[]): unknown {
  return { fixtureVersion: "dashboard-source-script/v1", inputs };
}

function dispatchRevision(
  revision: number,
  inputs: readonly DashboardSourceInput[],
  replace = false,
): void {
  window.dispatchEvent(
    new CustomEvent(TEST_FIXTURE_EVENT_NAME, {
      detail: { inputs, replace, revision },
    }),
  );
}

test("clears and consumes the initial serialized script before acknowledging its revision", async () => {
  // Arrange
  const initialInput = { channel: "sse-frame", name: "snapshot", raw: "{}" };
  const harness = installHarness(script([initialInput]));
  const received: DashboardSourceInput[] = [];
  const source = new TestFixtureSource(window);

  // Act
  source.open((input) => {
    received.push(input);
    return Promise.resolve();
  });
  await source.whenIdle();

  // Assert
  expect(harness.sourceScript).toBeNull();
  expect(received).toEqual([initialInput]);
  expect(harness.appliedRevision).toBe(1);
});

test("serializes appended revisions and acknowledges only after each consumer promise", async () => {
  // Arrange
  const harness = installHarness(script([]));
  const received: string[] = [];
  let releaseFirst: (() => void) | undefined;
  const firstBlocked = new Promise<void>((resolve) => {
    releaseFirst = resolve;
  });
  const source = new TestFixtureSource(window);
  source.open(async ({ name }) => {
    received.push(name);
    if (name === "first") {
      await firstBlocked;
    }
  });
  await source.whenIdle();

  // Act
  dispatchRevision(2, [{ channel: "sse-frame", name: "first", raw: "{}" }]);
  dispatchRevision(3, [{ channel: "sse-frame", name: "second", raw: "{}" }]);
  await Promise.resolve();
  const appliedWhileBlocked = harness.appliedRevision;
  releaseFirst?.();
  await source.whenIdle();

  // Assert
  expect(appliedWhileBlocked).toBe(1);
  expect(received).toEqual(["first", "second"]);
  expect(harness.appliedRevision).toBe(3);
});

test("invalidates an older revision and records one logical disposal on replacement", async () => {
  // Arrange
  const harness = installHarness(script([]));
  const received: string[] = [];
  let releaseStale: (() => void) | undefined;
  const staleBlocked = new Promise<void>((resolve) => {
    releaseStale = resolve;
  });
  const source = new TestFixtureSource(window);
  source.open(async ({ name }) => {
    received.push(name);
    if (name === "stale-first") {
      await staleBlocked;
    }
  });
  await source.whenIdle();

  // Act
  dispatchRevision(2, [
    { channel: "sse-frame", name: "stale-first", raw: "{}" },
    { channel: "sse-frame", name: "stale-second", raw: "{}" },
  ]);
  await Promise.resolve();
  dispatchRevision(3, [{ channel: "replay-bundle", name: "current", raw: "{}" }], true);
  releaseStale?.();
  await source.whenIdle();

  // Assert
  expect(received).toEqual(["stale-first", "current"]);
  expect(harness.appliedRevision).toBe(3);
  expect(harness.sourceDisposals).toBe(1);
});

test("records a fresh snapshot request in the isolated test harness", () => {
  // Arrange
  const harness = installHarness(script([]));
  const source = new TestFixtureSource(window);

  // Act
  source.recordSnapshotRequest();

  // Assert
  expect(harness.snapshotRequests).toBe(1);
});

test("disposes the fixture listener idempotently and ignores later revisions", async () => {
  // Arrange
  const harness = installHarness(script([]));
  const consumer = vi.fn(() => Promise.resolve());
  const source = new TestFixtureSource(window);
  const subscription = source.open(consumer);
  await source.whenIdle();

  // Act
  subscription.dispose();
  subscription.dispose();
  dispatchRevision(2, [{ channel: "sse-frame", name: "late", raw: "{}" }]);
  await source.whenIdle();

  // Assert
  expect(harness.sourceDisposals).toBe(1);
  expect(consumer).not.toHaveBeenCalled();
  expect(harness.appliedRevision).toBe(1);
});

test("refuses non-monotonic and malformed fixture revisions without acknowledging them", async () => {
  // Arrange
  const harness = installHarness(script([]), 2);
  const refusals: TestFixtureSourceRefusal[] = [];
  const source = new TestFixtureSource(window, (refusal) => {
    refusals.push(refusal);
  });
  source.open(() => Promise.resolve());
  await source.whenIdle();

  // Act
  dispatchRevision(2, []);
  window.dispatchEvent(
    new CustomEvent(TEST_FIXTURE_EVENT_NAME, {
      detail: { inputs: "not-an-array", replace: false, revision: 3 },
    }),
  );
  await source.whenIdle();

  // Assert
  expect(harness.appliedRevision).toBe(2);
  expect(refusals).toEqual([
    { code: "NON_MONOTONIC_REVISION" },
    { code: "INVALID_TEST_FIXTURE_BATCH" },
  ]);
});

test("clears an invalid initial script without exposing its candidate in a refusal", async () => {
  // Arrange
  const harness = installHarness({ bearer: "must-not-survive" });
  const refusals: TestFixtureSourceRefusal[] = [];
  const source = new TestFixtureSource(window, (refusal) => {
    refusals.push(refusal);
  });

  // Act
  source.open(() => Promise.resolve());
  await source.whenIdle();

  // Assert
  expect(harness.sourceScript).toBeNull();
  expect(refusals).toEqual([{ code: "INVALID_TEST_FIXTURE_SCRIPT" }]);
  expect(JSON.stringify(refusals)).not.toContain("must-not-survive");
});

test.each([
  null,
  { fixtureVersion: "wrong", inputs: [] },
  { fixtureVersion: "dashboard-source-script/v1", inputs: [null] },
  {
    fixtureVersion: "dashboard-source-script/v1",
    inputs: [{ channel: "unknown", name: "frame", raw: "{}" }],
  },
  {
    fixtureVersion: "dashboard-source-script/v1",
    inputs: [{ channel: "sse-frame", extra: true, name: "frame", raw: "{}" }],
  },
] as const)("refuses each structurally invalid initial source script", async (candidate) => {
  // Arrange
  installHarness(candidate);
  const refusals: TestFixtureSourceRefusal[] = [];
  const source = new TestFixtureSource(window, (refusal) => refusals.push(refusal));

  // Act
  source.open(() => Promise.resolve());
  await source.whenIdle();

  // Assert
  expect(refusals).toEqual([{ code: "INVALID_TEST_FIXTURE_SCRIPT" }]);
});

test("refuses an absent harness at open and snapshot boundaries", () => {
  // Arrange
  delete (window as unknown as HarnessWindow).__AERIAL_RESCUE_DASHBOARD_TEST__;
  const refusals: TestFixtureSourceRefusal[] = [];
  const source = new TestFixtureSource(window, (refusal) => refusals.push(refusal));

  // Act
  const subscription = source.open(() => Promise.resolve());
  source.recordSnapshotRequest();
  subscription.dispose();

  // Assert
  expect(refusals).toEqual([
    { code: "INVALID_TEST_HARNESS" },
    { code: "INVALID_TEST_HARNESS" },
    { code: "INVALID_TEST_HARNESS" },
  ]);
});

test("refuses non-custom and structurally invalid appended batches", async () => {
  // Arrange
  installHarness(script([]));
  const refusals: TestFixtureSourceRefusal[] = [];
  const source = new TestFixtureSource(window, (refusal) => refusals.push(refusal));
  source.open(() => Promise.resolve());
  await source.whenIdle();

  // Act
  window.dispatchEvent(new Event(TEST_FIXTURE_EVENT_NAME));
  for (const detail of [
    null,
    { inputs: [], replace: "no", revision: 2 },
    { inputs: [], replace: false, revision: 0 },
    { inputs: [{ channel: "sse-frame", name: 7, raw: "{}" }], replace: false, revision: 2 },
  ]) {
    window.dispatchEvent(new CustomEvent(TEST_FIXTURE_EVENT_NAME, { detail }));
  }
  await source.whenIdle();

  // Assert
  expect(refusals).toEqual(
    Array.from({ length: 5 }, () => ({ code: "INVALID_TEST_FIXTURE_BATCH" })),
  );
});

test("acknowledges a batch whose consumer disposes the source and refuses reopening", async () => {
  // Arrange
  const harness = installHarness(script([{ channel: "sse-frame", name: "overload", raw: "{}" }]));
  const source = new TestFixtureSource(window);

  // Act
  const subscription = source.open(() => {
    subscription.dispose();
    return Promise.resolve();
  });
  await source.whenIdle();
  let reopeningError: unknown;
  try {
    source.open(() => Promise.resolve());
  } catch (error: unknown) {
    reopeningError = error;
  }

  // Assert
  expect(harness.appliedRevision).toBe(1);
  expect(reopeningError).toEqual(new Error("a test fixture source can be opened only once"));
});

test("refuses acknowledgement and replacement counters after the harness disappears", async () => {
  // Arrange
  installHarness(script([]));
  const refusals: TestFixtureSourceRefusal[] = [];
  const source = new TestFixtureSource(window, (refusal) => refusals.push(refusal));
  source.open(() => {
    delete (window as unknown as HarnessWindow).__AERIAL_RESCUE_DASHBOARD_TEST__;
    return Promise.resolve();
  });
  await source.whenIdle();

  // Act
  dispatchRevision(2, [{ channel: "sse-frame", name: "late", raw: "{}" }], true);
  await source.whenIdle();

  // Assert
  expect(refusals).toContainEqual({ code: "INVALID_TEST_HARNESS" });
});
