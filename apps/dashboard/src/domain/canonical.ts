import type { DashboardReducedState, OrderedDashboardEvent } from "../contracts/generated";

const CANONICAL_KEY_PATTERN = /^[a-z][a-zA-Z0-9]*$/u;
const MAX_CANONICAL_KEY_CHARACTERS = 64;
const MAX_CANONICAL_STRING_BYTES = 4096;
const CANONICALIZATION_VERSION = 1;
const DIGEST_PREFIX = "aerial-rescue/canonical/v1";
const LOWERCASE_SHA256_PATTERN = /^[0-9a-f]{64}$/u;
const textEncoder = new TextEncoder();

export type CanonicalRefusal =
  | "ARRAY_FORM"
  | "CYCLE"
  | "INTEGER_RANGE"
  | "KEY_FORM"
  | "KEY_LENGTH"
  | "LONE_SURROGATE"
  | "PROPERTY_FORM"
  | "STRING_LENGTH"
  | "UNSUPPORTED_TYPE";

export class CanonicalizationError extends Error {
  readonly refusal: CanonicalRefusal;
  readonly value: unknown;

  constructor(refusal: CanonicalRefusal, value: unknown) {
    super(refusal);
    this.name = "CanonicalizationError";
    this.refusal = refusal;
    this.value = value;
  }
}

export type DigestContext =
  "evidence" | "idempotency-body" | "ordered-dashboard-event" | "proposal-digest" | "replay-state";

export type DigestRefusal = "NOT_AN_OBJECT" | "VERSION";

export class DigestError extends Error {
  readonly refusal: DigestRefusal;
  readonly value: unknown;

  constructor(refusal: DigestRefusal, value: unknown) {
    super(refusal);
    this.name = "DigestError";
    this.refusal = refusal;
    this.value = value;
  }
}

export type OrdinalWitnessValidationResult =
  | { readonly ok: true }
  | {
      readonly failure: { readonly code: "ORDINAL_WITNESS_INVARIANT" };
      readonly ok: false;
    };

export type DigestMatchResult =
  | { readonly matches: boolean; readonly ok: true }
  | {
      readonly failure: { readonly code: "DIGEST_FORM" };
      readonly ok: false;
    };

interface DataMember {
  readonly key: string;
  readonly value: unknown;
}

