import { useEffect, useState } from "react";

import type { DashboardEvent, DashboardProposalDecisionRequest } from "../contracts/generated";
import type { ProposalDecisionSubmission, ProposalDecisionSubmitter } from "./mutation-client";
import { prepareProposalDecision, type ProposalDecisionPreparation } from "./proposal-decision";

type ProposalEvent = Extract<DashboardEvent, { kind: "agentProposal" }>;
type EvidenceEvent = Extract<DashboardEvent, { kind: "evidenceDecision" }>;
type Decision = DashboardProposalDecisionRequest["decision"];

export type DashboardMode = "degradedLive" | "replay";
export type DashboardSourceState =
  "connected" | "degraded" | "exhausted" | "loading" | "offline" | "recovered" | "retrying";

interface ProposalDecisionPanelProperties {
  readonly decisionRecorded: boolean;
  readonly evidence: EvidenceEvent;
  readonly mode: DashboardMode;
  readonly proposal: ProposalEvent;
  readonly sourceState: DashboardSourceState;
  readonly submit?: ProposalDecisionSubmitter;
}

interface PreparedDecisions {
  readonly approve: ProposalDecisionPreparation;
  readonly evidence: EvidenceEvent;
  readonly proposal: ProposalEvent;
  readonly reject: ProposalDecisionPreparation;
}

interface DecisionConfirmation {
  readonly decision: Decision;
  readonly evidence: EvidenceEvent;
  readonly proposal: ProposalEvent;
}

type DecisionFeedback =
  | { readonly kind: "error"; readonly message: string }
  | { readonly kind: "status"; readonly message: string };

interface BoundDecisionFeedback {
  readonly evidence: EvidenceEvent;
  readonly feedback: DecisionFeedback;
  readonly proposal: ProposalEvent;
}

const unavailableByReason: Record<
  Exclude<ProposalDecisionPreparation, { ok: true }>["reason"],
  string
> = {
  APPROVAL_NOT_CORROBORATED: "Approval requires a corroborated contributing evidence decision.",
  BINDING_MISMATCH: "Proposal and evidence binding do not match.",
  DIGEST_REFUSED: "The browser could not verify the evidence binding.",
  DISTINCT_LIVE_SOURCES_REQUIRED: "Approval requires two distinct live evidence sources.",
};

const submissionFailureMessage: Record<
  Exclude<ProposalDecisionSubmission, { ok: true }>["reason"],
  string
> = {
  CONTRACT_REFUSED: "The decision contract was refused. No second decision was sent.",
  IDEMPOTENCY_REFUSED: "A safe idempotency key could not be created. Nothing was sent.",
  SERVER_REFUSED:
    "The server refused the decision. Refresh the broker-backed facts before retrying.",
  STALE_RUNTIME:
    "The dashboard runtime changed. A full reload is required before another decision.",
  SUBMISSION_PENDING: "A proposal decision is already pending.",
  TRANSPORT_AMBIGUOUS:
    "The result is ambiguous. Inspect refreshed broker facts before deciding again.",
};

function coordinate(value: number): string {
  return (value / 1_000_000).toFixed(6);
}

function outcomeLabel(evidence: EvidenceEvent): string {
  const data = evidence.data;
  if (data.outcome === "contributing") {
    return `${data.outcome} · ${data.band} · score ${String(data.score)}`;
  }
  return `${data.outcome} · ${data.reason}`;
}

function sourceAllowsDecision(sourceState: DashboardSourceState): boolean {
  // `exhausted` describes the mission, not the stream, and the agent's candidate always
  // arrives after the sweep completes. Grouping it with `offline` and `retrying` closed the
  // gate on every real proposal (docs/adr/0228).
  return sourceState === "connected" || sourceState === "exhausted" || sourceState === "recovered";
}

function preparationFor(
  decisions: PreparedDecisions | undefined,
  decision: Decision,
): ProposalDecisionPreparation | undefined {
  return decision === "approve" ? decisions?.approve : decisions?.reject;
}

function feedbackFor(result: ProposalDecisionSubmission): DecisionFeedback {
  if (result.ok) {
    return {
      kind: "status",
      message:
        "Durably accepted; awaiting validated broker events for authorization, dispatch, and result.",
    };
  }
  return { kind: "error", message: submissionFailureMessage[result.reason] };
}

