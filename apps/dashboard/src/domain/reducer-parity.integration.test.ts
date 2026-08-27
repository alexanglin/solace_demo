import { spawnSync } from "node:child_process";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

import { expect, test } from "vitest";

import { decodeCanonicalJson } from "../contracts/bootstrap";
import type { DashboardReplayBundle, OrderedDashboardEvent } from "../contracts/generated";
import { createDashboardSchemaRegistry } from "../contracts/schema-registry";
import { canonicalBytes, replayStateDigest } from "./canonical";
import {
  checkpointFromReplayBundle,
  foldOrderedDashboardEvent,
  foldVerifiedOrderedDashboardEvent,
  type FoldResult,
  type ReducerCheckpoint,
} from "./reducer";
import { appendMeaningfulTimelineEvent, replaceTimelineFromSnapshot } from "./timeline";

const REPLAY_BUNDLE_SCHEMA_ID =
  "https://aerial-rescue.invalid/schemas/v1/dashboard/replay-bundle.schema.json";
const REPOSITORY_ROOT = resolve(process.cwd(), "../..");
const PARITY_FIXTURE = resolve(
  REPOSITORY_ROOT,
  "fixtures/golden/v1/dashboard/replay-bundle/reducer-parity.json",
);
const PYTHON_RUNNER = resolve(process.cwd(), "tests/unit-support/reducer_parity_runner.py");
const PYTHON_EXECUTABLE = resolve(REPOSITORY_ROOT, ".venv/bin/python");
const PYTHON_RUNNER_TIMEOUT_MILLISECONDS = 5_000;
const PYTHON_RUNNER_OUTPUT_BYTES = 1_048_576;
const textDecoder = new TextDecoder();

interface StepEvidence {
  readonly canonicalState: string;
  readonly disposition: string;
  readonly latestEventDigest: string | null;
  readonly stateDigest: string;
  readonly timelineOrdinals: readonly number[];
}

interface RunEvidence {
  readonly finalDigest: string;
  readonly initialCanonicalState: string;
  readonly initialLatestEventDigest: string | null;
  readonly steps: readonly StepEvidence[];
}

