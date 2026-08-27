import { expect, type Page } from "@playwright/test";

export function missionPreparationAction(currentMission: string): "reset" | "start" {
  if (currentMission === "No validated live mission") return "start";
  if (currentMission.endsWith(" · PLANNED")) return "start";
  if (/ · (?:ABORTED|EXHAUSTED)$/u.test(currentMission)) return "reset";
  throw new Error("mission was neither prepared nor terminal");
}

export async function selectDegradedLiveMode(page: Page): Promise<void> {
  await expect(page.getByRole("status", { name: "Current mission", exact: true })).not.toHaveText(
    "No current mission",
    { timeout: 30_000 },
  );
  await expect(page.getByRole("status", { name: "Connection" })).toHaveText(
    /^(?:CONNECTED|REPLAY READY)$/u,
    { timeout: 30_000 },
  );
  const liveMode = page.getByRole("radio", { name: "Degraded live simulation" });
  await liveMode.click();
  await expect(page.getByRole("status", { name: "Operating mode" })).toHaveText(
    "DEGRADED LIVE SIMULATION",
  );
  await expect(page.getByRole("status", { name: "Readiness", exact: true })).toHaveText(
    /^(?:READY|UNAVAILABLE)$/u,
  );
}

export async function prepareLiveMissionStart(page: Page): Promise<void> {
  await selectDegradedLiveMode(page);
  const start = page.getByRole("button", { name: "Start wilderness mission" });
  const currentMission = page.getByRole("status", { name: "Current mission", exact: true });
  await expect(page.getByRole("status", { name: "Readiness", exact: true })).toHaveText("READY", {
    timeout: 30_000,
  });
  const action = missionPreparationAction((await currentMission.textContent()) ?? "");
  if (action === "start") {
    await expect(start).toBeEnabled();
    return;
  }
  await page.getByRole("button", { name: "Reset mission" }).click();
  await page
    .getByRole("dialog", { name: "Reset current mission" })
    .getByRole("button", { name: "Confirm reset" })
    .click();
  await expect(currentMission).toHaveText(/ · PLANNED$/u, { timeout: 30_000 });
  await expect(start).toBeEnabled();
}
