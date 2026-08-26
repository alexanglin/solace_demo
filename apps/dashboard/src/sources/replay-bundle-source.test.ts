import { expect, test, vi } from "vitest";

import type { DashboardSourceInput } from "./event-source";
import { ReplayBundleSource } from "./replay-bundle-source";

test("fetches one same-origin replay and emits its exact untrusted text", async () => {
  // Arrange
  const received: DashboardSourceInput[] = [];
  const fetcher = vi.fn<(input: string, init: RequestInit) => Promise<Response>>(() =>
    Promise.resolve(new Response('{"bundleVersion":"dashboard-replay-bundle/v1"}')),
  );
  const source = new ReplayBundleSource({
    fetcher,
    url: "/api/v1/replays/session-production-0001",
  });

  // Act
  source.open((input) => {
    received.push(input);
    return Promise.resolve();
  });
  await vi.waitUntil(() => received.length === 1);

  // Assert
  expect(received[0]).toEqual({
    channel: "replay-bundle",
    name: "validated-replay-bundle",
    raw: '{"bundleVersion":"dashboard-replay-bundle/v1"}',
  });
  expect(fetcher).toHaveBeenCalledOnce();
  const request = fetcher.mock.calls[0];
  expect(request?.[0]).toBe("/api/v1/replays/session-production-0001");
  expect(request?.[1]).toMatchObject({
    cache: "no-store",
    credentials: "same-origin",
    headers: { Accept: "application/json" },
    method: "GET",
  });
  expect(request?.[1].signal).toBeInstanceOf(AbortSignal);
});

test("refuses an over-bound replay without retaining or delivering its body", async () => {
  // Arrange
  const consumer = vi.fn(() => Promise.resolve());
  const oversized = "x".repeat(1024 * 1024 + 1);
  const source = new ReplayBundleSource({
    fetcher: () => Promise.resolve(new Response(oversized, { status: 200 })),
    url: "/api/v1/replays/session-production-0001",
  });

  // Act
  source.open(consumer);
  await vi.waitUntil(() => consumer.mock.calls.length === 1);

  // Assert
  expect(consumer).toHaveBeenCalledWith({
    channel: "replay-bundle",
    name: "validated-replay-bundle",
    raw: "",
  });
  expect(JSON.stringify(consumer.mock.calls)).not.toContain(oversized.slice(0, 128));
});

test("aborts disposal and suppresses a late replay response", async () => {
  // Arrange
  let release: ((response: Response) => void) | undefined;
  const pending = new Promise<Response>((resolve) => {
    release = resolve;
  });
  const consumer = vi.fn(() => Promise.resolve());
  const source = new ReplayBundleSource({ fetcher: () => pending, url: "/api/v1/replays/session" });

  // Act
  const subscription = source.open(consumer);
  subscription.dispose();
  release?.(new Response("{}"));
  await Promise.resolve();
  await Promise.resolve();

  // Assert
  expect(consumer).not.toHaveBeenCalled();
});
