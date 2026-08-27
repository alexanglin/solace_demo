import type {
  DashboardEvent,
  DashboardSnapshot,
  OrderedDashboardEvent,
} from "./contracts/generated";
import type { ProposalDecisionSubmitter } from "./operator/mutation-client";
import {
  ProposalDecisionPanel,
  type DashboardMode,
  type DashboardSourceState,
} from "./operator/proposal-decision-panel";
import { currentProposalBinding } from "./operator/proposal-binding";

export { currentProposalBinding } from "./operator/proposal-binding";

export type ApplicationSourceState = DashboardSourceState | "contractFailure" | "staleRuntime";

interface ApplicationShellProperties {
  readonly mode: DashboardMode;
  readonly snapshot?: DashboardSnapshot;
  readonly sourceState: ApplicationSourceState;
  readonly submitDecision?: ProposalDecisionSubmitter;
}

const sourceStateLabel: Record<ApplicationSourceState, string> = {
  connected: "Live broker stream connected",
  contractFailure: "Contract failure · last validated mission state retained",
  degraded: "Degraded live · broker delivery unavailable",
  exhausted: "Mission exhausted · critical facts retained",
  loading: "Loading broker-backed mission state",
  offline: "Offline · last validated mission state retained",
  recovered: "Recovered · validated broker stream resumed",
  retrying: "Connection interrupted · retrying",
  staleRuntime: "Runtime changed · full reload required",
};

function operatingMode(mode: DashboardMode, sourceState: ApplicationSourceState): string {
  if (mode === "replay") {
    return "ISOLATED REPLAY · READ ONLY";
  }
  if (sourceState === "connected" || sourceState === "recovered") {
    return "LIVE BROKER DATA · DEGRADED LIVE SIMULATION";
  }
  return "DEGRADED LIVE SIMULATION";
}

function byteOrder(left: string, right: string): number {
  if (left < right) {
    return -1;
  }
  return left === right ? 0 : 1;
}

function eventDetail(event: DashboardEvent): string {
  if (event.kind === "agentProposal") {
    return event.data.proposalId;
  }
  if (event.kind === "evidenceDecision") {
    return event.data.evidenceDecisionId;
  }
  if (event.kind === "operatorApproval") {
    return `${event.data.decision} · ${event.data.proposalId}`;
  }
  if (event.kind === "operatorCommand") {
    return event.data.commandId;
  }
  if (event.kind === "commandResult") {
    return `${event.data.commandId} · ${event.data.outcome}`;
  }
  return event.kind;
}

