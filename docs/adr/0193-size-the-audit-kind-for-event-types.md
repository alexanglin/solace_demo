# ADR-0193: Size the audit kind column for event types

- **Status:** Accepted
- **Date:** 2026-08-28
- **Deciders:** Alex Anglin

## Context

Revision 0001 created `audit_record.kind` as a 32-character string, the bound the contracts give a
single KIND level, and the recorder then wrote dashboard kinds such as `sectorLifecycle` into it.
The Solace data-plane adoption (`8b4f6d5`) made the dashboard projection bind every audit record to
its event by `record.kind == envelope.type`, so the recorder now writes the CloudEvent type. A type is
`aerial-rescue.v1.` plus a family literal plus a kind: `aerial-rescue.v1.agent.proposal.candidate-location`
is 50 characters, and the contracts allow 17 + 20 + 1 + 32 = 70. The first live application
data-plane run to survive a broker restart (run 14, 2026-08-28) stopped in the recorder's drain with
`value too long for type character varying(32)` on that proposal type; no unit test could see it,
because the recorder's suites use fakes and the schema tests pinned the width the column had.

## Decision

Revision `0011_audit_kind` widens `audit_record.kind` to 96 characters: enough for the 70 the
contracts can render, with margin for a longer family literal, and no other change. The downgrade
restores exactly 32 and fails if a longer kind is stored, which is the fail-closed behaviour the
immutable history requires. `EVENT_TYPE_LENGTH` names the width in the shared metadata, a schema
test derives the 70-character bound from the contracts and proves the column holds it, and the
offline migration tests render both directions. The KIND bound of 32 for a single level is unchanged;
`idempotency_claim.kind`, `broker_refusal.family`, and `evidence_item.source_kind` keep their widths
because they hold single levels.

## Consequences

- The recorder can persist every family's audit record under the merged binding.
- Revision 0011 joins the append-only history; the dashboard revision 0005 and every later revision
  stay immutable, and the live disposable-database probe walks eleven revisions in both directions.
- Rejected: writing a shorter kind, which would break the dashboard's `kind == type` binding; and
  widening every KIND column, which would loosen bounds that hold single levels.