export function ProposalDecisionPanel({
  decisionRecorded,
  evidence,
  mode,
  proposal,
  sourceState,
  submit,
}: ProposalDecisionPanelProperties): React.JSX.Element {
  const [prepared, setPrepared] = useState<PreparedDecisions>();
  const [confirmation, setConfirmation] = useState<DecisionConfirmation>();
  const [pending, setPending] = useState(false);
  const [feedback, setFeedback] = useState<BoundDecisionFeedback>();

  useEffect(() => {
    let current = true;
    void Promise.all([
      prepareProposalDecision(proposal, evidence, "approve"),
      prepareProposalDecision(proposal, evidence, "reject"),
    ]).then(([approve, reject]) => {
      if (current) {
        setPrepared({ approve, evidence, proposal, reject });
      }
    });
    return () => {
      current = false;
    };
  }, [evidence, proposal]);

  const currentPrepared =
    prepared?.evidence === evidence && prepared.proposal === proposal ? prepared : undefined;
  const activeConfirmation =
    confirmation?.evidence === evidence && confirmation.proposal === proposal
      ? confirmation.decision
      : undefined;
  const visibleFeedback =
    feedback?.evidence === evidence && feedback.proposal === proposal
      ? feedback.feedback
      : undefined;

  const operational =
    mode === "degradedLive" &&
    sourceAllowsDecision(sourceState) &&
    !decisionRecorded &&
    submit !== undefined;
  const approvePreparation = currentPrepared?.approve;
  const rejectPreparation = currentPrepared?.reject;
  const interactionLocked = pending || activeConfirmation !== undefined;
  const approveEnabled = operational && approvePreparation?.ok === true && !interactionLocked;
  const rejectEnabled = operational && rejectPreparation?.ok === true && !interactionLocked;

  async function confirmDecision(): Promise<void> {
    if (activeConfirmation === undefined || pending || submit === undefined) {
      return;
    }
    const selected = preparationFor(currentPrepared, activeConfirmation);
    if (!selected?.ok) {
      setFeedback({
        evidence,
        feedback: {
          kind: "error",
          message:
            selected === undefined
              ? "The exact decision binding is still being verified."
              : unavailableByReason[selected.reason],
        },
        proposal,
      });
      return;
    }
    setPending(true);
    const result = await submit(selected.request);
    setFeedback({ evidence, feedback: feedbackFor(result), proposal });
    setPending(false);
    setConfirmation(undefined);
  }

  return (
    <section aria-labelledby="proposal-decision-heading" className="proposal-panel">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Human authorization boundary</p>
          <h2 id="proposal-decision-heading">
            {mode === "replay" ? "Recorded proposal — replay is read only" : "Rescue proposal"}
          </h2>
        </div>
        <span className="status-chip">{outcomeLabel(evidence)}</span>
      </div>
      <dl className="binding-grid">
        <div>
          <dt>Proposal</dt>
          <dd>{proposal.data.proposalId}</dd>
        </div>
        <div>
          <dt>Proposal digest</dt>
          <dd className="digest">{proposal.data.proposalDigest}</dd>
        </div>
        <div>
          <dt>Evidence decision</dt>
          <dd>{evidence.data.evidenceDecisionId}</dd>
        </div>
        <div>
          <dt>Proposed action</dt>
          <dd>
            Rescue escalation · {proposal.data.droneId} ·{" "}
            {coordinate(proposal.data.latitudeMicrodegrees)},{" "}
            {coordinate(proposal.data.longitudeMicrodegrees)}
          </dd>
        </div>
      </dl>

      {mode === "replay" ? (
        <p className="read-only-note">
          Recorded facts are visible; this graph constructs no writer.
        </p>
      ) : (
        <>
          <div className="decision-actions" aria-label="Proposal decisions">
            <button
              aria-label="Approve exact rescue proposal"
              disabled={!approveEnabled}
              onClick={() => {
                setConfirmation({ decision: "approve", evidence, proposal });
              }}
              type="button"
            >
              Approve proposal
            </button>
            <button
              aria-label="Reject exact rescue proposal"
              className="secondary"
              disabled={!rejectEnabled}
              onClick={() => {
                setConfirmation({ decision: "reject", evidence, proposal });
              }}
              type="button"
            >
              Reject proposal
            </button>
          </div>
          {decisionRecorded ? (
            <p role="status">A decision is already recorded for this exact proposal.</p>
          ) : null}
          {approvePreparation !== undefined && !approvePreparation.ok ? (
            <p className="decision-guidance">{unavailableByReason[approvePreparation.reason]}</p>
          ) : null}
        </>
      )}

      {activeConfirmation !== undefined ? (
        <div
          aria-labelledby="proposal-confirmation-heading"
          aria-modal="true"
          className="dialog-backdrop"
          role="dialog"
        >
          <div className="dialog-card">
            <h3 id="proposal-confirmation-heading">Confirm proposal decision</h3>
            <p>
              {activeConfirmation === "approve"
                ? "Approval can authorize one rescue-escalation command for this exact proposal and evidence binding."
                : "Rejection records a human refusal and cannot authorize a rescue-escalation command."}
            </p>
            <div className="decision-actions">
              <button
                disabled={pending}
                onClick={() => {
                  void confirmDecision();
                }}
                type="button"
              >
                {pending
                  ? "Submitting decision…"
                  : activeConfirmation === "approve"
                    ? "Confirm approval"
                    : "Confirm rejection"}
              </button>
              <button
                className="secondary"
                disabled={pending}
                onClick={() => {
                  setConfirmation(undefined);
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
        <p aria-label="Proposal decision status" role="status">
          {visibleFeedback.message}
        </p>
      )}
    </section>
  );
}
