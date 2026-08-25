import { expect, test } from "vitest";

import { CanonicalizationError, canonicalBytes } from "./canonical";

interface CanonicalRefusalCase {
  readonly expectedRefusal:
    | "ARRAY_FORM"
    | "CYCLE"
    | "INTEGER_RANGE"
    | "KEY_FORM"
    | "KEY_LENGTH"
    | "LONE_SURROGATE"
    | "PROPERTY_FORM"
    | "STRING_LENGTH"
    | "UNSUPPORTED_TYPE";
  readonly label: string;
  readonly value: unknown;
}

function canonicalRefusalOf(value: unknown): string {
  try {
    canonicalBytes(value);
  } catch (error: unknown) {
    if (error instanceof CanonicalizationError) {
      return error.refusal;
    }
    throw error;
  }
  throw new Error("canonical value was accepted");
}

function cyclicObject(): Record<string, unknown> {
  const value: Record<string, unknown> = {};
  value["self"] = value;
  return value;
}

function cyclicArray(): unknown[] {
  const value: unknown[] = [];
  value.push(value);
  return value;
}

function inheritedIndexArray(): unknown[] {
  const value = new Array<unknown>(1);
  const prototype = {};
  Object.setPrototypeOf(prototype, Array.prototype);
  Object.defineProperty(prototype, "0", { enumerable: true, value: "inherited" });
  Object.setPrototypeOf(value, prototype);
  return value;
}

function arrayWithExtraProperty(): unknown[] {
  const value: unknown[] = ["first"];
  Object.defineProperty(value, "extra", { enumerable: true, value: "not-an-index" });
  return value;
}

function arrayWithHoleAndCompensatingProperty(): unknown[] {
  const value = new Array<unknown>(1);
  Object.defineProperty(value, "extra", { enumerable: true, value: "not-an-index" });
  return value;
}

function arrayWithSymbolProperty(): unknown[] {
  const value: unknown[] = ["first"];
  Object.defineProperty(value, Symbol("hidden"), { enumerable: true, value: "not-an-index" });
  return value;
}

function arrayWithShadowedMap(): unknown[] {
  const value: unknown[] = ["first"];
  Object.defineProperty(value, "map", { enumerable: true, value: null });
  return value;
}

function arrayWithNonEnumerableIndex(): unknown[] {
  const value: unknown[] = ["first"];
  Object.defineProperty(value, "0", { enumerable: false, value: "first" });
  return value;
}

interface ArrayRefusalCase {
  readonly expectedRefusal: "ARRAY_FORM" | "KEY_FORM" | "PROPERTY_FORM";
  readonly label: string;
  readonly value: unknown[];
}

const arrayRefusalCases: readonly ArrayRefusalCase[] = [
  {
    expectedRefusal: "ARRAY_FORM",
    label: "a sparse array",
    value: new Array<unknown>(1),
  },
  {
    expectedRefusal: "ARRAY_FORM",
    label: "a sparse array with a compensating extra property",
    value: arrayWithHoleAndCompensatingProperty(),
  },
  {
    expectedRefusal: "ARRAY_FORM",
    label: "an inherited array index",
    value: inheritedIndexArray(),
  },
  {
    expectedRefusal: "ARRAY_FORM",
    label: "an extra array property",
    value: arrayWithExtraProperty(),
  },
  {
    expectedRefusal: "KEY_FORM",
    label: "a symbol array property",
    value: arrayWithSymbolProperty(),
  },
  {
    expectedRefusal: "ARRAY_FORM",
    label: "a shadowed map property",
    value: arrayWithShadowedMap(),
  },
  {
    expectedRefusal: "PROPERTY_FORM",
    label: "a non-enumerable array index",
    value: arrayWithNonEnumerableIndex(),
  },
];

const canonicalRefusalCases: readonly CanonicalRefusalCase[] = [
  {
    expectedRefusal: "UNSUPPORTED_TYPE",
    label: "a fractional floating-point number",
    value: 1.5,
  },
  {
    expectedRefusal: "UNSUPPORTED_TYPE",
    label: "not-a-number",
    value: Number.NaN,
  },
  {
    expectedRefusal: "UNSUPPORTED_TYPE",
    label: "positive infinity",
    value: Number.POSITIVE_INFINITY,
  },
  {
    expectedRefusal: "INTEGER_RANGE",
    label: "an integer above the exact JavaScript range",
    value: Number.MAX_SAFE_INTEGER + 1,
  },
  {
    expectedRefusal: "INTEGER_RANGE",
    label: "an integer below the exact JavaScript range",
    value: Number.MIN_SAFE_INTEGER - 1,
  },
  {
    expectedRefusal: "UNSUPPORTED_TYPE",
    label: "undefined",
    value: undefined,
  },
  {
    expectedRefusal: "UNSUPPORTED_TYPE",
    label: "a bigint",
    value: 1n,
  },
  {
    expectedRefusal: "UNSUPPORTED_TYPE",
    label: "a Date object",
    value: new Date("2026-08-24T12:00:00.000Z"),
  },
  {
    expectedRefusal: "UNSUPPORTED_TYPE",
    label: "a Map object",
    value: new Map<string, number>([["first", 1]]),
  },
  {
    expectedRefusal: "UNSUPPORTED_TYPE",
    label: "a Set object",
    value: new Set<number>([1]),
  },
  {
    expectedRefusal: "UNSUPPORTED_TYPE",
    label: "a typed array",
    value: new Uint8Array([1]),
  },
  {
    expectedRefusal: "KEY_FORM",
    label: "an empty object key",
    value: { "": 1 },
  },
  {
    expectedRefusal: "KEY_FORM",
    label: "an uppercase object key",
    value: { MissionId: "mission-synthetic-0001" },
  },
  {
    expectedRefusal: "KEY_FORM",
    label: "a hyphenated object key",
    value: { "mission-id": "mission-synthetic-0001" },
  },
  {
    expectedRefusal: "KEY_FORM",
    label: "a symbol object key",
    value: { [Symbol("hidden")]: 1 },
  },
  {
    expectedRefusal: "KEY_LENGTH",
    label: "a 65-character object key",
    value: { ["a".repeat(65)]: 1 },
  },
  {
    expectedRefusal: "STRING_LENGTH",
    label: "a string above the 4096-byte UTF-8 bound",
    value: "🚁".repeat(1025),
  },
  {
    expectedRefusal: "LONE_SURROGATE",
    label: "a lone high surrogate",
    value: "\ud800",
  },
  {
    expectedRefusal: "LONE_SURROGATE",
    label: "a lone low surrogate",
    value: "\udfff",
  },
  {
    expectedRefusal: "CYCLE",
    label: "a cyclic object",
    value: cyclicObject(),
  },
  {
    expectedRefusal: "CYCLE",
    label: "a cyclic array",
    value: cyclicArray(),
  },
];