function MissionState({ snapshot }: { readonly snapshot: DashboardSnapshot }): React.JSX.Element {
  const mission = snapshot.state.currentMission;
  const orderedFleet = [...snapshot.state.fleet].sort((left, right) =>
    byteOrder(left.identifier, right.identifier),
  );
  const simulated = snapshot.state.fleet.filter(
    (member) => member.participation === "SIMULATED",
  ).length;
  const declaredOnly = snapshot.state.fleet.length - simulated;

  if (mission === null) {
    return (
      <section aria-labelledby="mission-state-heading" className="surface empty-state">
        <h2 id="mission-state-heading">No current mission</h2>
        <p>The broker-backed projection has no active mission.</p>
      </section>
    );
  }

  return (
    <>
      <section aria-labelledby="mission-state-heading" className="surface mission-summary">
        <div>
          <p className="eyebrow">Current mission</p>
          <h2 id="mission-state-heading">{mission.identifier}</h2>
          <p>{mission.lifecycle}</p>
        </div>
        <dl className="mission-metrics">
          <div>
            <dt>Declared</dt>
            <dd>{snapshot.state.fleet.length}</dd>
          </div>
          <div>
            <dt>Simulated</dt>
            <dd>{simulated}</dd>
          </div>
          <div>
            <dt>Declared only</dt>
            <dd>{declaredOnly}</dd>
          </div>
          <div>
            <dt>Audit ordinal</dt>
            <dd>{snapshot.state.latestAuditOrdinal}</dd>
          </div>
        </dl>
      </section>

      <section aria-labelledby="fleet-heading" className="surface">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Broker-backed projection</p>
            <h2 id="fleet-heading">Fleet</h2>
          </div>
          <span className="status-chip">{snapshot.state.sectors.length} sectors</span>
        </div>
        <div className="table-scroll">
          <table aria-label="Fleet status">
            <thead>
              <tr>
                <th scope="col">Aircraft</th>
                <th scope="col">Participation</th>
                <th scope="col">Connectivity</th>
                <th scope="col">Battery</th>
              </tr>
            </thead>
            <tbody>
              {orderedFleet.map((member) => (
                <tr key={member.identifier}>
                  <th scope="row">{member.identifier}</th>
                  <td>
                    {member.participation === "DECLARED_ONLY"
                      ? "DECLARED ONLY — NOT EXECUTED"
                      : "SIMULATED"}
                  </td>
                  <td>
                    {member.participation === "DECLARED_ONLY"
                      ? "Not applicable"
                      : member.connectivity}
                  </td>
                  <td>
                    {member.participation === "SIMULATED" && member.telemetry !== null
                      ? `${String(member.telemetry.batteryPercent)}%`
                      : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}

function MissionTimeline({
  timeline,
}: {
  readonly timeline: readonly OrderedDashboardEvent[];
}): React.JSX.Element {
  const ordered = [...timeline].sort((left, right) => left.auditOrdinal - right.auditOrdinal);
  return (
    <section aria-labelledby="timeline-heading" className="surface">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Durable audit order</p>
          <h2 id="timeline-heading">Mission timeline</h2>
        </div>
        <span className="status-chip">{ordered.length} facts</span>
      </div>
      <ol aria-label="Mission timeline" className="timeline-list">
        {ordered.map(({ auditOrdinal, event }) => (
          <li key={auditOrdinal}>
            <span className="ordinal">#{auditOrdinal}</span>
            <div>
              <strong>{event.kind}</strong>
              <p>{eventDetail(event)}</p>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

export function ApplicationShell({
  mode,
  snapshot,
  sourceState,
  submitDecision,
}: ApplicationShellProperties): React.JSX.Element {
  const binding = snapshot === undefined ? undefined : currentProposalBinding(snapshot.timeline);
  const decisionSourceState: DashboardSourceState =
    sourceState === "contractFailure" || sourceState === "staleRuntime" ? "degraded" : sourceState;

  return (
    <>
      <header className="application-header">
        <div>
          <p className="eyebrow">Solace PubSub+ mission data plane</p>
          <h1>Aerial Rescue Mesh Mission Control</h1>
        </div>
        <p aria-label="Operating mode" className={`mode-badge mode-${mode}`} role="status">
          {operatingMode(mode, sourceState)}
        </p>
      </header>
      <main>
        <section aria-labelledby="dashboard-state-heading" className="connection-strip">
          <div>
            <p className="eyebrow">Connection and recovery</p>
            <h2 id="dashboard-state-heading">Mission dashboard</h2>
          </div>
          <p aria-label="Dashboard state" role="status">
            {sourceStateLabel[sourceState]}
          </p>
        </section>

        <div className="dashboard-grid">
          <div className="mission-column">
            {snapshot === undefined ? (
              <section aria-live="polite" className="surface empty-state">
                <h2>Waiting for validated snapshot</h2>
                <p>No broker-derived mission fact has been accepted yet.</p>
              </section>
            ) : (
              <>
                <MissionState snapshot={snapshot} />
                {binding === undefined ? null : (
                  <ProposalDecisionPanel
                    decisionRecorded={binding.decisionRecorded}
                    evidence={binding.evidence}
                    mode={mode}
                    proposal={binding.proposal}
                    sourceState={decisionSourceState}
                    {...(submitDecision === undefined ? {} : { submit: submitDecision })}
                  />
                )}
              </>
            )}
          </div>
          <aside aria-label="Mission audit rail">
            <MissionTimeline timeline={snapshot?.timeline ?? []} />
          </aside>
        </div>
      </main>
    </>
  );
}
