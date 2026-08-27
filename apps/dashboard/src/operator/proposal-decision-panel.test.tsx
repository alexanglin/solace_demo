import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

import {
  evidenceFixture as evidence,
  PROPOSAL_DIGEST,
  proposalFixture as proposal,
  type EvidenceEvent,
} from "../../tests/unit-support/proposal-fixtures";
import { ProposalDecisionPanel } from "./proposal-decision-panel";
import type { ProposalDecisionSubmitter } from "./mutation-client";

afterEach(() => {
  cleanup();
});

test("presents the exact proposal and requires consequence confirmation before approval", async () => {
  // Arrange
  const user = userEvent.setup();
  const submit = vi.fn<ProposalDecisionSubmitter>().mockResolvedValue({
    ok: true,
    response: {
      operationVersion: "dashboard-proposal-decision-response/v1",
      missionId: "mission-synthetic-0001",
      proposalId: "proposal-synthetic-0001",
      approvalId: "approval-synthetic-0001",
      eventId: "event-approval-synthetic-0001",
      decision: "approve",
      issuedAt: "2026-08-25T12:05:00.000Z",
      expiresAt: "2026-08-25T12:06:00.000Z",
    },
  });
  render(
    <ProposalDecisionPanel
      decisionRecorded={false}
      evidence={evidence()}
      mode="degradedLive"
      proposal={proposal()}
      sourceState="connected"
      submit={submit}
    />,
  );

  // Act
  const approve = await screen.findByRole("button", { name: "Approve exact rescue proposal" });
  await waitFor(() => {
    if ((approve as HTMLButtonElement).disabled) {
      throw new Error("approval binding is not ready");
    }
  });
  await user.click(approve);
  const dialog = screen.getByRole("dialog", { name: "Confirm proposal decision" });
  await user.click(screen.getByRole("button", { name: "Confirm approval" }));

  // Assert
  expect(screen.getByText("proposal-synthetic-0001")).toBeTruthy();
  expect(screen.getByText(PROPOSAL_DIGEST)).toBeTruthy();
  expect(screen.getByText("decision-synthetic-0001")).toBeTruthy();
  expect(screen.getByText("Proposed action").parentElement?.textContent).toContain(
    "45.123456, -75.123456",
  );
  expect(dialog.textContent).toContain("authorize one rescue-escalation command");
  await waitFor(() => {
    expect(submit).toHaveBeenCalledOnce();
  });
  expect(submit.mock.calls[0]?.[0]).toMatchObject({
    decision: "approve",
    evidenceDecisionId: "decision-synthetic-0001",
    proposalId: "proposal-synthetic-0001",
  });
  expect(
    (await screen.findByRole("status", { name: "Proposal decision status" })).textContent,
  ).toContain("Durably accepted; awaiting validated broker events");
});

test("disables both decisions while one exact rejection remains pending", async () => {
  // Arrange
  const user = userEvent.setup();
  let release: (() => void) | undefined;
  const pending = new Promise<Awaited<ReturnType<ProposalDecisionSubmitter>>>((resolve) => {
    release = () => {
      resolve({ ok: false, reason: "TRANSPORT_AMBIGUOUS" });
    };
  });
  const submit = vi.fn<ProposalDecisionSubmitter>().mockReturnValue(pending);
  render(
    <ProposalDecisionPanel
      decisionRecorded={false}
      evidence={evidence()}
      mode="degradedLive"
      proposal={proposal()}
      sourceState="connected"
      submit={submit}
    />,
  );
  const reject = await screen.findByRole("button", { name: "Reject exact rescue proposal" });
  await waitFor(() => {
    if ((reject as HTMLButtonElement).disabled) {
      throw new Error("rejection binding is not ready");
    }
  });

  // Act
  await user.click(reject);
  const confirm = screen.getByRole("button", { name: "Confirm rejection" });
  await user.click(confirm);
  await user.click(confirm);

  // Assert
  expect(submit).toHaveBeenCalledOnce();
  expect(screen.getByRole("button", { name: "Approve exact rescue proposal" })).toHaveProperty(
    "disabled",
    true,
  );
  expect(screen.getByRole("button", { name: "Reject exact rescue proposal" })).toHaveProperty(
    "disabled",
    true,
  );
  release?.();
  expect((await screen.findByRole("alert")).textContent).toContain(
    "The result is ambiguous. Inspect refreshed broker facts before deciding again.",
  );
});

