import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

import {
  approvalFixture as approval,
  evidenceFixture as evidence,
  proposalFixture as proposal,
} from "../../tests/unit-support/proposal-fixtures";
import type { OperatorCommandSubmitter } from "./command-client";
import { RescueEscalationPanel } from "./rescue-escalation-panel";

afterEach(() => {
  cleanup();
});

const ACCEPTED = {
  ok: true,
  response: {
    operationVersion: "dashboard-command-response/v1",
    missionId: "mission-synthetic-0001",
    commandId: "command-synthetic-0001",
    eventId: "event-command-synthetic-0001",
  },
} as const;

async function readyEscalateButton(): Promise<HTMLButtonElement> {
  const button = await screen.findByRole("button", { name: "Dispatch approved rescue escalation" });
  await waitFor(() => {
    if ((button as HTMLButtonElement).disabled) {
      throw new Error("escalation binding is not ready");
    }
  });
  return button as HTMLButtonElement;
}

test("dispatches the approved escalation only after the consequence is confirmed", async () => {
  // Arrange
  const user = userEvent.setup();
  const submit = vi.fn<OperatorCommandSubmitter>().mockResolvedValue(ACCEPTED);
  render(
    <RescueEscalationPanel
      approval={approval()}
      evidence={evidence()}
      mode="degradedLive"
      proposal={proposal()}
      sourceState="connected"
      submit={submit}
    />,
  );

  // Act
  await user.click(await readyEscalateButton());
  screen.getByRole("dialog", { name: "Confirm rescue escalation" });
  await user.click(screen.getByRole("button", { name: "Confirm escalation" }));

  // Assert
  await waitFor(() => {
    expect(submit).toHaveBeenCalledOnce();
  });
  expect(submit).toHaveBeenCalledWith({
    missionId: "mission-synthetic-0001",
    action: {
      commandType: "escalate-rescue",
      droneId: "drone-synthetic-01",
      proposalId: "proposal-synthetic-0001",
      proposalDigest: proposal().data.proposalDigest,
      proposalVersion: 1,
      evidenceDecisionId: "decision-synthetic-0001",
      evidenceDecisionDigest: approval().data.evidenceDecisionDigest,
      evidenceDecisionVersion: 1,
      latitudeMicrodegrees: 45_123_456,
      longitudeMicrodegrees: -75_123_456,
    },
  });
});

test("names the drone and the coordinates the escalation dispatches to", () => {
  // Arrange
  const submit = vi.fn<OperatorCommandSubmitter>().mockResolvedValue(ACCEPTED);

  // Act
  render(
    <RescueEscalationPanel
      approval={approval()}
      evidence={evidence()}
      mode="degradedLive"
      proposal={proposal()}
      sourceState="connected"
      submit={submit}
    />,
  );

  // Assert
  expect(screen.getByText(/drone-synthetic-01/u).textContent).toContain("45.123456");
});

test("offers no escalation for a proposal a human rejected", () => {
  // Arrange
  const submit = vi.fn<OperatorCommandSubmitter>();

  // Act
  render(
    <RescueEscalationPanel
      approval={approval({ decision: "reject" })}
      evidence={evidence()}
      mode="degradedLive"
      proposal={proposal()}
      sourceState="connected"
      submit={submit}
    />,
  );

  // Assert
  expect(screen.queryByRole("button", { name: "Dispatch approved rescue escalation" })).toBeNull();
  expect(submit).not.toHaveBeenCalled();
});

test("constructs no enabled escalation control in replay", () => {
  // Arrange
  const submit = vi.fn<OperatorCommandSubmitter>();

  // Act
  render(
    <RescueEscalationPanel
      approval={approval()}
      evidence={evidence()}
      mode="replay"
      proposal={proposal()}
      sourceState="connected"
      submit={submit}
    />,
  );

  // Assert
  expect(screen.queryByRole("button", { name: "Dispatch approved rescue escalation" })).toBeNull();
  expect(submit).not.toHaveBeenCalled();
});

