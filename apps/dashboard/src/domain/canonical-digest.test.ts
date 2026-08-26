import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

import { expect, test, vi } from "vitest";

import type { DashboardReducedState, OrderedDashboardEvent } from "../contracts/generated";
import { createDashboardSchemaRegistry } from "../contracts/schema-registry";
import {
  CanonicalizationError,
  DigestError,
  digestMatches,
  digestDocument,
  orderedDashboardEventDigest,
  replayStateDigest,
  validateOrdinalWitness,
} from "./canonical";

const EVENT_FRAME_SCHEMA_ID =
  "https://aerial-rescue.invalid/schemas/v1/dashboard/dashboard-event-frame.schema.json";
const SNAPSHOT_SCHEMA_ID =
  "https://aerial-rescue.invalid/schemas/v1/dashboard/dashboard-snapshot.schema.json";
const FIXTURE_ROOT = resolve(process.cwd(), "../../fixtures/golden/v1/dashboard");
const BASELINE_REPLAY_STATE_DIGEST =
  "6dc970704498d43445023241c0dac7efdcf97cdaa3d28adae2d861cdd0764b34";
const BASELINE_ORDERED_EVENT_DIGEST =
  "eafd46f76f706183272a016f99d5468c7ebde22de44600092f81992903509c25";

type DigestContext =
  "evidence" | "idempotency-body" | "ordered-dashboard-event" | "proposal-digest" | "replay-state";

interface DigestContextCase {
  readonly context: DigestContext;
  readonly expectedDigest: string;
}

interface DigestRefusalCase {
  readonly expectedRefusal: "NOT_AN_OBJECT" | "VERSION";
  readonly label: string;
  readonly value: unknown;
}

interface EventTamperingCase {
  readonly field: "data" | "eventClass" | "kind" | "mission" | "time";
  readonly replacement: unknown;
}

interface OrdinalWitnessCase {
  readonly expected:
    | { readonly ok: true }
    | {
        readonly failure: { readonly code: "ORDINAL_WITNESS_INVARIANT" };
        readonly ok: false;
      };
  readonly latestAuditOrdinal: number;
  readonly latestEventDigest: null | string;
  readonly surface: string;
}

interface DigestMatchCase {
  readonly actual: unknown;
  readonly expected: unknown;
  readonly outcome:
    | { readonly matches: boolean; readonly ok: true }
    | { readonly failure: { readonly code: "DIGEST_FORM" }; readonly ok: false };
  readonly relationship: string;
}

const digestContextCases: readonly DigestContextCase[] = [
  {
    context: "proposal-digest",
    expectedDigest: "539f858f4efaff3f20e077b6e53bb2959f259b340e336608304b7c185dfcb4c4",
  },
  {
    context: "replay-state",
    expectedDigest: "7f818535b6392c3a0a0896c98f2a33cb15eff4bac167988ed71c253638bba3bf",
  },
  {
    context: "evidence",
    expectedDigest: "4d59830c5c13e196c5fe80c0882f3cace2bb2e8ee9db1159677816e1530e1308",
  },
  {
    context: "idempotency-body",
    expectedDigest: "f8e44c13c03d808faf312677886d7f89ae2e96b80a6403ae9779ce35501b5193",
  },
  {
    context: "ordered-dashboard-event",
    expectedDigest: "41ceb0f009c6f5bffc25ea06b84144ac49ed0c47e0632d81ec8006eb7c9e5fda",
  },
];

const digestRefusalCases: readonly DigestRefusalCase[] = [
  { expectedRefusal: "NOT_AN_OBJECT", label: "null", value: null },
  { expectedRefusal: "NOT_AN_OBJECT", label: "an array", value: [] },
  { expectedRefusal: "NOT_AN_OBJECT", label: "a string", value: "state" },
  { expectedRefusal: "VERSION", label: "a missing version", value: { missionId: "mission-1" } },
  {
    expectedRefusal: "VERSION",
    label: "another version",
    value: { canonicalizationVersion: 2 },
  },
  {
    expectedRefusal: "VERSION",
    label: "a boolean version",
    value: { canonicalizationVersion: true },
  },
];