test.each(["offline", "retrying", "exhausted"] as const)(
  "keeps the exact binding visible but disables mutation while the source is %s",
  async (sourceState) => {
    // Arrange
    const submit = vi.fn<ProposalDecisionSubmitter>();

    // Act
    render(
      <ProposalDecisionPanel
        decisionRecorded={false}
        evidence={evidence()}
        mode="degradedLive"
        proposal={proposal()}
        sourceState={sourceState}
        submit={submit}
      />,
    );
    const approve = await screen.findByRole("button", { name: "Approve exact rescue proposal" });

    // Assert
    expect((approve as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByRole("button", { name: "Reject exact rescue proposal" })).toHaveProperty(
      "disabled",
      true,
    );
    expect(submit).not.toHaveBeenCalled();
  },
);

test("renders recorded proposal facts in replay without constructing action controls", () => {
  // Arrange
  const selectedProposal = proposal();

  // Act
  render(
    <ProposalDecisionPanel
      decisionRecorded={false}
      evidence={evidence()}
      mode="replay"
      proposal={selectedProposal}
      sourceState="connected"
    />,
  );

  // Assert
  expect(screen.getByText("Recorded proposal — replay is read only")).toBeTruthy();
  expect(screen.queryByRole("button", { name: /proposal/iu })).toBeNull();
  expect(selectedProposal.data.proposalId).toBe("proposal-synthetic-0001");
});

test("cancels an exact recovered-source approval without submitting it", async () => {
  // Arrange
  const user = userEvent.setup();
  const submit = vi.fn<ProposalDecisionSubmitter>();
  render(
    <ProposalDecisionPanel
      decisionRecorded={false}
      evidence={evidence()}
      mode="degradedLive"
      proposal={proposal()}
      sourceState="recovered"
      submit={submit}
    />,
  );
  const approve = await screen.findByRole("button", { name: "Approve exact rescue proposal" });
  await waitFor(() => {
    if ((approve as HTMLButtonElement).disabled) {
      throw new Error("recovered binding is not ready");
    }
  });

  // Act
  await user.click(approve);
  await user.click(screen.getByRole("button", { name: "Cancel" }));

  // Assert
  expect(screen.queryByRole("dialog", { name: "Confirm proposal decision" })).toBeNull();
  expect(submit).not.toHaveBeenCalled();
  expect((approve as HTMLButtonElement).disabled).toBe(false);
});

test("explains why weak single-source evidence cannot be approved", async () => {
  // Arrange
  const weakEvidence = evidence();
  if (weakEvidence.data.outcome !== "contributing") {
    throw new Error("evidence fixture must contribute");
  }
  weakEvidence.data.band = "weak";
  weakEvidence.data.score = 40;
  const contributor = weakEvidence.data.contributors[0];
  if (contributor === undefined) {
    throw new Error("evidence fixture must contain a contributor");
  }
  weakEvidence.data.contributors = [contributor];

  // Act
  render(
    <ProposalDecisionPanel
      decisionRecorded={false}
      evidence={weakEvidence}
      mode="degradedLive"
      proposal={proposal()}
      sourceState="connected"
      submit={vi.fn<ProposalDecisionSubmitter>()}
    />,
  );
  const approve = await screen.findByRole("button", { name: "Approve exact rescue proposal" });
  const guidance = await screen.findByText(
    "Approval requires a corroborated contributing evidence decision.",
  );

  // Assert
  expect((approve as HTMLButtonElement).disabled).toBe(true);
  expect(guidance).toBeTruthy();
  expect(screen.getByText(/contributing · weak · score 40/iu)).toBeTruthy();
});

test("labels a noncontributing recorded evidence outcome without action controls", () => {
  // Arrange
  const abstention: EvidenceEvent = {
    ...evidence(),
    data: {
      canonicalizationVersion: 1,
      evidenceDecisionVersion: 1,
      proposalId: "proposal-synthetic-0001",
      proposalDigest: PROPOSAL_DIGEST,
      proposalVersion: 1,
      evidenceDecisionId: "decision-synthetic-0002",
      outcome: "abstained",
      reason: "declined",
    },
  };

  // Act
  render(
    <ProposalDecisionPanel
      decisionRecorded={true}
      evidence={abstention}
      mode="replay"
      proposal={proposal()}
      sourceState="connected"
    />,
  );

  // Assert
  expect(screen.getByText("abstained · declined")).toBeTruthy();
  expect(screen.queryByRole("button")).toBeNull();
});
