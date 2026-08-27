import type { DashboardBootstrap } from "./generated";
import {
  createDashboardSchemaRegistry,
  type DashboardSchemaValidationResult,
} from "./schema-registry";

const BOOTSTRAP_SCHEMA_ID =
  "https://aerial-rescue.invalid/schemas/v1/dashboard/bootstrap.schema.json";
const CANONICAL_KEY_PATTERN = /^[a-z][a-zA-Z0-9]*$/u;
const JSON_NUMBER_TOKEN = /^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?/u;
const LONE_SURROGATE_PATTERN = /[\uD800-\uDFFF]/u;
const MAX_CANONICAL_KEY_CHARACTERS = 64;
const MAX_CANONICAL_STRING_BYTES = 4096;
const MAX_SAFE_INTEGER = BigInt(Number.MAX_SAFE_INTEGER);
const MIN_SAFE_INTEGER = -MAX_SAFE_INTEGER;
const textEncoder = new TextEncoder();
const dashboardSchemaRegistry = createDashboardSchemaRegistry();

export interface CanonicalJsonTextFailure {
  readonly code: "CANONICAL_PROFILE_REFUSED" | "MALFORMED_JSON";
}

export type CanonicalJsonDecodeResult =
  | {
      readonly ok: true;
      readonly value: unknown;
    }
  | {
      readonly failure: CanonicalJsonTextFailure;
      readonly ok: false;
    };

export type DashboardBootstrapParseResult =
  | DashboardSchemaValidationResult<DashboardBootstrap>
  | {
      readonly failure: CanonicalJsonTextFailure;
      readonly ok: false;
    };

function stringOutsideCanonicalProfile(value: string): boolean {
  return (
    LONE_SURROGATE_PATTERN.test(value) ||
    textEncoder.encode(value.normalize("NFC")).byteLength > MAX_CANONICAL_STRING_BYTES
  );
}

function keyOutsideCanonicalProfile(value: string): boolean {
  return (
    value.length > MAX_CANONICAL_KEY_CHARACTERS ||
    !CANONICAL_KEY_PATTERN.test(value) ||
    stringOutsideCanonicalProfile(value)
  );
}

function numberOutsideCanonicalProfile(token: string): boolean {
  if (/[.eE]/u.test(token)) {
    return true;
  }
  const value = BigInt(token);
  return value < MIN_SAFE_INTEGER || value > MAX_SAFE_INTEGER;
}

function jsonStringToken(raw: string, start: number): string {
  let end = start + 1;
  while (raw.charAt(end) !== '"') {
    if (raw.charAt(end) === "\\") {
      end += 1;
    }
    end += 1;
  }
  return raw.slice(start, end + 1);
}

function scanCanonicalJson(raw: string): boolean {
  const objectMemberSets: Set<string>[] = [];
  let index = 0;
  while (index < raw.length) {
    const character = raw.charAt(index);
    if (character === '"') {
      const token = jsonStringToken(raw, index);
      const decoded = JSON.parse(token) as string;
      if (stringOutsideCanonicalProfile(decoded)) {
        return true;
      }
      if (/^\s*:/u.test(raw.slice(index + token.length))) {
        const members = objectMemberSets.at(-1);
        if (members === undefined || keyOutsideCanonicalProfile(decoded) || members.has(decoded)) {
          return true;
        }
        members.add(decoded);
      }
      index += token.length;
      continue;
    }
    if (character === "{") {
      objectMemberSets.push(new Set());
    } else if (character === "}") {
      objectMemberSets.pop();
    } else if (character === "-" || /[0-9]/u.test(character)) {
      const token = JSON_NUMBER_TOKEN.exec(raw.slice(index))?.[0];
      if (token === undefined || numberOutsideCanonicalProfile(token)) {
        return true;
      }
      index += token.length;
      continue;
    }
    index += 1;
  }
  return false;
}

export function decodeCanonicalJson(raw: string): CanonicalJsonDecodeResult {
  let decoded: unknown;
  try {
    decoded = JSON.parse(raw) as unknown;
  } catch {
    return { failure: { code: "MALFORMED_JSON" }, ok: false };
  }

  if (scanCanonicalJson(raw)) {
    return { failure: { code: "CANONICAL_PROFILE_REFUSED" }, ok: false };
  }

  return { ok: true, value: decoded };
}

export function parseDashboardBootstrap(raw: string): DashboardBootstrapParseResult {
  const decoded = decodeCanonicalJson(raw);
  if (!decoded.ok) {
    return decoded;
  }

  return dashboardSchemaRegistry.validate(BOOTSTRAP_SCHEMA_ID, decoded.value);
}

export function consumeDashboardBootstrap(
  raw: string,
  consume: (bootstrap: DashboardBootstrap) => void,
): DashboardBootstrapParseResult {
  const result = parseDashboardBootstrap(raw);
  if (result.ok) {
    consume(result.value);
  }
  return result;
}
