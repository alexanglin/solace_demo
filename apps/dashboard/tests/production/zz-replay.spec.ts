import { expect, test } from "@playwright/test";

test("reproduces one final digest across ten replay folds", async ({ page }) => {
  // Arrange
  const replayRequests: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (request.method() === "GET" && url.pathname.startsWith("/api/v1/replays/")) {
      replayRequests.push(url.pathname);
    }
  });
  await page.goto("/");
  await page.getByRole("radio", { name: "Isolated replay" }).click();
  await page.getByRole("button", { name: "Start replay" }).click();
  const controls = page.getByRole("region", { name: "Replay controls" });
  await controls.waitFor({ state: "visible", timeout: 30_000 });
  const progress = controls.getByRole("slider", { name: "Replay progress" });
  const end = await progress.getAttribute("max");
  if (end === null) throw new Error("validated replay progress has no maximum");
  const observedDigests: string[] = [];
  const replayResetPosts: string[] = [];
  page.on("request", (request) => {
    if (
      request.method() === "POST" &&
      new URL(request.url()).pathname === "/api/v1/scenarios/current/reset"
    ) {
      replayResetPosts.push(new URL(request.url()).pathname);
    }
  });

  // Act
  await page.reload();
  await controls.waitFor({ state: "visible", timeout: 30_000 });
  await controls.getByRole("button", { name: "Step forward" }).click();
  await controls.getByRole("button", { name: "2×" }).click();
  await controls.getByRole("button", { name: "Play replay" }).click();
  await controls.getByRole("button", { name: "Pause replay" }).click();
  for (let run = 0; run < 10; run += 1) {
    await progress.fill(end);
    observedDigests.push(
      (await page
        .getByRole("status", { name: "Current mission digest", exact: true })
        .textContent()) ?? "",
    );
    if (run < 9) await controls.getByRole("button", { name: "Restart replay" }).click();
  }
  await page.getByRole("button", { name: "New replay session" }).click();
  const dialog = page.getByRole("dialog", { name: "Start a new replay session" });
  const resetConsequences = (await dialog.textContent()) ?? "";
  await dialog.getByRole("button", { name: "Create new replay session" }).click();
  await page.waitForFunction(
    () => {
      const slider = document.querySelector('input[aria-label="Replay progress"]');
      return slider instanceof HTMLInputElement && slider.value === "0";
    },
    undefined,
    { timeout: 30_000 },
  );
  const resetCursor = await progress.inputValue();
  await progress.fill(end);

  // Assert
  expect(replayRequests).toHaveLength(3);
  expect(replayRequests[1]).toBe(replayRequests[0]);
  expect(replayRequests[2]).not.toBe(replayRequests[0]);
  expect(new Set(observedDigests).size).toBe(1);
  expect(observedDigests[0]).toMatch(/^[0-9a-f]{64}$/);
  await expect(page.getByRole("status", { name: "Replay digest verification" })).toHaveText(
    "Verified",
  );
  expect(resetConsequences).toMatch(/fresh cursor-zero replay session/i);
  expect(resetConsequences).toMatch(/does not mutate an operational mission/i);
  expect(replayResetPosts).toEqual(["/api/v1/scenarios/current/reset"]);
  expect(resetCursor).toBe("0");
});
