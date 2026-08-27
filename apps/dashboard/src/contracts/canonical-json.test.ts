import { expect, test } from "vitest";

import { decodeCanonicalJson } from "./bootstrap";

test("canonical-decodes a generic dashboard document before schema narrowing", () => {
  // Arrange
  const raw = '{"bundleVersion":"dashboard-replay-bundle/v1","events":[],"scenarioRevision":1}';

  // Act
  const result = decodeCanonicalJson(raw);

  // Assert
  expect(result).toEqual({
    ok: true,
    value: {
      bundleVersion: "dashboard-replay-bundle/v1",
      events: [],
      scenarioRevision: 1,
    },
  });
});

test("refuses a generic document before a floating-point token loses its lexical form", () => {
  // Arrange
  const raw = '{"scenarioRevision":1.0}';

  // Act
  const result = decodeCanonicalJson(raw);

  // Assert
  expect(result).toEqual({ failure: { code: "CANONICAL_PROFILE_REFUSED" }, ok: false });
});
