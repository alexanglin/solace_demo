# ADR-0215: End a broker data plane that cannot recover, and report the one that dies

- **Status:** Accepted
- **Date:** 2026-08-30
- **Deciders:** Alex Anglin

## Context

The first composed live run of the merged runtime carried a full mission correctly — 280 telemetry,
42 sector, 3 connectivity, 328 audit records, every one provenance-linked — and then stopped
publishing. `/api/v1/readiness` reported `{"ready":false,"reasons":["broker-delivery-unavailable"]}`
for more than ten minutes. Two `application_outbox` rows staged by the ADR-0209 mission-lifecycle
observer stayed `staged`, so the operator's timeline never left `PLANNED`. Restarting the container
published the backlog and advanced the mission exactly one state, then stranded on the next row.
Two restarts were needed to reach `EXHAUSTED`. Nothing was logged.

The mission-lifecycle observer is the visible casualty because it stages *after* the mission's own
traffic has stopped — exactly when nothing else will disturb the serving loop.

Two independent defects produce that symptom, and the deployed process makes both silent.

**A serving loop that can pause for the life of the process.** `_recover_if_needed` returns early,
pausing without calling `recover()`, for any state outside `{CONNECTED, RECOVERY_PENDING}`. The only
edge out of `RECOVERING` is `BrokerLifecycle.reconnected`, and `_accept_transport_event_unlocked`
drops any transport callback whose stamp is behind the last one accepted. The SDK reports the attempt
and the restoration from its own threads, and `_BrokerServiceLifecycleListener` is registered as both
listeners, so every attempt advances the high-water mark. A restoration that trails its own final
attempt is discarded, and the state is never left again. Verified directly against the class:
`reconnecting(2000)` then `reconnected(1000)` leaves `recovering` permanently, while an equal or
later stamp reaches `recovery-pending`.

That guard is not the defect to fix. `test_stale_reconnected_callback_cannot_end_a_newer_recovery_epoch`
pins the reason it exists: a genuinely stale callback from a previous epoch must not end a newer
recovery. From timestamps alone the two cases are indistinguishable, so the ordering rule stands and
the serving loop must stop depending on a callback that may never arrive.

**A serving task nobody awaits.** `DashboardBrokerSupervisor._run` had no exception handling, and
`asyncio.create_task` keeps a strong reference, so a task that dies keeps its exception to itself and
Python's "never retrieved" handler never fires. `serve()` returning a terminal report was equally
quiet: `exit_status` is stored and never read. Every sibling service consumes its report and ends the
process — `fleet_simulator/console.py:470,483`, `evidence_service/console.py:356`,
`recorder/console.py:390`. The dashboard API alone kept serving HTTP over a dead data plane.

ADR-0211 already decided this for the observer in this same service, in words that apply verbatim
here: *"a task nobody awaits and nobody drops reports nothing."* That decision was not carried to the
serving loop.

There is no counter anywhere that bounds application recovery. `BrokerLifecycle` holds no attempt
count, `recovery_required()` only clears a boolean, and the production pause is a flat one second.
The sole budget in the system is the SDK's `RECONNECTION_ATTEMPTS = 60` (ADR-0192), which counts
transport attempts and is reset inside the SDK.

## Decision

**A transport state the serving loop can only pause on becomes terminal after the transport's own
budget has passed.** `serve()` counts consecutive cycles outside `{CONNECTED, RECOVERY_PENDING}` and
calls `lifecycle.exhausted()` past `STALLED_RECOVERY_CYCLES = RECONNECTION_ATTEMPTS + 10`. The count
resets on any cycle in a state the loop can act on, so ordinary recovery is untouched. Sixty attempts
one second apart is what ADR-0192 sized against a reference-host broker restart; past that plus a
margin the SDK has either restored the session or interrupted it, so a still-paused plane is orphaned
rather than recovering.

**A data plane that ends is reported.** `SupervisorPorts` gains a `report` port. `_run` reports a
terminal `serve()` report as `DATA_PLANE_ENDED`, and reports an unexpected exception before
re-raising it — shutdown still surfaces the task failure exactly as before. The production console
supplies `report_data_plane_failure`, which names only the exception's module and class, because a
traceback here could carry a database URL or a topic body.

`ManagedDashboardPlane` also gains `publish_staged`, which `serve()` has always called through a
`cast`.

## Consequences

**Positive.** A stalled plane now ends, is named in the log, and clears
`Dependency.BROKER_DELIVERY`; the compose healthcheck already asserts `ready is True`, so the
container goes unhealthy instead of silently serving a mute plane. The two manual restarts the live
run needed become one reported, bounded failure.

**Negative.** The bound is a timeout, and a transport that would have recovered at ninety seconds is
now ended at seventy. That trade is deliberate: an unbounded wait is indistinguishable from a
permanent stall, and this project would rather end a session it could have kept than serve an
operator a timeline that silently stopped advancing.

**Negative.** This does not fix a dropped `reconnected` callback; it bounds the damage. If the SDK
regularly delivers trailing stamps, sessions will end on a timer. Nothing observed says it does — the
mechanism is proven reachable, not proven frequent, and the live incident was not reproduced in two
subsequent attempts.

**Unproven.** The originating incident has not been reproduced. Two runs after the fix — one warm,
one against a cold process — completed cleanly with no stall, so this record's mechanism is
established from the code and from a direct probe of `BrokerLifecycle`, not from a second live
failure. What the fix guarantees is that a recurrence ends loudly and bounded rather than silently
and forever.

## Alternatives considered

**Let a trailing `reconnected` leave `RECOVERING`.** Implemented and reverted: it fails
`test_stale_reconnected_callback_cannot_end_a_newer_recovery_epoch`, and honouring it would let a
stale callback from a previous epoch declare a newer recovery finished. The existing rule is correct.

**Ask the transport whether it is connected.** The precise fix — it settles exactly the ambiguity
timestamps cannot. Rejected for now because no connectivity query is exposed anywhere in
`packages/broker`, so it means a new port through the untyped Solace boundary (ADR-0028) days before
a demonstration. Worth revisiting.

**Call `recover()` from `RECOVERING` as well.** Rejected: `recover()` publishes, and publishing into
a transport the SDK is still reconnecting is exactly what the state is meant to prevent.

**Report without ending.** Rejected as a half measure. It converts a silent stall into a noisy one
and still requires the operator to restart a container mid-mission.
