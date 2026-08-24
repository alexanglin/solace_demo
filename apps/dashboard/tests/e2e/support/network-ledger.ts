import type { Page } from "@playwright/test";

export interface NetworkLedger {
  readonly eventSourceRequests: string[];
  readonly remoteRequests: string[];
  readonly runtimeRequests: string[];
  readonly waitForQuiescence: () => Promise<void>;
  readonly webSockets: string[];
}

const networkQuietWindowMilliseconds = 500;
const networkQuiescenceTimeoutMilliseconds = 3_000;
const networkPollIntervalMilliseconds = 25;

function redactedLocation(rawUrl: string): string {
  const url = new URL(rawUrl);
  return `${url.protocol}//${url.host}${url.pathname}`;
}

async function waitForQuiescence(activityVersion: () => number): Promise<void> {
  const deadline = Date.now() + networkQuiescenceTimeoutMilliseconds;
  let observedVersion = activityVersion();
  let quietSince = Date.now();

  while (Date.now() - quietSince < networkQuietWindowMilliseconds) {
    if (Date.now() >= deadline) {
      throw new Error(
        `browser network did not become quiescent within ${networkQuiescenceTimeoutMilliseconds.toString()} milliseconds`,
      );
    }
    await new Promise<void>((resolve) => {
      setTimeout(resolve, networkPollIntervalMilliseconds);
    });
    const currentVersion = activityVersion();
    if (currentVersion !== observedVersion) {
      observedVersion = currentVersion;
      quietSince = Date.now();
    }
  }
}

export async function installNetworkLedger(
  page: Page,
  allowedOrigin: string,
): Promise<NetworkLedger> {
  let activityVersion = 0;
  const ledger: NetworkLedger = {
    eventSourceRequests: [],
    remoteRequests: [],
    runtimeRequests: [],
    waitForQuiescence: async () => {
      await waitForQuiescence(() => activityVersion);
    },
    webSockets: [],
  };
  const recordActivity = (): void => {
    activityVersion += 1;
  };
  page.on("request", recordActivity);
  page.on("requestfailed", recordActivity);
  page.on("requestfinished", recordActivity);
  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const location = redactedLocation(request.url());
    if (url.origin !== allowedOrigin && url.protocol !== "blob:" && url.protocol !== "data:") {
      ledger.remoteRequests.push(location);
      await route.abort("blockedbyclient");
      return;
    }
    if (url.pathname.startsWith("/api/")) {
      ledger.runtimeRequests.push(location);
    }
    if (request.resourceType() === "eventsource") {
      ledger.eventSourceRequests.push(location);
    }
    await route.continue();
  });
  await page.routeWebSocket("**/*", async (socket) => {
    recordActivity();
    ledger.webSockets.push(redactedLocation(socket.url()));
    await socket.close({ code: 1008, reason: "browser network isolation" });
  });
  return ledger;
}
