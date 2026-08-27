export const DASHBOARD_SOAK_SAMPLE_INTERVAL_MILLISECONDS = 30_000;
export const DASHBOARD_SOAK_SAMPLE_COUNT = 61;
export const DASHBOARD_SOAK_DURATION_MILLISECONDS =
  DASHBOARD_SOAK_SAMPLE_INTERVAL_MILLISECONDS * (DASHBOARD_SOAK_SAMPLE_COUNT - 1);
export const DASHBOARD_SOAK_FINALIZATION_MARGIN_MILLISECONDS = 180_000;
export const DASHBOARD_SOAK_MAXIMUM_RSS_GROWTH_BYTES = 64 * 1024 * 1024;
export const DASHBOARD_SOAK_MAXIMUM_FD_GROWTH = 8;

export interface DashboardProcessSample {
  readonly containerId: string;
  readonly openFileDescriptors: number;
  readonly pid: number;
  readonly rssBytes: number;
}

export type DashboardProcessMetrics = Pick<
  DashboardProcessSample,
  "openFileDescriptors" | "rssBytes"
>;

export type DashboardSoakRefusal =
  | "CONTAINER_CHANGED"
  | "FD_GROWTH_EXCEEDED"
  | "PID_CHANGED"
  | "RSS_GROWTH_EXCEEDED"
  | "SAMPLE_COUNT_INVALID";

export interface DashboardSoakEvaluation {
  readonly ok: boolean;
  readonly refusals: readonly DashboardSoakRefusal[];
}

export interface DashboardSoakSummary {
  readonly baselineOpenFileDescriptors: number;
  readonly baselineRssBytes: number;
  readonly containerChanged: boolean;
  readonly maximumOpenFileDescriptors: number;
  readonly maximumRssBytes: number;
  readonly openFileDescriptorGrowth: number;
  readonly pidChanged: boolean;
  readonly rssGrowthBytes: number;
  readonly sampleCount: number;
}

/** Keeps sample targets on the fixed cadence even when a process probe takes time. */
export function soakDelayForSample(
  startedAtMilliseconds: number,
  sampleIndex: number,
  nowMilliseconds: number,
): number {
  return Math.max(
    0,
    startedAtMilliseconds +
      sampleIndex * DASHBOARD_SOAK_SAMPLE_INTERVAL_MILLISECONDS -
      nowMilliseconds,
  );
}

/** Parses the two bounded unsigned integers emitted by the in-container process probe. */
export function parseDashboardProcessProbe(raw: string): DashboardProcessMetrics {
  const match = /^(0|[1-9][0-9]{0,15}) (0|[1-9][0-9]{0,15})\n?$/u.exec(raw);
  const rssBytes = Number(match?.[1]);
  const openFileDescriptors = Number(match?.[2]);
  if (
    match === null ||
    !Number.isSafeInteger(rssBytes) ||
    rssBytes <= 0 ||
    !Number.isSafeInteger(openFileDescriptors) ||
    openFileDescriptors < 0
  ) {
    throw new Error("dashboard process probe was malformed");
  }
  return { openFileDescriptors, rssBytes };
}

/** Evaluates the exact accepted process-identity and bounded-growth soak envelope. */
export function evaluateDashboardSoakSamples(
  samples: readonly DashboardProcessSample[],
): DashboardSoakEvaluation {
  if (samples.length !== DASHBOARD_SOAK_SAMPLE_COUNT) {
    return { ok: false, refusals: ["SAMPLE_COUNT_INVALID"] };
  }
  const baseline = samples[0];
  if (baseline === undefined) return { ok: false, refusals: ["SAMPLE_COUNT_INVALID"] };
  const refusals: DashboardSoakRefusal[] = [];
  if (samples.some(({ containerId }) => containerId !== baseline.containerId)) {
    refusals.push("CONTAINER_CHANGED");
  }
  if (samples.some(({ pid }) => pid !== baseline.pid)) refusals.push("PID_CHANGED");
  const maximumRss = Math.max(...samples.map(({ rssBytes }) => rssBytes));
  if (maximumRss - baseline.rssBytes > DASHBOARD_SOAK_MAXIMUM_RSS_GROWTH_BYTES) {
    refusals.push("RSS_GROWTH_EXCEEDED");
  }
  const maximumDescriptors = Math.max(
    ...samples.map(({ openFileDescriptors }) => openFileDescriptors),
  );
  if (maximumDescriptors - baseline.openFileDescriptors > DASHBOARD_SOAK_MAXIMUM_FD_GROWTH) {
    refusals.push("FD_GROWTH_EXCEEDED");
  }
  return { ok: refusals.length === 0, refusals };
}

/** Produces the exact non-secret resource measurements retained with production soak evidence. */
export function summarizeDashboardSoakSamples(
  samples: readonly DashboardProcessSample[],
): DashboardSoakSummary {
  const baseline = samples[0];
  if (baseline === undefined) {
    throw new Error("dashboard soak summary requires at least one sample");
  }
  const maximumRssBytes = Math.max(...samples.map(({ rssBytes }) => rssBytes));
  const maximumOpenFileDescriptors = Math.max(
    ...samples.map(({ openFileDescriptors }) => openFileDescriptors),
  );
  return {
    baselineOpenFileDescriptors: baseline.openFileDescriptors,
    baselineRssBytes: baseline.rssBytes,
    containerChanged: samples.some(({ containerId }) => containerId !== baseline.containerId),
    maximumOpenFileDescriptors,
    maximumRssBytes,
    openFileDescriptorGrowth: maximumOpenFileDescriptors - baseline.openFileDescriptors,
    pidChanged: samples.some(({ pid }) => pid !== baseline.pid),
    rssGrowthBytes: maximumRssBytes - baseline.rssBytes,
    sampleCount: samples.length,
  };
}