const eventTamperingCases: readonly EventTamperingCase[] = [
  { field: "kind", replacement: "missionLifecycleTampered" },
  { field: "eventClass", replacement: "CONNECTIVITY" },
  { field: "mission", replacement: "mission-synthetic-9999" },
  { field: "time", replacement: "2026-08-24T12:00:00.001Z" },
  { field: "data", replacement: { lifecycle: "SEARCHING" } },
];

const ordinalWitnessCases: readonly OrdinalWitnessCase[] = [
  {
    expected: { ok: true },
    latestAuditOrdinal: 0,
    latestEventDigest: null,
    surface: "an empty snapshot",
  },
  {
    expected: { ok: true },
    latestAuditOrdinal: 4,
    latestEventDigest: BASELINE_ORDERED_EVENT_DIGEST,
    surface: "an eventful replay checkpoint",
  },
  {
    expected: { failure: { code: "ORDINAL_WITNESS_INVARIANT" }, ok: false },
    latestAuditOrdinal: 0,
    latestEventDigest: BASELINE_ORDERED_EVENT_DIGEST,
    surface: "a snapshot with a witness but no event",
  },
  {
    expected: { failure: { code: "ORDINAL_WITNESS_INVARIANT" }, ok: false },
    latestAuditOrdinal: 4,
    latestEventDigest: null,
    surface: "a replay checkpoint with events but no witness",
  },
];

const digestMatchCases: readonly DigestMatchCase[] = [
  {
    actual: BASELINE_ORDERED_EVENT_DIGEST,
    expected: BASELINE_ORDERED_EVENT_DIGEST,
    outcome: { matches: true, ok: true },
    relationship: "equal lowercase SHA-256 strings",
  },
  {
    actual: `0${BASELINE_ORDERED_EVENT_DIGEST.slice(1)}`,
    expected: BASELINE_ORDERED_EVENT_DIGEST,
    outcome: { matches: false, ok: true },
    relationship: "valid strings differing at the first character",
  },
  {
    actual: `${BASELINE_ORDERED_EVENT_DIGEST.slice(0, 32)}0${BASELINE_ORDERED_EVENT_DIGEST.slice(33)}`,
    expected: BASELINE_ORDERED_EVENT_DIGEST,
    outcome: { matches: false, ok: true },
    relationship: "valid strings differing in the middle",
  },
  {
    actual: `${BASELINE_ORDERED_EVENT_DIGEST.slice(0, -1)}0`,
    expected: BASELINE_ORDERED_EVENT_DIGEST,
    outcome: { matches: false, ok: true },
    relationship: "valid strings differing at the final character",
  },
  {
    actual: BASELINE_ORDERED_EVENT_DIGEST.toUpperCase(),
    expected: BASELINE_ORDERED_EVENT_DIGEST,
    outcome: { failure: { code: "DIGEST_FORM" }, ok: false },
    relationship: "an uppercase candidate",
  },
  {
    actual: BASELINE_ORDERED_EVENT_DIGEST.slice(1),
    expected: BASELINE_ORDERED_EVENT_DIGEST,
    outcome: { failure: { code: "DIGEST_FORM" }, ok: false },
    relationship: "a short candidate",
  },
  {
    actual: `${BASELINE_ORDERED_EVENT_DIGEST}0`,
    expected: BASELINE_ORDERED_EVENT_DIGEST,
    outcome: { failure: { code: "DIGEST_FORM" }, ok: false },
    relationship: "a long candidate",
  },
  {
    actual: "g".repeat(64),
    expected: BASELINE_ORDERED_EVENT_DIGEST,
    outcome: { failure: { code: "DIGEST_FORM" }, ok: false },
    relationship: "a non-hexadecimal candidate",
  },
  {
    actual: null,
    expected: BASELINE_ORDERED_EVENT_DIGEST,
    outcome: { failure: { code: "DIGEST_FORM" }, ok: false },
    relationship: "a non-string candidate",
  },
  {
    actual: BASELINE_ORDERED_EVENT_DIGEST,
    expected: 7,
    outcome: { failure: { code: "DIGEST_FORM" }, ok: false },
    relationship: "a non-string expected digest",
  },
];

async function readFixture(directory: string): Promise<unknown> {
  const raw = await readFile(resolve(FIXTURE_ROOT, directory, "baseline.json"), "utf8");
  return JSON.parse(raw) as unknown;
}

