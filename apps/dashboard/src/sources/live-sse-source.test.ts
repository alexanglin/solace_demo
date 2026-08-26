import { afterEach, expect, test, vi } from "vitest";

import type { DashboardSourceInput } from "./event-source";
import {
  DASHBOARD_TRANSPORT_OFFLINE_MILLISECONDS,
  LiveSseSource,
  type LiveEventStream,
} from "./live-sse-source";

afterEach(() => {
  vi.useRealTimers();
});

class FakeEventStream implements LiveEventStream {
  closeCount = 0;
  readyState = 0;
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

  dispatch(name: string, data = ""): void {
    const event =
      name === "open" || name === "error" ? new Event(name) : new MessageEvent(name, { data });
    this.dispatchEvent(name, event);
  }

  dispatchEvent(name: string, event: Event): void {
    for (const listener of this.listeners.get(name) ?? []) {
      listener(event);
    }
  }

  listenerCount(name: string): number {
    return this.listeners.get(name)?.size ?? 0;
  }
}

test("opens the injected URL and emits only the three named SSE data frames", async () => {
  // Arrange
  const stream = new FakeEventStream();
  const factory = vi.fn(() => stream);
  const received: DashboardSourceInput[] = [];
  const source = new LiveSseSource({ factory, url: "/api/v1/events" });
  source.open((input) => {
    received.push(input);
    return Promise.resolve();
  });

  // Act
  stream.dispatch("message", '{"ignored":true}');
  stream.dispatch("snapshot", '{"snapshotVersion":"dashboard-snapshot/v1"}');
  stream.dispatch("dashboard-event", '{"frameVersion":"ordered-dashboard-event-frame/v1"}');
  stream.dispatch("stream-overloaded", '{"controlVersion":"dashboard-stream-overloaded/v1"}');
  await Promise.resolve();

  // Assert
  expect(factory).toHaveBeenCalledWith("/api/v1/events");
  expect(received).toEqual([
    {
      channel: "source-signal",
      name: "connecting",
      raw: '{"signalVersion":"dashboard-source-signal/v1","signal":"connecting"}',
    },
    {
      channel: "sse-frame",
      lastEventId: "",
      name: "snapshot",
      raw: '{"snapshotVersion":"dashboard-snapshot/v1"}',
    },
    {
      channel: "sse-frame",
      lastEventId: "",
      name: "dashboard-event",
      raw: '{"frameVersion":"ordered-dashboard-event-frame/v1"}',
    },
    {
      channel: "sse-frame",
      lastEventId: "",
      name: "stream-overloaded",
      raw: '{"controlVersion":"dashboard-stream-overloaded/v1"}',
    },
  ]);
});

test("forwards the native SSE event identifier with every named data frame", async () => {
  // Arrange
  const stream = new FakeEventStream();
  const received: DashboardSourceInput[] = [];
  const source = new LiveSseSource({ factory: () => stream, url: "/events" });
  source.open((input) => {
    received.push(input);
    return Promise.resolve();
  });
  const nativeFrame = new MessageEvent("snapshot", {
    data: '{"snapshotVersion":"dashboard-snapshot/v1"}',
    lastEventId: "opaque-snapshot-cursor",
  });

  // Act
  stream.dispatchEvent("snapshot", nativeFrame);
  await Promise.resolve();

  // Assert
  expect(received.at(-1)).toEqual({
    channel: "sse-frame",
    lastEventId: "opaque-snapshot-cursor",
    name: "snapshot",
    raw: '{"snapshotVersion":"dashboard-snapshot/v1"}',
  });
});

test("reports disconnect and recovery as serialized source signals", async () => {
  // Arrange
  const stream = new FakeEventStream();
  const received: DashboardSourceInput[] = [];
  const source = new LiveSseSource({ factory: () => stream, url: "/events" });
  source.open((input) => {
    received.push(input);
    return Promise.resolve();
  });

  // Act
  stream.dispatch("open");
  stream.dispatch("error");
  stream.dispatch("error");
  stream.dispatch("open");
  await Promise.resolve();

  // Assert
  expect(received.map(({ name }) => name)).toEqual(["connecting", "disconnected", "recovered"]);
  expect(received.every(({ channel }) => channel === "source-signal")).toBe(true);
});

