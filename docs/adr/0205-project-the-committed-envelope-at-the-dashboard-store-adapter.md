# ADR-0205: Project the recorder's committed envelope at the dashboard store adapter

- **Status:** Accepted
- **Date:** 2026-08-29
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

`audit_record` has one `payload` column and two writers, because the recorder carries two
compositions (`TECH_DEBT.md` section 3).

Compose runs the `aerial-rescue-recorder` console script, so `capture.Recorder` is the deployed
writer. Its `_recording_fact` builds an `AuditFact` whose `kind` is `envelope.type` and whose payload
is `canonical.canonical_bytes(envelope_document(envelope))` -- the canonical CloudEvent envelope. The
parallel `main.py` composition's `CaptureProcessor` builds its record in `_capture_material`, whose
`kind` is the projected view kind and whose payload is `{kind, eventClass, mission, time, data}`.

The dashboard API reads those rows from two places, and the two disagreed:

- `messaging/projection.py::apply_audit` decodes the envelope and calls the contracts-owned
  `project()`. It reads the deployed form.
- `snapshot.py::_ordered_event` validated the payload against `dashboard-event.schema.json`. It reads
  the parallel form.

Both readers were exercised against both writers' output on 2026-08-29:

| Writer | `snapshot.py::_ordered_event` | `projection::apply_audit` |
| --- | --- | --- |
| `capture.Recorder` (deployed) -- canonical envelope | refused `DEPENDENCY_UNAVAILABLE` | accepted |
| `main.py` `CaptureProcessor` -- normalized view document | accepted | refused `EVENT_BINDING` |

No payload form was accepted by both, so whichever recorder ran, one of the two dashboard paths could
not read the rows.

The live consequence is in the store the merged runtime left behind. `mission-113f4845ff5d49bf88497fb36a946df6`
-- the run `dashboard_current_run` selected on 2026-08-28 -- holds 163 rows in the deployed form. The
broker-recovery path can fold them; the snapshot reconstruction cannot. `fold_basis_through` reads only
while `checkpoint.state.latest_audit_ordinal < through_ordinal`, so a mission whose watermark is still
zero hides the defect entirely. It surfaces once events are committed and any client requests a
snapshot or resynchronizes after an overload closure, which is precisely the demonstration path.

`ports.py` already settles which form the dashboard's domain consumes: `StoredEvent` is documented as
"one audit ordinal and its exact canonical normalized-event bytes". The adapter returned the stored
bytes unchanged, so it was not meeting the contract its own port type declares.

## Decision

`audit_record.payload` carries the canonical CloudEvent envelope, stored under the envelope's own type
as `kind`. That is what the deployed recorder commits, and it is the better durable record: the
envelope retains the source, event identifier, producer sequence, `dataschema`, and trace context that
the projected view document discards. A durable audit row must not be lossier than the event it
witnesses.

`store_adapter._normalized_event` owns the projection from that durable form into the normalized-event
bytes `StoredEvent` declares. It decodes the envelope, refuses a `kind` that disagrees with
`envelope.type`, projects through the contracts-owned `project()`, and returns
`ordered_event_document(ordered)["event"]`. Both dashboard readers therefore derive their event from
the same `project()` call over the same bytes.

The adapter is the seam because it is the boundary between a durable row and a typed domain value,
and because `StoredEvent` -- the port type it constructs -- already names the form it must produce.

## Consequences

- The snapshot reconstruction and the broker-recovery projection now agree on every committed row.
  Their checkpoints and ordered-event digests are computed from the same projected event, so the two
  paths cannot diverge on the reduced-state witness.
- `snapshot.py::_ordered_event` keeps its established contract -- normalized event document in -- and
  the suites that encode that contract in `test_snapshot_and_orchestration.py`, `test_stream_runtime.py`,
  and `test_stream_pressure.py` remain valid without modification.
- Negative: output from the parallel `main.py` composition is now unreadable by the dashboard. That
  composition is not deployed and is already recorded as debt; this record does not close it. The two
  recorder compositions remain a `TECH_DEBT.md` section 3 item and the parallel one must either adopt
  this form or be removed.
- Negative: rows a parallel composition committed before this change stay unreadable, and a mission
  mixing both forms fails closed at the first row of the other form. The demonstration reset therefore
  matters: a store carrying pre-merge rows is not a store this adapter can serve.
- Negative: the adapter now decodes and re-encodes every row it reads, where it previously passed bytes
  through. The work is bounded by the existing page size and the reconstruction ceiling, but a read is
  no longer free.
- `tests/contract/test_committed_audit_payload.py` derives its fixture by calling `_recording_fact`
  itself rather than restating the committed columns, so a change to what the deployed recorder writes
  fails that contract test instead of silently re-opening the divergence.

## Alternatives considered

- **Decode the envelope in `snapshot.py::_ordered_event` instead, leaving the adapter a pass-through.**
  Architecturally defensible -- it mirrors `apply_audit`, which decodes in the messaging layer -- but it
  changes established expectations across three test modules and requires rewriting `StoredEvent`'s
  documented meaning from normalized-event bytes to stored bytes. The adapter change touches one
  established expectation and brings the code to the port's existing documentation rather than moving
  the documentation to the code.
- **Accept either form in both readers.** Rejected. It cements two live vocabularies in one column, and
  a fallback makes each refusal inexact: a malformed envelope becomes a candidate view document and the
  reverse, so neither reader can fail closed on a specific reason.
- **Change the deployed recorder to commit the normalized view document.** Rejected. It would break
  `apply_audit`, which requires the envelope, and it discards the source, sequence, and trace context
  that make the audit row a provenance record rather than a display cache.
- **Migrate the existing rows to one form.** Rejected for now. The pre-merge rows belong to terminal
  missions that no run selects, and a data migration to repair a store that a demonstration reset
  discards would be cost without benefit.
