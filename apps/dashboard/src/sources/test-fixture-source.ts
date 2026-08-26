import type {
  DashboardEventSource,
  DashboardSourceConsumer,
  DashboardSourceInput,
  DashboardSourceSubscription,
} from "./event-source";

export const TEST_FIXTURE_EVENT_NAME = "aerial-rescue-dashboard:test-source-inputs";
const TEST_HARNESS_PROPERTY = "__AERIAL_RESCUE_DASHBOARD_TEST__";
const TEST_FIXTURE_CHANNELS = new Set([
  "bootstrap",
  "http-response",
  "mutation-result",
  "replay-bundle",
  "source-signal",
  "sse-frame",
]);

interface TestFixtureHarness {
  appliedRevision: number;
  snapshotRequests: number;
  sourceDisposals: number;
  sourceRevision: number;
  sourceScript: unknown;
}

interface TestFixtureScript {
  readonly fixtureVersion: "dashboard-source-script/v1";
  readonly inputs: readonly DashboardSourceInput[];
}

interface TestFixtureBatch {
  readonly inputs: readonly DashboardSourceInput[];
  readonly replace: boolean;
  readonly revision: number;
}

type TestFixtureWindow = Window & Record<typeof TEST_HARNESS_PROPERTY, unknown>;

export interface TestFixtureSourceRefusal {
  readonly code:
    | "INVALID_TEST_HARNESS"
    | "INVALID_TEST_FIXTURE_SCRIPT"
    | "INVALID_TEST_FIXTURE_BATCH"
    | "NON_MONOTONIC_REVISION";
}

export type TestFixtureRefusalConsumer = (refusal: TestFixtureSourceRefusal) => void;

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function hasExactMembers(value: Record<string, unknown>, expected: readonly string[]): boolean {
  const keys = Object.keys(value).sort();
  const orderedExpected = [...expected].sort();
  return (
    keys.length === orderedExpected.length &&
    keys.every((key, index) => key === orderedExpected[index])
  );
}

function counter(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}

function harnessFrom(host: Window): TestFixtureHarness | null {
  const candidate = record((host as TestFixtureWindow)[TEST_HARNESS_PROPERTY]);
  if (
    candidate === null ||
    !hasExactMembers(candidate, [
      "appliedRevision",
      "snapshotRequests",
      "sourceDisposals",
      "sourceRevision",
      "sourceScript",
    ]) ||
    !counter(candidate["appliedRevision"]) ||
    !counter(candidate["snapshotRequests"]) ||
    !counter(candidate["sourceDisposals"]) ||
    !counter(candidate["sourceRevision"]) ||
    candidate["sourceRevision"] < 1
  ) {
    return null;
  }
  return candidate as unknown as TestFixtureHarness;
}

function sourceInput(value: unknown): DashboardSourceInput | null {
  const candidate = record(value);
  if (
    candidate === null ||
    !hasExactMembers(candidate, ["channel", "name", "raw"]) ||
    typeof candidate["channel"] !== "string" ||
    !TEST_FIXTURE_CHANNELS.has(candidate["channel"]) ||
    typeof candidate["name"] !== "string" ||
    typeof candidate["raw"] !== "string"
  ) {
    return null;
  }
  return {
    channel: candidate["channel"],
    name: candidate["name"],
    raw: candidate["raw"],
  };
}

function sourceInputs(value: unknown): readonly DashboardSourceInput[] | null {
  if (!Array.isArray(value)) {
    return null;
  }
  const inputs: DashboardSourceInput[] = [];
  for (const candidate of value) {
    const validated = sourceInput(candidate);
    if (validated === null) {
      return null;
    }
    inputs.push(validated);
  }
  return inputs;
}

function fixtureScript(value: unknown): TestFixtureScript | null {
  const candidate = record(value);
  if (
    candidate === null ||
    !hasExactMembers(candidate, ["fixtureVersion", "inputs"]) ||
    candidate["fixtureVersion"] !== "dashboard-source-script/v1"
  ) {
    return null;
  }
  const inputs = sourceInputs(candidate["inputs"]);
  return inputs === null ? null : { fixtureVersion: "dashboard-source-script/v1", inputs };
}