test("refuses a second dispatch while the first command is still pending", async () => {
  // Arrange
  const user = userEvent.setup();
  let release: ((value: typeof ACCEPTED) => void) | undefined;
  const submit = vi.fn<OperatorCommandSubmitter>().mockReturnValue(
    new Promise((resolve) => {
      release = resolve;
    }),
  );
  render(
    <RescueEscalationPanel
      approval={approval()}
      evidence={evidence()}
      mode="degradedLive"
      proposal={proposal()}
      sourceState="connected"
      submit={submit}
    />,
  );

  // Act
  await user.click(await readyEscalateButton());
  const confirm = screen.getByRole("button", { name: "Confirm escalation" });
  await user.click(confirm);
  await user.click(confirm);
  release?.(ACCEPTED);

  // Assert
  await waitFor(() => {
    expect(submit).toHaveBeenCalledOnce();
  });
});

test("reports a refused dispatch without exposing server prose", async () => {
  // Arrange
  const user = userEvent.setup();
  const submit = vi
    .fn<OperatorCommandSubmitter>()
    .mockResolvedValue({ ok: false, reason: "SERVER_REFUSED" });
  render(
    <RescueEscalationPanel
      approval={approval()}
      evidence={evidence()}
      mode="degradedLive"
      proposal={proposal()}
      sourceState="connected"
      submit={submit}
    />,
  );

  // Act
  await user.click(await readyEscalateButton());
  await user.click(screen.getByRole("button", { name: "Confirm escalation" }));

  // Assert
  expect((await screen.findByRole("alert")).textContent).toContain("refused");
});

test("reports the durable acceptance rather than implying the command already ran", async () => {
  // Arrange
  const user = userEvent.setup();
  const submit = vi.fn<OperatorCommandSubmitter>().mockResolvedValue(ACCEPTED);
  render(
    <RescueEscalationPanel
      approval={approval()}
      evidence={evidence()}
      mode="degradedLive"
      proposal={proposal()}
      sourceState="connected"
      submit={submit}
    />,
  );

  // Act
  await user.click(await readyEscalateButton());
  await user.click(screen.getByRole("button", { name: "Confirm escalation" }));

  // Assert
  expect((await screen.findByLabelText("Rescue escalation status")).textContent).toContain(
    "Durably accepted",
  );
});

test("cancels an open escalation without dispatching it", async () => {
  // Arrange
  const user = userEvent.setup();
  const submit = vi.fn<OperatorCommandSubmitter>();
  render(
    <RescueEscalationPanel
      approval={approval()}
      evidence={evidence()}
      mode="degradedLive"
      proposal={proposal()}
      sourceState="connected"
      submit={submit}
    />,
  );

  // Act
  await user.click(await readyEscalateButton());
  await user.click(screen.getByRole("button", { name: "Cancel" }));

  // Assert
  expect(screen.queryByRole("dialog", { name: "Confirm rescue escalation" })).toBeNull();
  expect(submit).not.toHaveBeenCalled();
});

test("refuses to dispatch when fresh evidence unbinds the open confirmation", async () => {
  // Arrange
  const user = userEvent.setup();
  const submit = vi.fn<OperatorCommandSubmitter>();
  const recorded = approval();
  const selected = proposal();
  const view = render(
    <RescueEscalationPanel
      approval={recorded}
      evidence={evidence()}
      mode="degradedLive"
      proposal={selected}
      sourceState="connected"
      submit={submit}
    />,
  );
  await user.click(await readyEscalateButton());

  // Act
  view.rerender(
    <RescueEscalationPanel
      approval={recorded}
      evidence={{
        ...evidence(),
        data: { ...evidence().data, evidenceDecisionId: "decision-synthetic-other" },
      }}
      mode="degradedLive"
      proposal={selected}
      sourceState="connected"
      submit={submit}
    />,
  );
  await user.click(screen.getByRole("button", { name: "Confirm escalation" }));

  // Assert
  expect((await screen.findByRole("alert")).textContent).toContain("bindings do not match");
  expect(submit).not.toHaveBeenCalled();
});