async function readBaselineReducedState(): Promise<DashboardReducedState> {
  const candidate = await readFixture("dashboard-snapshot");
  const result = createDashboardSchemaRegistry().validate(SNAPSHOT_SCHEMA_ID, candidate);
  if (!result.ok) {
    throw new Error("dashboard snapshot baseline failed its schema");
  }
  return result.value.state;
}

async function readBaselineOrderedEvent(): Promise<OrderedDashboardEvent> {
  const candidate = await readFixture("ordered-dashboard-event");
  const result = createDashboardSchemaRegistry().validate(EVENT_FRAME_SCHEMA_ID, {
    cursor: "mission-cursor-canonical-event",
    digest: "0".repeat(64),
    event: candidate,
    frameVersion: "ordered-dashboard-event-frame/v1",
  });
  if (!result.ok) {
    throw new Error("dashboard event-frame baseline failed its schema");
  }
  return result.value.event;
}

async function digestRefusalOf(value: unknown): Promise<string> {
  try {
    await digestDocument("replay-state", value);
  } catch (error: unknown) {
    if (error instanceof DigestError) {
      return error.refusal;
    }
    throw error;
  }
  throw new Error("digest document was accepted");
}

async function canonicalDigestRefusalOf(value: unknown): Promise<string> {
  try {
    await digestDocument("replay-state", value);
  } catch (error: unknown) {
    if (error instanceof CanonicalizationError) {
      return error.refusal;
    }
    throw error;
  }
  throw new Error("digest document was accepted");
}

test("uses Web Crypto SHA-256 over the versioned, domain-separated canonical material", async () => {
  // Arrange
  const payload = { canonicalizationVersion: 1, missionId: "mission-synthetic-0001" };
  const cryptoDigest = vi.spyOn(globalThis.crypto.subtle, "digest");

  // Act
  const computed = await digestDocument("replay-state", payload);

  // Assert
  expect(cryptoDigest).toHaveBeenCalledOnce();
  expect(cryptoDigest.mock.calls[0]?.[0]).toBe("SHA-256");
  expect(computed).toBe("7f818535b6392c3a0a0896c98f2a33cb15eff4bac167988ed71c253638bba3bf");
});

test.each(digestContextCases)(
  "separates identical bytes in the $context context",
  async ({ context, expectedDigest }) => {
    // Arrange
    const payload = { canonicalizationVersion: 1, missionId: "mission-synthetic-0001" };

    // Act
    const computed = await digestDocument(context, payload);

    // Assert
    expect(computed).toBe(expectedDigest);
    expect(computed).toMatch(/^[0-9a-f]{64}$/u);
  },
);

test("excludes only the top-level digest member from the covered document", async () => {
  // Arrange
  const plain = { canonicalizationVersion: 1, nested: { digest: "kept" } };
  const withTopLevelDigest = { ...plain, digest: "excluded" };
  const withChangedNestedDigest = { canonicalizationVersion: 1, nested: { digest: "changed" } };

  // Act
  const [plainDigest, topLevelDigest, changedNestedDigest] = await Promise.all([
    digestDocument("replay-state", plain),
    digestDocument("replay-state", withTopLevelDigest),
    digestDocument("replay-state", withChangedNestedDigest),
  ]);

  // Assert
  expect(topLevelDigest).toBe(plainDigest);
  expect(changedNestedDigest).not.toBe(plainDigest);
});

test.each(digestRefusalCases)(
  "returns the structured $expectedRefusal digest refusal for $label",
  async ({ expectedRefusal, value }) => {
    // Arrange
    const candidate = value;

    // Act
    const refusal = await digestRefusalOf(candidate);

    // Assert
    expect(refusal).toBe(expectedRefusal);
  },
);

test("matches the unchanged shared replay-state baseline digest", async () => {
  // Arrange
  const state = await readBaselineReducedState();

  // Act
  const computed = await replayStateDigest(state);

  // Assert
  expect(computed).toBe(BASELINE_REPLAY_STATE_DIGEST);
  expect("latestEventDigest" in state).toBe(false);
});

test("produces the same replay-state digest on ten identical runs", async () => {
  // Arrange
  const state = await readBaselineReducedState();

  // Act
  const computed = await Promise.all(
    Array.from({ length: 10 }, async () => replayStateDigest(state)),
  );

  // Assert
  expect(computed).toEqual(Array.from({ length: 10 }, () => BASELINE_REPLAY_STATE_DIGEST));
});