test("emits NFC-normalized, minimally escaped UTF-8 bytes in canonical key order", () => {
  // Arrange
  const value = {
    zulu: ["e\u0301", '"/\\\b\f\n\r\t\u0000\u001f/'],
    alpha: null,
  };
  const expectedText = '{"alpha":null,"zulu":["é","\\"/\\\\\\b\\f\\n\\r\\t\\u0000\\u001f/"]}';

  // Act
  const encoded = canonicalBytes(value);

  // Assert
  expect(encoded).toEqual(new TextEncoder().encode(expectedText));
  expect(new TextDecoder().decode(encoded)).toBe(expectedText);
});

test("emits exact lowercase canonical bytes for both booleans", () => {
  // Arrange
  const value = [true, false];
  const expectedText = "[true,false]";

  // Act
  const encoded = canonicalBytes(value);

  // Assert
  expect(encoded).toEqual(new TextEncoder().encode(expectedText));
  expect(new TextDecoder().decode(encoded)).toBe(expectedText);
});

test("accepts the exact integer, object-key, and UTF-8 string bounds", () => {
  // Arrange
  const key = "a".repeat(64);
  const value = {
    [key]: "a".repeat(4096),
    maximum: Number.MAX_SAFE_INTEGER,
    minimum: Number.MIN_SAFE_INTEGER,
  };

  // Act
  const encoded = new TextDecoder().decode(canonicalBytes(value));

  // Assert
  expect(encoded).toContain(`"${key}":"${"a".repeat(4096)}"`);
  expect(encoded).toContain('"maximum":9007199254740991');
  expect(encoded).toContain('"minimum":-9007199254740991');
});

test.each(canonicalRefusalCases)(
  "returns the structured $expectedRefusal refusal for $label",
  ({ expectedRefusal, value }) => {
    // Arrange
    const candidate = value;

    // Act
    const refusal = canonicalRefusalOf(candidate);

    // Assert
    expect(refusal).toBe(expectedRefusal);
  },
);

test.each(arrayRefusalCases)(
  "returns the structured $expectedRefusal refusal for $label",
  ({ expectedRefusal, value }) => {
    // Arrange
    const candidate = value;

    // Act
    const refusal = canonicalRefusalOf(candidate);

    // Assert
    expect(refusal).toBe(expectedRefusal);
  },
);

test("refuses an accessor array index without invoking its getter", () => {
  // Arrange
  const value: unknown[] = ["first"];
  let getterCalls = 0;
  Object.defineProperty(value, "0", {
    enumerable: true,
    get: () => {
      getterCalls += 1;
      return "observed";
    },
  });

  // Act
  const refusal = canonicalRefusalOf(value);

  // Assert
  expect(refusal).toBe("PROPERTY_FORM");
  expect(getterCalls).toBe(0);
});

test("refuses an accessor object member without invoking its getter", () => {
  // Arrange
  const value: Record<string, unknown> = {};
  let getterCalls = 0;
  Object.defineProperty(value, "missionId", {
    enumerable: true,
    get: () => {
      getterCalls += 1;
      return "mission-synthetic-0001";
    },
  });

  // Act
  const refusal = canonicalRefusalOf(value);

  // Assert
  expect(refusal).toBe("PROPERTY_FORM");
  expect(getterCalls).toBe(0);
});

test("refuses a non-enumerable object member as a structured property-form error", () => {
  // Arrange
  const value: Record<string, unknown> = {};
  Object.defineProperty(value, "missionId", {
    enumerable: false,
    value: "mission-synthetic-0001",
  });

  // Act
  const refusal = canonicalRefusalOf(value);

  // Assert
  expect(refusal).toBe("PROPERTY_FORM");
  expect(Object.getOwnPropertyDescriptor(value, "missionId")?.enumerable).toBe(false);
});