function isPlainObject(value: object): value is Record<PropertyKey, unknown> {
  const prototype: unknown = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

function assertScalarString(value: string): void {
  for (const character of value) {
    const codePoint = character.codePointAt(0);
    if (codePoint !== undefined && codePoint >= 0xd800 && codePoint <= 0xdfff) {
      throw new CanonicalizationError("LONE_SURROGATE", value);
    }
  }
}

function escapeCharacter(character: string): string {
  switch (character) {
    case '"':
      return '\\"';
    case "\\":
      return "\\\\";
    case "\b":
      return "\\b";
    case "\f":
      return "\\f";
    case "\n":
      return "\\n";
    case "\r":
      return "\\r";
    case "\t":
      return "\\t";
    default: {
      const codePoint = character.charCodeAt(0);
      return codePoint < 0x20 ? `\\u${codePoint.toString(16).padStart(4, "0")}` : character;
    }
  }
}

function encodeString(value: string): string {
  assertScalarString(value);
  const normalized = value.normalize("NFC");
  if (textEncoder.encode(normalized).byteLength > MAX_CANONICAL_STRING_BYTES) {
    throw new CanonicalizationError("STRING_LENGTH", value);
  }
  return `"${Array.from(normalized, escapeCharacter).join("")}"`;
}

function dataDescriptorValue(key: string, descriptor: PropertyDescriptor): unknown {
  if (descriptor.enumerable !== true || !("value" in descriptor)) {
    throw new CanonicalizationError("PROPERTY_FORM", key);
  }
  return descriptor.value as unknown;
}

function snapshotPlainObject(value: Record<PropertyKey, unknown>): DataMember[] {
  const descriptors = Object.getOwnPropertyDescriptors(value);
  const symbolKeys = Object.getOwnPropertySymbols(descriptors);
  if (symbolKeys.length > 0) {
    throw new CanonicalizationError("KEY_FORM", symbolKeys[0]);
  }
  return Object.keys(descriptors).map((key) => {
    const descriptor = descriptors[key] as PropertyDescriptor;
    return { key, value: dataDescriptorValue(key, descriptor) };
  });
}

function validatedObjectMembers(members: readonly DataMember[]): DataMember[] {
  for (const { key } of members) {
    if (key.length > MAX_CANONICAL_KEY_CHARACTERS) {
      throw new CanonicalizationError("KEY_LENGTH", key);
    }
    if (!CANONICAL_KEY_PATTERN.test(key)) {
      throw new CanonicalizationError("KEY_FORM", key);
    }
  }
  return [...members].sort(({ key: left }, { key: right }) => {
    if (left < right) {
      return -1;
    }
    return left > right ? 1 : 0;
  });
}

function snapshotDenseArray(value: unknown[]): unknown[] {
  const descriptors = Object.getOwnPropertyDescriptors(value);
  const symbolKeys = Object.getOwnPropertySymbols(descriptors);
  if (symbolKeys.length > 0) {
    throw new CanonicalizationError("KEY_FORM", symbolKeys[0]);
  }
  const lengthDescriptor = descriptors.length as PropertyDescriptor;
  const length = lengthDescriptor.value as number;
  const keys = Object.keys(descriptors);
  if (keys.length !== length + 1) {
    throw new CanonicalizationError("ARRAY_FORM", value);
  }
  const items: unknown[] = [];
  for (let index = 0; index < length; index += 1) {
    const key = String(index);
    const descriptor = descriptors[key];
    if (descriptor === undefined) {
      throw new CanonicalizationError("ARRAY_FORM", value);
    }
    items.push(dataDescriptorValue(key, descriptor));
  }
  return items;
}

function encodeContainer(
  value: unknown[] | Record<PropertyKey, unknown>,
  activeContainers: WeakSet<object>,
): string {
  if (activeContainers.has(value)) {
    throw new CanonicalizationError("CYCLE", value);
  }
  activeContainers.add(value);
  try {
    if (Array.isArray(value)) {
      const encodedItems: string[] = [];
      for (const item of snapshotDenseArray(value)) {
        encodedItems.push(encodeValue(item, activeContainers));
      }
      return `[${encodedItems.join(",")}]`;
    }
    const encodedMembers = validatedObjectMembers(snapshotPlainObject(value)).map(
      ({ key, value: memberValue }) =>
        `${encodeString(key)}:${encodeValue(memberValue, activeContainers)}`,
    );
    return `{${encodedMembers.join(",")}}`;
  } finally {
    activeContainers.delete(value);
  }
}

function encodeNumber(value: number): string {
  if (!Number.isFinite(value) || !Number.isInteger(value)) {
    throw new CanonicalizationError("UNSUPPORTED_TYPE", value);
  }
  if (!Number.isSafeInteger(value)) {
    throw new CanonicalizationError("INTEGER_RANGE", value);
  }
  return String(value);
}

function encodeValue(value: unknown, activeContainers: WeakSet<object>): string {
  if (value === null) {
    return "null";
  }
  switch (typeof value) {
    case "boolean":
      return String(value);
    case "number":
      return encodeNumber(value);
    case "string":
      return encodeString(value);
    case "object":
      if (Array.isArray(value) || isPlainObject(value)) {
        return encodeContainer(value, activeContainers);
      }
      break;
    default:
      break;
  }
  throw new CanonicalizationError("UNSUPPORTED_TYPE", value);
}

/**
 * Encode an already-decoded canonical value. Raw JSON callers must canonical-decode first because
 * JavaScript cannot distinguish an integer token from a lexical real such as `1.0` after parsing.
 */
export function canonicalBytes(value: unknown): Uint8Array {
  return textEncoder.encode(encodeValue(value, new WeakSet<object>()));
}

function digestCoveredDocument(value: unknown): Record<PropertyKey, unknown> {
  if (
    value === null ||
    typeof value !== "object" ||
    Array.isArray(value) ||
    !isPlainObject(value)
  ) {
    throw new DigestError("NOT_AN_OBJECT", value);
  }
  const members = snapshotPlainObject(value);
  const covered = Object.create(null) as Record<string, unknown>;
  let version: unknown;
  for (const member of members) {
    if (member.key === "canonicalizationVersion") {
      version = member.value;
    }
    if (member.key !== "digest") {
      covered[member.key] = member.value;
    }
  }
  if (
    typeof version !== "number" ||
    !Number.isInteger(version) ||
    version !== CANONICALIZATION_VERSION
  ) {
    throw new DigestError("VERSION", version);
  }
  return covered;
}

function digestMaterial(context: DigestContext, value: unknown): Uint8Array<ArrayBuffer> {
  const prefix = textEncoder.encode(`${DIGEST_PREFIX}\n${context}\n`);
  const document = canonicalBytes(digestCoveredDocument(value));
  const material = new Uint8Array(prefix.byteLength + document.byteLength);
  material.set(prefix);
  material.set(document, prefix.byteLength);
  return material;
}

function lowercaseHexadecimal(value: ArrayBuffer): string {
  return Array.from(new Uint8Array(value), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export async function digestDocument(context: DigestContext, value: unknown): Promise<string> {
  const digest = await globalThis.crypto.subtle.digest("SHA-256", digestMaterial(context, value));
  return lowercaseHexadecimal(digest);
}

export async function replayStateDigest(state: DashboardReducedState): Promise<string> {
  return digestDocument("replay-state", state);
}

export async function orderedDashboardEventDigest(
  orderedEvent: OrderedDashboardEvent,
): Promise<string> {
  return digestDocument("ordered-dashboard-event", {
    auditOrdinal: orderedEvent.auditOrdinal,
    canonicalizationVersion: CANONICALIZATION_VERSION,
    event: orderedEvent.event,
  });
}

export function digestMatches(expected: unknown, actual: unknown): DigestMatchResult {
  if (
    typeof expected !== "string" ||
    typeof actual !== "string" ||
    !LOWERCASE_SHA256_PATTERN.test(expected) ||
    !LOWERCASE_SHA256_PATTERN.test(actual)
  ) {
    return { failure: { code: "DIGEST_FORM" }, ok: false };
  }
  let difference = 0;
  for (let index = 0; index < 64; index += 1) {
    difference |= expected.charCodeAt(index) ^ actual.charCodeAt(index);
  }
  return { matches: difference === 0, ok: true };
}

export function validateOrdinalWitness(
  latestAuditOrdinal: number,
  latestEventDigest: string | null,
): OrdinalWitnessValidationResult {
  if ((latestAuditOrdinal === 0) === (latestEventDigest === null)) {
    return { ok: true };
  }
  return {
    failure: { code: "ORDINAL_WITNESS_INVARIANT" },
    ok: false,
  };
}