test("hashes the exact versioned ordered-event witness document in its own context", async () => {
  // Arrange
  const orderedEvent = await readBaselineOrderedEvent();
  const witnessDocument = {
    auditOrdinal: orderedEvent.auditOrdinal,
    canonicalizationVersion: 1,
    event: orderedEvent.event,
  };

  // Act
  const [specializedDigest, documentDigest] = await Promise.all([
    orderedDashboardEventDigest(orderedEvent),
    digestDocument("ordered-dashboard-event", witnessDocument),
  ]);

  // Assert
  expect(specializedDigest).toBe(BASELINE_ORDERED_EVENT_DIGEST);
  expect(documentDigest).toBe(BASELINE_ORDERED_EVENT_DIGEST);
});

test("changes the ordered-event witness when its audit ordinal is tampered", async () => {
  // Arrange
  const orderedEvent = await readBaselineOrderedEvent();
  const tamperedEvent = { ...orderedEvent, auditOrdinal: orderedEvent.auditOrdinal + 1 };

  // Act
  const [baselineDigest, tamperedDigest] = await Promise.all([
    orderedDashboardEventDigest(orderedEvent),
    orderedDashboardEventDigest(tamperedEvent),
  ]);

  // Assert
  expect(baselineDigest).toBe(BASELINE_ORDERED_EVENT_DIGEST);
  expect(tamperedDigest).not.toBe(baselineDigest);
});

test.each(eventTamperingCases)(
  "changes the ordered-event witness when its $field field is tampered",
  async ({ field, replacement }) => {
    // Arrange
    const orderedEvent = await readBaselineOrderedEvent();
    const tamperedEvent: Record<string, unknown> = { ...orderedEvent.event };
    tamperedEvent[field] = replacement;
    const witnessDocument = {
      auditOrdinal: orderedEvent.auditOrdinal,
      canonicalizationVersion: 1,
      event: tamperedEvent,
    };

    // Act
    const tamperedDigest = await digestDocument("ordered-dashboard-event", witnessDocument);

    // Assert
    expect(tamperedDigest).not.toBe(BASELINE_ORDERED_EVENT_DIGEST);
  },
);

test.each(ordinalWitnessCases)(
  "enforces the snapshot/replay ordinal-witness invariant for $surface",
  ({ expected, latestAuditOrdinal, latestEventDigest }) => {
    // Arrange
    const ordinal = latestAuditOrdinal;
    const witness = latestEventDigest;

    // Act
    const result = validateOrdinalWitness(ordinal, witness);

    // Assert
    expect(result).toEqual(expected);
  },
);

test("refuses a digest accessor without invoking it or reading the version separately", async () => {
  // Arrange
  const document: Record<string, unknown> = { missionId: "mission-synthetic-0001" };
  let getterCalls = 0;
  Object.defineProperty(document, "canonicalizationVersion", {
    enumerable: true,
    get: () => {
      getterCalls += 1;
      return 1;
    },
  });

  // Act
  const refusal = await canonicalDigestRefusalOf(document);

  // Assert
  expect(refusal).toBe("PROPERTY_FORM");
  expect(getterCalls).toBe(0);
});

test.each([
  {
    label: "a non-enumerable member",
    makeDocument: () => {
      const document: Record<string, unknown> = { canonicalizationVersion: 1 };
      Object.defineProperty(document, "missionId", {
        enumerable: false,
        value: "mission-synthetic-0001",
      });
      return document;
    },
    refusal: "PROPERTY_FORM",
  },
  {
    label: "a symbol member",
    makeDocument: () => ({ canonicalizationVersion: 1, [Symbol("hidden")]: "value" }),
    refusal: "KEY_FORM",
  },
] as const)(
  "refuses a digest document carrying $label from the descriptor snapshot",
  async ({ makeDocument, refusal }) => {
    // Arrange
    const document = makeDocument();

    // Act
    const observed = await canonicalDigestRefusalOf(document);

    // Assert
    expect(observed).toBe(refusal);
  },
);

test.each(digestMatchCases)(
  "returns a typed comparison result for $relationship",
  ({ actual, expected, outcome }) => {
    // Arrange
    const expectedDigest = expected;
    const actualDigest = actual;

    // Act
    const result = digestMatches(expectedDigest, actualDigest);

    // Assert
    expect(result).toEqual(outcome);
  },
);
