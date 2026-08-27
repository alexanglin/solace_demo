import { expect, test } from "vitest";

import { parseDashboardBootstrap } from "./bootstrap";

interface BootstrapRefusalCase {
  readonly expectedCode:
    "CANONICAL_PROFILE_REFUSED" | "MALFORMED_JSON" | "SCHEMA_VALIDATION_FAILED";
  readonly label: string;
  readonly raw: string;
}

const sensitiveBearer = "sensitive-bootstrap-bearer-must-not-escape";

const bootstrapRefusalCases: readonly BootstrapRefusalCase[] = [
  {
    expectedCode: "MALFORMED_JSON",
    label: "malformed JSON",
    raw: `{"bootstrapVersion":"dashboard-bootstrap/v1","bearer":"${sensitiveBearer}"`,
  },
  {
    expectedCode: "SCHEMA_VALIDATION_FAILED",
    label: "an unknown member",
    raw: JSON.stringify({
      bearer: sensitiveBearer,
      bootstrapVersion: "dashboard-bootstrap/v1",
      runtimeId: "runtime-synthetic-0001",
      unexpected: true,
    }),
  },
  {
    expectedCode: "CANONICAL_PROFILE_REFUSED",
    label: "a duplicate key",
    raw: `{"bootstrapVersion":"dashboard-bootstrap/v1","bearer":"${sensitiveBearer}","runtimeId":"runtime-first","runtimeId":"runtime-second"}`,
  },
  {
    expectedCode: "CANONICAL_PROFILE_REFUSED",
    label: "a floating-point value",
    raw: `{"bootstrapVersion":"dashboard-bootstrap/v1","bearer":"${sensitiveBearer}","runtimeId":"runtime-synthetic-0001","unexpected":1.5}`,
  },
  {
    expectedCode: "CANONICAL_PROFILE_REFUSED",
    label: "an unpaired surrogate",
    raw: `{"bootstrapVersion":"dashboard-bootstrap/v1","bearer":"${sensitiveBearer}","runtimeId":"runtime-\\ud800"}`,
  },
];

test.each(bootstrapRefusalCases)(
  "refuses bootstrap containing $label and redacts the raw candidate",
  ({ expectedCode, raw }) => {
    // Arrange
    const rawCandidate = raw;

    // Act
    const result = parseDashboardBootstrap(rawCandidate);

    // Assert
    expect(result).toMatchObject({ failure: { code: expectedCode }, ok: false });
    expect(JSON.stringify(result)).not.toContain(sensitiveBearer);
    expect(JSON.stringify(result)).not.toContain(rawCandidate);
  },
);
