import { expect, test } from "vitest";

import { consumeDashboardBootstrapElement } from "./bootstrap-element";

test("removes and validates the transient server bootstrap before returning it", () => {
  // Arrange
  document.body.innerHTML = `
    <script type="application/json" data-dashboard-bootstrap>
      {"bearer":"runtime-bearer-synthetic-0001","bootstrapVersion":"dashboard-bootstrap/v1","runtimeId":"runtime-synthetic-0001"}
    </script>
  `;

  // Act
  const result = consumeDashboardBootstrapElement(document);

  // Assert
  expect(result).toEqual({
    ok: true,
    value: {
      bearer: "runtime-bearer-synthetic-0001",
      bootstrapVersion: "dashboard-bootstrap/v1",
      runtimeId: "runtime-synthetic-0001",
    },
  });
  expect(document.querySelector("[data-dashboard-bootstrap]")).toBeNull();
  expect(document.body.textContent).not.toContain("runtime-bearer-synthetic-0001");
});

test("removes malformed bootstrap bytes and fails closed", () => {
  // Arrange
  document.body.innerHTML = `
    <script type="application/json" data-dashboard-bootstrap>
      {"bearer":"runtime-bearer-synthetic-0001",
    </script>
  `;

  // Act
  const result = consumeDashboardBootstrapElement(document);

  // Assert
  expect(result).toEqual({ ok: false, reason: "BOOTSTRAP_REFUSED" });
  expect(document.querySelector("[data-dashboard-bootstrap]")).toBeNull();
  expect(document.body.textContent).not.toContain("runtime-bearer-synthetic-0001");
});

test("reports a missing bootstrap without reading another script element", () => {
  // Arrange
  document.body.innerHTML = '<script type="application/json">{"bearer":"unrelated"}</script>';

  // Act
  const result = consumeDashboardBootstrapElement(document);

  // Assert
  expect(result).toEqual({ ok: false, reason: "BOOTSTRAP_MISSING" });
  expect(document.querySelector("script")?.textContent).toContain("unrelated");
});
