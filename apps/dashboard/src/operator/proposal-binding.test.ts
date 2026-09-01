import { expect, test } from "vitest";

import {
  approvalFixture as approvalEvent,
  evidenceFixture as evidenceEvent,
  proposalFixture as proposalEvent,
} from "../../tests/unit-support/proposal-fixtures";
import type { DashboardEvent, OrderedDashboardEvent } from "../contracts/generated";
import { currentProposalBinding } from "./proposal-binding";

function ordered(events: readonly DashboardEvent[]): readonly OrderedDashboardEvent[] {
  return events.map((event, index) => ({ auditOrdinal: index + 1, event }));
}

test("carries the recorded approval so an escalation can be built from it", () => {
  // Arrange
  const approval = approvalEvent();
  const timeline = ordered([proposalEvent(), evidenceEvent(), approval]);

  // Act
  const binding = currentProposalBinding(timeline);

  // Assert
  expect(binding).toEqual({
    approval,
    decisionRecorded: true,
    evidence: evidenceEvent(),
    proposal: proposalEvent(),
  });
});

test("carries a recorded rejection rather than hiding it as no decision", () => {
  // Arrange
  const rejection = approvalEvent({ decision: "reject" });
  const timeline = ordered([proposalEvent(), evidenceEvent(), rejection]);

  // Act
  const binding = currentProposalBinding(timeline);

  // Assert
  expect(binding?.approval).toEqual(rejection);
  expect(binding?.decisionRecorded).toBe(true);
});

test("reports no approval until a human has decided", () => {
  // Arrange
  const timeline = ordered([proposalEvent(), evidenceEvent()]);

  // Act
  const binding = currentProposalBinding(timeline);

  // Assert
  expect(binding?.approval).toBeUndefined();
  expect(binding?.decisionRecorded).toBe(false);
});

test("drops an earlier approval when a later proposal supersedes it", () => {
  // Arrange
  const superseded: DashboardEvent = {
    ...proposalEvent(),
    data: { ...proposalEvent().data, proposalId: "proposal-synthetic-0002" },
  };
  const timeline = ordered([
    proposalEvent(),
    evidenceEvent(),
    approvalEvent(),
    superseded,
    {
      ...evidenceEvent(),
      data: { ...evidenceEvent().data, proposalId: "proposal-synthetic-0002" },
    },
  ]);

  // Act
  const binding = currentProposalBinding(timeline);

  // Assert
  expect(binding?.approval).toBeUndefined();
  expect(binding?.decisionRecorded).toBe(false);
});

test("binds evidence the recorder captured before its own proposal", () => {
  // Arrange
  // Live ordering, 2026-09-01: the recorder captured the evidence decision at audit ordinal
  // 329 and the proposal it scores at 331, because the two families arrive on separate
  // queues and capture order across them is not the causal order.
  const timeline = ordered([evidenceEvent(), proposalEvent()]);

  // Act
  const binding = currentProposalBinding(timeline);

  // Assert
  expect(binding?.proposal).toEqual(proposalEvent());
  expect(binding?.evidence).toEqual(evidenceEvent());
});

test("binds an approval the recorder captured before its own proposal", () => {
  // Arrange
  const timeline = ordered([approvalEvent(), evidenceEvent(), proposalEvent()]);

  // Act
  const binding = currentProposalBinding(timeline);

  // Assert
  expect(binding?.approval).toEqual(approvalEvent());
  expect(binding?.decisionRecorded).toBe(true);
});
