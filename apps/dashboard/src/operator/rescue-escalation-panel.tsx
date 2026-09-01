import { useEffect, useState } from "react";

import type { DashboardEvent } from "../contracts/generated";
import type { OperatorCommandSubmission, OperatorCommandSubmitter } from "./command-client";
import type { DashboardMode, DashboardSourceState } from "./proposal-decision-panel";
import { prepareRescueEscalation, type RescueEscalationPreparation } from "./rescue-escalation";

type ProposalEvent = Extract<DashboardEvent, { kind: "agentProposal" }>;
type EvidenceEvent = Extract<DashboardEvent, { kind: "evidenceDecision" }>;
type ApprovalEvent = Extract<DashboardEvent, { kind: "operatorApproval" }>;

interface RescueEscalationPanelProperties {
  readonly approval: ApprovalEvent;
  readonly evidence: EvidenceEvent;
  readonly mode: DashboardMode;
  readonly proposal: ProposalEvent;
  readonly sourceState: DashboardSourceState;
  readonly submit?: OperatorCommandSubmitter;
}

interface PreparedEscalation {
  readonly approval: ApprovalEvent;
  readonly escalation: RescueEscalationPreparation;
}

type EscalationFeedback =
  | { readonly kind: "error"; readonly message: string }
  | { readonly kind: "status"; readonly message: string };

interface BoundEscalationFeedback {
  readonly approval: ApprovalEvent;
  readonly feedback: EscalationFeedback;
}

const unavailableByReason: Record<
  Exclude<RescueEscalationPreparation, { ok: true }>["reason"],
  string
> = {
  APPROVAL_REJECTED: "The recorded decision was a rejection, so no escalation is authorized.",
  BINDING_MISMATCH: "Approval, proposal, and evidence bindings do not match.",
  DIGEST_REFUSED: "The browser could not verify the evidence binding.",
};

const submissionFailureMessage: Record<
  Exclude<OperatorCommandSubmission, { ok: true }>["reason"],
  string
> = {
  CONTRACT_REFUSED: "The command contract was refused. No escalation was sent.",
  IDEMPOTENCY_REFUSED: "A safe idempotency key could not be created. Nothing was sent.",
  SERVER_REFUSED:
    "The server refused the escalation. Refresh the broker-backed facts before retrying.",
  STALE_RUNTIME:
    "The dashboard runtime changed. A full reload is required before another escalation.",
  SUBMISSION_PENDING: "A rescue escalation is already pending.",
  TRANSPORT_AMBIGUOUS:
    "The result is ambiguous. Inspect refreshed broker facts before dispatching again.",
};

function coordinate(value: number): string {
  return (value / 1_000_000).toFixed(6);
}

function sourceAllowsDispatch(sourceState: DashboardSourceState): boolean {
  // The same rule the decision above follows: an exhausted mission is a healthy stream, and
  // it is when the approved candidate exists at all (docs/adr/0228).
  return sourceState === "connected" || sourceState === "exhausted" || sourceState === "recovered";
}

function feedbackFor(result: OperatorCommandSubmission): EscalationFeedback {
  if (result.ok) {
    return {
      kind: "status",
      message:
        "Durably accepted; the command gateway is the only publisher of the executable command.",
    };
  }
  return { kind: "error", message: submissionFailureMessage[result.reason] };
}

export function RescueEscalationPanel({
  approval,
  evidence,
  mode,
  proposal,
  sourceState,
  submit,
}: RescueEscalationPanelProperties): React.JSX.Element | null {
  const [prepared, setPrepared] = useState<PreparedEscalation>();
  const [confirming, setConfirming] = useState<ApprovalEvent>();
  const [pending, setPending] = useState(false);
  const [feedback, setFeedback] = useState<BoundEscalationFeedback>();

  useEffect(() => {
    let current = true;
    void prepareRescueEscalation(proposal, evidence, approval).then((escalation) => {
      if (current) {
        setPrepared({ approval, escalation });
      }
    });
    return () => {
      current = false;
    };
  }, [approval, evidence, proposal]);

  if (approval.data.decision !== "approve" || mode === "replay") {
    return null;
  }

  const currentPrepared = prepared?.approval === approval ? prepared.escalation : undefined;
  const activeConfirmation = confirming === approval;
  const visibleFeedback = feedback?.approval === approval ? feedback.feedback : undefined;
  const operational =
    sourceAllowsDispatch(sourceState) && submit !== undefined && !pending && !activeConfirmation;
  const escalateEnabled = operational && currentPrepared?.ok === true;

  async function dispatchEscalation(): Promise<void> {
    if (!activeConfirmation || pending || submit === undefined) {
      return;
    }
    if (!currentPrepared?.ok) {
      setFeedback({
        approval,
        feedback: {
          kind: "error",
          message:
            currentPrepared === undefined
              ? "The exact escalation binding is still being verified."
              : unavailableByReason[currentPrepared.reason],
        },
      });
      return;
    }
    setPending(true);
    const result = await submit(currentPrepared.request);
    setFeedback({ approval, feedback: feedbackFor(result) });
    setPending(false);
    setConfirming(undefined);
  }

  return (
    <section aria-labelledby="rescue-escalation-heading" className="proposal-panel">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Authorized action</p>
          <h2 id="rescue-escalation-heading">Rescue escalation</h2>
        </div>
        <span className="status-chip">approved · {approval.data.approvalId}</span>
      </div>
      <p>
        Dispatches a rescue escalation to {approval.data.action.droneId} at{" "}
        {coordinate(approval.data.action.latitudeMicrodegrees)},{" "}
        {coordinate(approval.data.action.longitudeMicrodegrees)}. The command gateway is the only
        publisher of the executable command; this consumes the single-use approval above.
      </p>
      <div className="decision-actions" aria-label="Rescue escalation">
        <button
          aria-label="Dispatch approved rescue escalation"
          disabled={!escalateEnabled}
          onClick={() => {
            setConfirming(approval);
          }}
          type="button"
        >
          Escalate rescue
        </button>
      </div>
      {currentPrepared !== undefined && !currentPrepared.ok ? (
        <p className="decision-guidance">{unavailableByReason[currentPrepared.reason]}</p>
      ) : null}

      {activeConfirmation ? (
        <div
          aria-labelledby="rescue-escalation-confirmation-heading"
          aria-modal="true"
          className="dialog-backdrop"
          role="dialog"
        >
          <div className="dialog-card">
            <h3 id="rescue-escalation-confirmation-heading">Confirm rescue escalation</h3>
            <p>
              This sends one executable rescue-escalation command for {approval.data.action.droneId}{" "}
              and cannot be recalled from this surface.
            </p>
            <div className="decision-actions">
              <button
                disabled={pending}
                onClick={() => {
                  void dispatchEscalation();
                }}
                type="button"
              >
                {pending ? "Dispatching escalation…" : "Confirm escalation"}
              </button>
              <button
                className="secondary"
                disabled={pending}
                onClick={() => {
                  setConfirming(undefined);
                }}
                type="button"
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {visibleFeedback === undefined ? null : visibleFeedback.kind === "error" ? (
        <p role="alert">{visibleFeedback.message}</p>
      ) : (
        <p aria-label="Rescue escalation status" role="status">
          {visibleFeedback.message}
        </p>
      )}
    </section>
  );
}