test("enters offline after one bounded outage and reports recovery when the stream reopens", async () => {
  // Arrange
  vi.useFakeTimers();
  const stream = new FakeEventStream();
  const received: DashboardSourceInput[] = [];
  const source = new LiveSseSource({ factory: () => stream, url: "/events" });
  source.open((input) => {
    received.push(input);
    return Promise.resolve();
  });

  // Act
  stream.dispatch("error");
  await vi.advanceTimersByTimeAsync(DASHBOARD_TRANSPORT_OFFLINE_MILLISECONDS - 1);
  const beforeBound = received.map(({ name }) => name);
  await vi.advanceTimersByTimeAsync(1);
  stream.dispatch("open");

  // Assert
  expect(beforeBound).toEqual(["connecting", "disconnected"]);
  expect(received.map(({ name }) => name)).toEqual([
    "connecting",
    "disconnected",
    "offline",
    "recovered",
  ]);
});

test("reopens a terminally closed browser stream after the bounded outage transition", async () => {
  // Arrange
  vi.useFakeTimers();
  const first = new FakeEventStream();
  const replacement = new FakeEventStream();
  const factory = vi.fn().mockReturnValueOnce(first).mockReturnValueOnce(replacement);
  const received: DashboardSourceInput[] = [];
  const source = new LiveSseSource({ factory, url: "/events" });
  source.open((input) => {
    received.push(input);
    return Promise.resolve();
  });
  first.readyState = 2;

  // Act
  first.dispatch("error");
  await vi.advanceTimersByTimeAsync(DASHBOARD_TRANSPORT_OFFLINE_MILLISECONDS);
  replacement.dispatch("open");

  // Assert
  expect(factory).toHaveBeenCalledTimes(2);
  expect(first.closeCount).toBe(1);
  expect(received.map(({ name }) => name)).toEqual([
    "connecting",
    "disconnected",
    "offline",
    "recovered",
  ]);
});

test("cancels pending offline transitions on early recovery and source disposal", async () => {
  // Arrange
  vi.useFakeTimers();
  const stream = new FakeEventStream();
  const received: DashboardSourceInput[] = [];
  const source = new LiveSseSource({ factory: () => stream, url: "/events" });
  const subscription = source.open((input) => {
    received.push(input);
    return Promise.resolve();
  });

  // Act
  stream.dispatch("error");
  stream.dispatch("open");
  stream.dispatch("error");
  subscription.dispose();
  await vi.advanceTimersByTimeAsync(DASHBOARD_TRANSPORT_OFFLINE_MILLISECONDS);

  // Assert
  expect(received.map(({ name }) => name)).toEqual([
    "connecting",
    "disconnected",
    "recovered",
    "disconnected",
  ]);
  expect(stream.closeCount).toBe(1);
});

test("turns a non-message named callback into malformed raw input for boundary refusal", async () => {
  // Arrange
  const stream = new FakeEventStream();
  const received: DashboardSourceInput[] = [];
  const source = new LiveSseSource({ factory: () => stream, url: "/events" });
  source.open((input) => {
    received.push(input);
    return Promise.resolve();
  });

  // Act
  stream.dispatchEvent("snapshot", new Event("snapshot"));
  await Promise.resolve();

  // Assert
  expect(received.at(-1)).toEqual({
    channel: "sse-frame",
    lastEventId: "",
    name: "snapshot",
    raw: "",
  });
});

test("removes listeners and closes the browser stream exactly once", async () => {
  // Arrange
  const stream = new FakeEventStream();
  const consumer = vi.fn(() => Promise.resolve());
  const source = new LiveSseSource({ factory: () => stream, url: "/events" });
  const subscription = source.open(consumer);
  await Promise.resolve();

  // Act
  subscription.dispose();
  subscription.dispose();
  stream.dispatch("snapshot", "{}");

  // Assert
  expect(stream.closeCount).toBe(1);
  expect(stream.listenerCount("snapshot")).toBe(0);
  expect(consumer).toHaveBeenCalledTimes(1);
});

test("uses the browser EventSource constructor when no factory is injected", async () => {
  // Arrange
  const openedUrls: string[] = [];
  class BrowserEventSourceStub extends FakeEventStream {
    constructor(url: string) {
      super();
      openedUrls.push(url);
    }
  }
  vi.stubGlobal("EventSource", BrowserEventSourceStub);
  const source = new LiveSseSource({ url: "/api/v1/events" });

  // Act
  const subscription = source.open(() => Promise.resolve());
  await Promise.resolve();
  subscription.dispose();
  vi.unstubAllGlobals();

  // Assert
  expect(openedUrls).toEqual(["/api/v1/events"]);
});
