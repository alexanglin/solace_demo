# ADR-0127: Bind live runs to their mission scenario

- **Status:** Accepted
- **Date:** 2026-08-25
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

Revision `0005_dashboard_runtime` stores scenario identifier and revision on both an operational
mission and its live run. The run value has production readers: snapshots and the focused recorder
export select it with the prepared state. The mission value initially had only a writer. Keeping two
unchecked copies would let a live run name one mission while claiming a different scenario, and would
leave the mission copy with no effect.

The revision is not yet committed, so the invariant can enter the original `0005` definition without
rewriting applied migration history. Start and reset already insert the mission before the live run in
one store transaction. Replay runs intentionally have no operational mission.

## Decision

In revision `0005_dashboard_runtime`, make `(mission_id, scenario_id, scenario_revision)` a unique
referenced key on `dashboard_mission`. Replace the mission-only foreign key on `dashboard_run` with one
composite foreign key from those same three run columns to the mission key.

The existing mode constraint makes `mission_id` non-null for `degradedLive`, so PostgreSQL must match
the complete scenario identity for every live run. It makes `mission_id` null for `replay`; the
foreign key's default `MATCH SIMPLE` semantics therefore leave replay session rows valid without an
operational mission. Replay scenario identity remains run-owned.

Keep scenario identity immutable on both records. The mission repository inserts it and updates only
lifecycle. The run repository inserts it and never updates a historical run. The normalized recording
export continues to read scenario identity from `dashboard_run` and lifecycle from the joined
`dashboard_mission`; it does not add a second authority or an application-level comparison.

## Consequences

- A live run whose scenario identifier or revision differs from its mission is rejected by the
  database, including callers that bypass the service coordinator but still use the store schema.
- The mission scenario fields now have a production integrity role instead of being write-only data.
- PostgreSQL needs the explicit three-column unique constraint as the composite foreign key target even
  though `mission_id` is already the mission primary key. That adds one index to this small retained
  history.
- The previous mission-only foreign key is removed because the composite foreign key already enforces
  mission existence for live rows; retaining both would add no independent protection.
- Disposable-PostgreSQL integration evidence must cover mismatched live refusal, matching live
  acceptance, and a missionless replay row. Offline migration rendering cannot make that claim.

## Alternatives considered

- **Keep both scenario copies without a database relationship.** Rejected because the mission copy has
  no effect and inconsistent durable state remains representable.
- **Remove scenario identity from the mission.** Rejected because a mission would no longer bind its
  retained history to the scenario that created it, leaving the run as an unchecked assertion.
- **Compare the values only in the dashboard API.** Rejected because recorder, recovery, migrations, and
  future purpose-specific store callers share this persistence boundary.
- **Add a trigger.** Rejected because a declarative foreign key expresses the invariant directly and is
  inspected, downgraded, and enforced by the existing migration test strategy.
