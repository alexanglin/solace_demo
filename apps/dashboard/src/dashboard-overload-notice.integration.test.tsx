import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { fixtureForState, resilienceFaultInputs } from "../tests/e2e/support/dashboard-fixtures";
import { DashboardApplication } from "./dashboard-app";
import type {
  DashboardEventSource,
  DashboardSourceConsumer,
  DashboardSourceInput,
  DashboardSourceSubscription,
} from "./sources/event-source";
import type {
  ProductionDashboardRuntimeOptions,
  ProductionRuntimePort,
} from "./sources/production-runtime";

vi.mock("./components/search-map", () => ({
  SearchMap: () => <section aria-label="Search map">Production map</section>,
}));

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

class ManualSource implements DashboardEventSource {
  private consumer: DashboardSourceConsumer | null = null;
  private resolveOpened: (() => void) | null = null;
  private readonly opened = new Promise<void>((resolve) => {
    this.resolveOpened = resolve;
  });

  emit(input: DashboardSourceInput): Promise<void> {
    if (this.consumer === null) throw new Error("manual source was not open");
    return this.consumer(input);
  }

  open(consumer: DashboardSourceConsumer): DashboardSourceSubscription {
    this.consumer = consumer;
    this.resolveOpened?.();
    return { dispose: vi.fn() };
  }

  whenOpened(): Promise<void> {
    return this.opened;
  }
}

class ImmediateResnapshotRuntime implements ProductionRuntimePort {
  private readonly boundaries: readonly DashboardSourceInput[];
  private readonly consumeBoundary: DashboardSourceConsumer;
  private readonly replacementInputs: readonly DashboardSourceInput[];
  private readonly session: ProductionDashboardRuntimeOptions["session"];
  readonly initialSource = new ManualSource();
  readonly replacementSource = new ManualSource();
  acceptedMutationCount = 0;
  disposed = false;
  selectedMode: "degradedLive" | "replay" | null = null;
  private replacementDelivery: Promise<void> = Promise.resolve();

  constructor(
    options: ProductionDashboardRuntimeOptions,
    boundaries: readonly DashboardSourceInput[],
    replacementInputs: readonly DashboardSourceInput[],
  ) {
    this.boundaries = [options.bootstrap, ...boundaries];
    this.consumeBoundary = options.consumeBoundary;
    this.replacementInputs = replacementInputs;
    this.session = options.session;
  }

  acceptedMutation(): void {
    this.acceptedMutationCount += 1;
  }

  dispose(): void {
    this.disposed = true;
  }

  observeSnapshotRun = vi.fn();

  resnapshot(): void {
    this.session.replaceSource(this.replacementSource, "degradedLive");
    this.replacementDelivery = (async () => {
      for (const input of this.replacementInputs) await this.replacementSource.emit(input);
    })();
  }

  selectMode(mode: "degradedLive" | "replay"): Promise<void> {
    this.selectedMode = mode;
    return Promise.resolve();
  }

  async start(): Promise<void> {
    for (const boundary of this.boundaries) await this.consumeBoundary(boundary);
    this.session.replaceSource(this.initialSource, "degradedLive");
  }

  async whenReplacementDelivered(): Promise<void> {
    await this.replacementSource.whenOpened();
    await this.replacementDelivery;
  }
}

test("keeps the overload announcement observable across an immediate replacement snapshot", async () => {
  // Arrange
  vi.useFakeTimers();
  const running = fixtureForState("running").inputs;
  const bootstrap = running.find(({ channel }) => channel === "bootstrap");
  const boundaries = running.filter(({ channel }) => channel === "http-response");
  const initialSnapshot = running.find(({ name }) => name === "snapshot");
  const replacementInputs = fixtureForState("exhausted").inputs.filter(
    ({ channel }) => channel === "sse-frame",
  );
  const overload = resilienceFaultInputs("streamOverloaded")[0];
  if (
    bootstrap === undefined ||
    initialSnapshot === undefined ||
    replacementInputs.length === 0 ||
    overload === undefined
  ) {
    throw new Error("overload notice fixture was incomplete");
  }
  let runtime: ImmediateResnapshotRuntime | undefined;
  render(
    <DashboardApplication
      productionBootstrap={bootstrap}
      productionRuntimeFactory={(options) => {
        runtime = new ImmediateResnapshotRuntime(options, boundaries, replacementInputs);
        return runtime;
      }}
    />,
  );
  const activeRuntime = runtime;
  if (activeRuntime === undefined) throw new Error("production runtime was not constructed");
  await activeRuntime.initialSource.whenOpened();
  await act(async () => {
    await activeRuntime.initialSource.emit(initialSnapshot);
  });

  // Act
  await act(async () => {
    await activeRuntime.initialSource.emit(overload);
    await activeRuntime.whenReplacementDelivered();
  });
  const announced = screen.getByRole("status", { name: "Dashboard state" }).textContent;
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1_000);
  });

  // Assert
  expect(screen.getByRole("status", { name: "Current mission" }).textContent).toContain(
    "EXHAUSTED",
  );
  expect(announced).toBe("Stream overloaded · resynchronizing");
  expect(screen.getByRole("status", { name: "Dashboard state" }).textContent).toBe(
    "Mission exhausted",
  );
});