function lowercaseHexadecimal(value: ArrayBuffer): string {
  return Array.from(new Uint8Array(value), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function foldDisposition(result: FoldResult): string {
  return result.ok ? result.disposition : `REFUSED:${result.failure.code}`;
}

function acceptedCheckpoint(result: FoldResult): ReducerCheckpoint {
  if (!result.ok) {
    throw new Error(`unexpected reducer refusal: ${result.failure.code}`);
  }
  return result.checkpoint;
}

function replayChecksumMaterial(bundle: DashboardReplayBundle): object {
  return {
    bundleVersion: bundle.bundleVersion,
    events: bundle.events,
    initialState: bundle.initialState,
    integrity: {
      algorithm: bundle.integrity.algorithm,
      expectedFinalDigest: bundle.integrity.expectedFinalDigest,
      integrityVersion: bundle.integrity.integrityVersion,
    },
    latestEventDigest: bundle.latestEventDigest,
    scenarioId: bundle.scenarioId,
    scenarioRevision: bundle.scenarioRevision,
  };
}

async function provisionalReplayChecksum(bundle: DashboardReplayBundle): Promise<string> {
  const bytes = canonicalBytes(replayChecksumMaterial(bundle));
  const material = Uint8Array.from(bytes);
  const checksum = await globalThis.crypto.subtle.digest("SHA-256", material);
  return lowercaseHexadecimal(checksum);
}

async function runTypeScriptFold(bundle: DashboardReplayBundle): Promise<RunEvidence> {
  const anchored = checkpointFromReplayBundle(bundle);
  if (!anchored.ok) {
    throw new Error(`unexpected replay anchor refusal: ${anchored.failure.code}`);
  }
  let checkpoint = anchored.checkpoint;
  let timeline = replaceTimelineFromSnapshot([]);
  const steps: StepEvidence[] = [];
  for (const orderedEvent of bundle.events) {
    const folded = await foldOrderedDashboardEvent(checkpoint, orderedEvent);
    const successor = acceptedCheckpoint(folded);
    if (folded.ok && folded.disposition === "APPLIED") {
      timeline = appendMeaningfulTimelineEvent(timeline, orderedEvent);
    }
    checkpoint = successor;
    steps.push({
      canonicalState: textDecoder.decode(canonicalBytes(checkpoint.state)),
      disposition: foldDisposition(folded),
      latestEventDigest: checkpoint.latestEventDigest,
      stateDigest: await replayStateDigest(checkpoint.state),
      timelineOrdinals: timeline.map(({ auditOrdinal }) => auditOrdinal),
    });
  }
  return {
    finalDigest: await replayStateDigest(checkpoint.state),
    initialCanonicalState: textDecoder.decode(canonicalBytes(bundle.initialState)),
    initialLatestEventDigest: bundle.latestEventDigest,
    steps,
  };
}

function eventTamperingCases(
  orderedEvent: OrderedDashboardEvent,
): Readonly<Record<string, OrderedDashboardEvent>> {
  const event = orderedEvent.event;
  return {
    auditOrdinal: { ...orderedEvent, auditOrdinal: orderedEvent.auditOrdinal + 2 },
    data: {
      ...orderedEvent,
      event: { ...event, data: { lifecycle: "ABORTED" } },
    } as OrderedDashboardEvent,
    eventClass: {
      ...orderedEvent,
      event: { ...event, eventClass: "TELEMETRY" },
    } as OrderedDashboardEvent,
    kind: {
      ...orderedEvent,
      event: { ...event, kind: "unprojectedKind" },
    } as unknown as OrderedDashboardEvent,
    compoundKindMission: {
      ...orderedEvent,
      event: { ...event, kind: "unprojectedKind", mission: "INVALID_MISSION" },
    } as unknown as OrderedDashboardEvent,
    mission: {
      ...orderedEvent,
      event: { ...event, mission: "mission-synthetic-other" },
    },
    time: {
      ...orderedEvent,
      event: { ...event, time: "2026-08-25T12:00:13.000Z" },
    },
  };
}

async function typeScriptTamperingEvidence(
  bundle: DashboardReplayBundle,
): Promise<Readonly<Record<string, string>>> {
  const run = checkpointFromReplayBundle(bundle);
  if (!run.ok) {
    throw new Error(`unexpected replay anchor refusal: ${run.failure.code}`);
  }
  let checkpoint = run.checkpoint;
  for (const orderedEvent of bundle.events) {
    checkpoint = acceptedCheckpoint(await foldOrderedDashboardEvent(checkpoint, orderedEvent));
  }
  const finalEvent = bundle.events.at(-1);
  if (finalEvent === undefined) {
    throw new Error("parity replay has no final event");
  }
  const evidence: Record<string, string> = {};
  evidence["duplicate"] = foldDisposition(await foldOrderedDashboardEvent(checkpoint, finalEvent));
  for (const [name, tampered] of Object.entries(eventTamperingCases(finalEvent))) {
    evidence[name] = foldDisposition(await foldOrderedDashboardEvent(checkpoint, tampered));
  }
  evidence["serverDigest"] = foldDisposition(
    await foldVerifiedOrderedDashboardEvent(checkpoint, finalEvent, "0".repeat(64)),
  );
  return evidence;
}

test("proves the validated Python and TypeScript reducers agree across ten independent runs", async () => {
  // Arrange
  const raw = await readFile(PARITY_FIXTURE, "utf8");
  const decoded = decodeCanonicalJson(raw);
  if (!decoded.ok) {
    throw new Error(decoded.failure.code);
  }
  const validated = createDashboardSchemaRegistry().validate(
    REPLAY_BUNDLE_SCHEMA_ID,
    decoded.value,
  );
  if (!validated.ok) {
    throw new Error(validated.failure.code);
  }
  const bundle = validated.value;

  // Act
  const typeScriptRuns: RunEvidence[] = [];
  for (let index = 0; index < 10; index += 1) {
    typeScriptRuns.push(await runTypeScriptFold(bundle));
  }
  const typeScriptTampering = await typeScriptTamperingEvidence(bundle);
  const checksum = await provisionalReplayChecksum(bundle);
  const python = spawnSync(PYTHON_EXECUTABLE, [PYTHON_RUNNER, PARITY_FIXTURE, "10"], {
    cwd: REPOSITORY_ROOT,
    encoding: "utf8",
    maxBuffer: PYTHON_RUNNER_OUTPUT_BYTES,
    timeout: PYTHON_RUNNER_TIMEOUT_MILLISECONDS,
  });
  const pythonEvidence: unknown = python.status === 0 ? JSON.parse(python.stdout) : null;

  // Assert
  expect(python.status).toBe(0);
  expect(python.stderr).toBe("");
  expect(new Set(typeScriptRuns.map(({ finalDigest }) => finalDigest))).toEqual(
    new Set([bundle.integrity.expectedFinalDigest]),
  );
  expect(checksum).toBe(bundle.integrity.checksum);
  expect(pythonEvidence).toEqual({
    checksum,
    expectedFinalDigest: bundle.integrity.expectedFinalDigest,
    runs: typeScriptRuns,
    tampering: typeScriptTampering,
  });
});
