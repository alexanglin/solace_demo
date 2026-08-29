# ADR-0206: Link the broker provenance the dashboard watermark reads

- **Status:** Accepted
- **Date:** 2026-08-29
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

`watermark_statement` (`packages/store/src/aerial_rescue_store/dashboard/events.py`) reads the
dashboard's snapshot watermark by inner-joining `audit_record` to `dashboard_broker_event`:

```python
joined = _AUDIT_ROWS.join(
    _BROKER_ROWS,
    (_RECORD_MISSION == _AUDIT_MISSION) & (_RECORD_ORDINAL == _AUDIT_ORDINAL),
)
```

An audit row with no provenance row is invisible to it. The recorder has two compositions
(`TECH_DEBT.md` section 3) and only one of them writes that provenance row:

- Compose runs the `aerial-rescue-recorder` console script, so `capture.Recorder` is the deployed
  writer. Its `_persist` claims the inbox, records the source event, appends the audit row, and
  completes the inbox. It never touches `dashboard_broker_event`.
- The parallel `main.py` composition's `CaptureProcessor` builds a `BrokerEvent` in
  `_capture_material` and commits it with the audit row through `append_broker_event`.

So `audit_watermark` is structurally zero for every mission the deployed recorder records.
`SnapshotService.fold_basis_through` loops `while checkpoint.state.latest_audit_ordinal <
through_ordinal`, which is never entered, so the snapshot carries the prepared state and an empty
timeline no matter how many events committed.

Measured on 2026-08-29 against the running stack: a mission started through the browser boundary
committed telemetry rows to `audit_record`, while `dashboard_broker_event` gained none and its newest
rows still belonged to pre-merge missions. `broker_inbox` showed the recorder consuming normally, so
capture was live and only the link was missing.

This is the true root cause of finding 6 in `merged-runtime-second-composition.md` -- "the deployed
dashboard composition folded none of them". The prepared-state seeding fix and ADR-0205 repaired the
*broker-recovery* path, which reads `audit_record` directly and does not join. The snapshot path was
never repaired because nothing had identified why its watermark stayed at zero.

## Decision

The deployed recorder owns the provenance row for every fact it records. `RecordingFact` carries a
`BrokerEvent` beside its inbox, source-event and audit facts; `_persist` links it immediately after
the audit append, inside the same caller-owned transaction, before the inbox completes.

The link is a distinct store operation rather than a swap to `append_broker_event`. That function
appends the audit row *and* links it, which the recorder cannot use: it already appended its own row
under its own inbox claim, and calling it would append a second. `link_broker_event` therefore
carries the provenance half alone -- ensure the source, lock it, return an exact known duplicate,
ask the pure sequence rule whether the candidate advances, advance the high-water mark, and insert
the link for an ordinal the caller already holds. `append_broker_event` is unchanged and keeps its
combined behaviour for the parallel composition.

Ordering matters and is fixed: the link needs the ordinal, so it follows the append; it must be
durable before the inbox records a completed result, so it precedes `complete_inbox`. A redelivery
after a partial write cannot therefore observe a completed inbox with no provenance row.

## Consequences

- The snapshot watermark advances for missions the deployed recorder records, so
  `fold_basis_through` reads its pages and the dashboard's initial state and timeline are populated.
  Every operator-visible surface that folds from the snapshot depends on this.
- The recorder now serializes on `dashboard_broker_source` per producer, which it did not before. Two
  facts from one source in one mission are ordered by that lock rather than by arrival.
- Negative: the recorder now enforces the producer sequence rule. A source that reuses or regresses a
  sequence is refused `SEQUENCE_REUSED` or `STALE_SEQUENCE` where it previously recorded. That is the
  intended integrity property, and `_PERMANENT_STORE_REFUSALS` already classifies both as permanent,
  but it is new behaviour for the deployed path and a badly-behaved producer will now surface.
- Negative: capture does strictly more work per fact -- an upsert, a locking read, a duplicate probe,
  an update, and an insert. The scenario's rate is far below any measured bound, but this is not free.
- Negative: rows the deployed recorder committed before this change keep no provenance and stay
  invisible to the watermark. They are not repaired; a mission recorded before this change shows an
  empty dashboard timeline for ever.
- `TECH_DEBT.md` section 3 stays open. This record makes the deployed composition write what the
  dashboard reads; it does not remove the parallel one.

## Alternatives considered

- **Have the recorder call `append_broker_event` instead of `append_audit`.** Rejected: the recorder
  owns an inbox claim and a source-event write around its append, and the combined function would
  append a second audit row for the same fact.
- **Make `watermark_statement` read `audit_record` alone.** Rejected. The join is the point: the
  watermark is meant to be the greatest *recorder-linked* ordinal, so that a row appended by a service
  writing its own audit fact -- the command gateway and evidence service both do -- cannot advance the
  dashboard past an event the recorder has not captured. Dropping the join would let the snapshot read
  a page the recorder never saw.
- **Backfill provenance for existing rows.** Rejected for now. The affected missions are terminal or
  superseded, and a migration to repair a store a demonstration reset discards is cost without benefit.
- **Delete the parallel composition and deploy `main.py` instead.** Rejected as out of scope here; it
  is a larger change than the defect requires, and `TECH_DEBT.md` section 3 already carries it.
