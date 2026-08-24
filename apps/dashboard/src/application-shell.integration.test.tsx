import { readFile } from "node:fs/promises";

import { act, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";

test("integrates the production HTML host with the real application entry point", async () => {
  // Arrange
  vi.resetModules();
  const indexHtml = await readFile("index.html", "utf8");
  const parsedDocument = new DOMParser().parseFromString(indexHtml, "text/html");
  document.body.innerHTML = parsedDocument.body.innerHTML;
  delete window.__AERIAL_RESCUE_DASHBOARD_TEST__;

  // Act
  await act(async () => import("./main"));
  const rootElement = document.getElementById("root");
  const banner = screen.getByRole("banner");
  const main = screen.getByRole("main");

  // Assert
  expect(rootElement).toBeInstanceOf(HTMLDivElement);
  expect(rootElement?.children).toHaveLength(2);
  expect(banner.parentElement).toBe(rootElement);
  expect(main.parentElement).toBe(rootElement);
  expect(parsedDocument.querySelector('script[type="module"]')?.getAttribute("src")).toBe(
    "/src/main.tsx",
  );
});
