import { expect, test } from "vitest";

import { parseDashboardBootstrap } from "./bootstrap";

interface CanonicalBootstrapCase {
  readonly expectedCode?: "CANONICAL_PROFILE_REFUSED" | "SCHEMA_VALIDATION_FAILED";
  readonly label: string;
  readonly raw: string;
}

const canonicalBootstrap = (bearer: string): string =>
  JSON.stringify({
    bearer,
    bootstrapVersion: "dashboard-bootstrap/v1",
    runtimeId: "runtime-synthetic-0001",
  });

const canonicalProfileCases: readonly CanonicalBootstrapCase[] = [
  {
    label: "a paired supplementary Unicode scalar",
    raw: canonicalBootstrap("synthetic-\ud83d\ude80-bearer"),
  },
  {
    label: "an escaped quote and backslash",
    raw: canonicalBootstrap('synthetic-"-\\-bearer'),
  },
  {
    expectedCode: "SCHEMA_VALIDATION_FAILED",
    label: "the largest safe integer",
    raw: '{"bootstrapVersion":"dashboard-bootstrap/v1","bearer":"safe","runtimeId":"runtime-synthetic-0001","unexpected":9007199254740991}',
  },
  {
    expectedCode: "SCHEMA_VALIDATION_FAILED",
    label: "the smallest safe integer",
    raw: '{"bootstrapVersion":"dashboard-bootstrap/v1","bearer":"safe","runtimeId":"runtime-synthetic-0001","unexpected":-9007199254740991}',
  },
  {
    expectedCode: "SCHEMA_VALIDATION_FAILED",
    label: "a 64-character canonical key",
    raw: `{"bootstrapVersion":"dashboard-bootstrap/v1","bearer":"safe","runtimeId":"runtime-synthetic-0001","${"a".repeat(64)}":null}`,
  },
  {
    expectedCode: "SCHEMA_VALIDATION_FAILED",
    label: "nested objects and arrays with distinct keys",
    raw: '{"bootstrapVersion":"dashboard-bootstrap/v1","bearer":"safe","runtimeId":"runtime-synthetic-0001","unexpected":[{"first":true,"second":null}]}',
  },
  {
    expectedCode: "CANONICAL_PROFILE_REFUSED",
    label: "an exponent",
    raw: '{"bootstrapVersion":"dashboard-bootstrap/v1","bearer":"safe","runtimeId":"runtime-synthetic-0001","unexpected":1e0}',
  },
  {
    expectedCode: "CANONICAL_PROFILE_REFUSED",
    label: "an integer above the safe range",
    raw: '{"bootstrapVersion":"dashboard-bootstrap/v1","bearer":"safe","runtimeId":"runtime-synthetic-0001","unexpected":9007199254740992}',
  },
  {
    expectedCode: "CANONICAL_PROFILE_REFUSED",
    label: "an integer below the safe range",
    raw: '{"bootstrapVersion":"dashboard-bootstrap/v1","bearer":"safe","runtimeId":"runtime-synthetic-0001","unexpected":-9007199254740992}',
  },
  {
    expectedCode: "CANONICAL_PROFILE_REFUSED",
    label: "an uppercase object key",
    raw: '{"bootstrapVersion":"dashboard-bootstrap/v1","bearer":"safe","runtimeId":"runtime-synthetic-0001","Unexpected":null}',
  },
  {
    expectedCode: "CANONICAL_PROFILE_REFUSED",
    label: "a 65-character object key",
    raw: `{"bootstrapVersion":"dashboard-bootstrap/v1","bearer":"safe","runtimeId":"runtime-synthetic-0001","${"a".repeat(65)}":null}`,
  },
  {
    expectedCode: "CANONICAL_PROFILE_REFUSED",
    label: "a string above the UTF-8 byte bound",
    raw: canonicalBootstrap("\ud83d\ude80".repeat(1025)),
  },
  {
    expectedCode: "CANONICAL_PROFILE_REFUSED",
    label: "a duplicate nested key",
    raw: '{"bootstrapVersion":"dashboard-bootstrap/v1","bearer":"safe","runtimeId":"runtime-synthetic-0001","unexpected":{"same":1,"same":2}}',
  },
];

test.each(canonicalProfileCases)(
  "classifies bootstrap containing $label at the canonical boundary",
  ({ expectedCode, raw }) => {
    // Arrange
    const candidate = raw;

    // Act
    const result = parseDashboardBootstrap(candidate);

    // Assert
    if (expectedCode === undefined) {
      expect(result).toMatchObject({ ok: true });
    } else {
      expect(result).toMatchObject({ failure: { code: expectedCode }, ok: false });
    }
  },
);