function fixtureBatch(event: Event): TestFixtureBatch | null {
  if (!(event instanceof CustomEvent)) {
    return null;
  }
  const candidate = record(event.detail);
  if (
    candidate === null ||
    !hasExactMembers(candidate, ["inputs", "replace", "revision"]) ||
    typeof candidate["replace"] !== "boolean" ||
    !counter(candidate["revision"]) ||
    candidate["revision"] < 1
  ) {
    return null;
  }
  const inputs = sourceInputs(candidate["inputs"]);
  return inputs === null
    ? null
    : { inputs, replace: candidate["replace"], revision: candidate["revision"] };
}

/** Test-build-only adapter for the serialized Playwright input boundary. */
export class TestFixtureSource implements DashboardEventSource {
  private consumer: DashboardSourceConsumer | null = null;
  private disposed = false;
  private generation = 0;
  private lastRevision = 0;
  private readonly onRefusal: TestFixtureRefusalConsumer | undefined;
  private processing: Promise<void> = Promise.resolve();
  private readonly window: Window;

  constructor(windowHost: Window, onRefusal?: TestFixtureRefusalConsumer) {
    this.window = windowHost;
    this.onRefusal = onRefusal;
  }

  open(consumer: DashboardSourceConsumer): DashboardSourceSubscription {
    if (this.consumer !== null || this.disposed) {
      throw new Error("a test fixture source can be opened only once");
    }
    const harness = harnessFrom(this.window);
    if (harness === null) {
      this.refuse("INVALID_TEST_HARNESS");
      return {
        dispose: () => {
          this.dispose();
        },
      };
    }
    this.consumer = consumer;
    this.window.addEventListener(TEST_FIXTURE_EVENT_NAME, this.receiveRevision);
    const initialCandidate = harness.sourceScript;
    harness.sourceScript = null;
    const initialScript = fixtureScript(initialCandidate);
    if (initialScript === null) {
      this.refuse("INVALID_TEST_FIXTURE_SCRIPT");
    } else {
      this.lastRevision = harness.sourceRevision;
      this.enqueue({
        inputs: initialScript.inputs,
        replace: false,
        revision: harness.sourceRevision,
      });
    }
    return {
      dispose: () => {
        this.dispose();
      },
    };
  }

  whenIdle(): Promise<void> {
    return this.processing;
  }

  recordSnapshotRequest(): void {
    const harness = harnessFrom(this.window);
    if (harness === null) {
      this.refuse("INVALID_TEST_HARNESS");
      return;
    }
    harness.snapshotRequests += 1;
  }

  private readonly receiveRevision = (event: Event): void => {
    const batch = fixtureBatch(event);
    if (batch === null) {
      this.refuse("INVALID_TEST_FIXTURE_BATCH");
      return;
    }
    if (batch.revision <= this.lastRevision) {
      this.refuse("NON_MONOTONIC_REVISION");
      return;
    }
    this.lastRevision = batch.revision;
    if (batch.replace) {
      this.generation += 1;
      this.recordLogicalDisposal();
    }
    this.enqueue(batch);
  };

  private enqueue(batch: TestFixtureBatch): void {
    const batchGeneration = this.generation;
    this.processing = this.processing.then(async () => {
      const consumer = this.consumer;
      if (this.disposed || batchGeneration !== this.generation || consumer === null) {
        return;
      }
      for (const input of batch.inputs) {
        await consumer(input);
        if (batchGeneration !== this.generation) {
          this.acknowledge(batch.revision);
          return;
        }
      }
      this.acknowledge(batch.revision);
    });
  }

  private acknowledge(revision: number): void {
    const harness = harnessFrom(this.window);
    if (harness === null) {
      this.refuse("INVALID_TEST_HARNESS");
      return;
    }
    harness.appliedRevision = revision;
  }

  private recordLogicalDisposal(): void {
    const harness = harnessFrom(this.window);
    if (harness === null) {
      this.refuse("INVALID_TEST_HARNESS");
      return;
    }
    harness.sourceDisposals += 1;
  }

  private dispose(): void {
    if (this.disposed) {
      return;
    }
    this.disposed = true;
    this.generation += 1;
    this.window.removeEventListener(TEST_FIXTURE_EVENT_NAME, this.receiveRevision);
    this.recordLogicalDisposal();
  }

  private refuse(code: TestFixtureSourceRefusal["code"]): void {
    this.onRefusal?.({ code });
  }
}
