import { expect, test, vi } from "vitest";

import { ReplayBundleSource } from "./replay-bundle-source";

test.each([
  ["network", () => Promise.reject(new Error("offline"))],
  ["http", () => Promise.resolve(new Response("ignored", { status: 503 }))],
] as const)(
  "emits an empty refusal candidate after a %s replay failure",
  async (_kind, fetcher) => {
    // Arrange
    let acceptDelivery: ((raw: string) => void) | undefined;
    const delivery = new Promise<string>((resolve) => {
      acceptDelivery = resolve;
    });
    const consumer = vi.fn((input: { readonly raw: string }) => {
      acceptDelivery?.(input.raw);
      return Promise.resolve();
    });
    const source = new ReplayBundleSource({ fetcher, url: "/api/v1/replays/session" });

    // Act
    source.open(consumer);
    const raw = await delivery;

    // Assert
    expect(raw).toBe("");
    expect(consumer).toHaveBeenCalledWith({
      channel: "replay-bundle",
      name: "validated-replay-bundle",
      raw: "",
    });
  },
);

test("uses same-origin browser fetch by default and disposes idempotently", async () => {
  // Arrange
  const fetcher = vi.fn(() => Promise.resolve(new Response("{}", { status: 200 })));
  vi.stubGlobal("fetch", fetcher);
  let acceptDelivery: ((raw: string) => void) | undefined;
  const delivery = new Promise<string>((resolve) => {
    acceptDelivery = resolve;
  });
  const consumer = vi.fn((input: { readonly raw: string }) => {
    acceptDelivery?.(input.raw);
    return Promise.resolve();
  });
  const source = new ReplayBundleSource({ url: "/api/v1/replays/session" });

  // Act
  const subscription = source.open(consumer);
  const raw = await delivery;
  subscription.dispose();
  subscription.dispose();
  vi.unstubAllGlobals();

  // Assert
  expect(fetcher).toHaveBeenCalledOnce();
  expect(raw).toBe("{}");
  expect(consumer).toHaveBeenCalledWith({
    channel: "replay-bundle",
    name: "validated-replay-bundle",
    raw: "{}",
  });
});
