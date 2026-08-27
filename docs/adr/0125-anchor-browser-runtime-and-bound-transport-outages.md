# ADR-0125: Anchor browser runtime identity and bound transport outages

- **Status:** Accepted
- **Date:** 2026-08-25
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

The dynamic dashboard bootstrap and every SSE snapshot both carry the API process runtime identifier,
and ADR-0096 requires a changed identifier to disable mutations and offer a full reload. The first
production browser composition opened EventSource before giving the validated bootstrap identifier to
the source session. A process restart before the first accepted snapshot could therefore establish the
new process as the session anchor instead of detecting that the page still held the prior process's
bearer.

The browser also exposed `offline` and `recovered` presentation states, but the live EventSource adapter
emitted only an immediate `disconnected` signal. Fixture input could render the other states while no
production outage could reach them. Conversely, `runtimeChanged` appeared in the source-signal schema
even though an EventSource callback cannot authenticate process identity. Keeping that value would make
a transport observation look like runtime authority.

## Decision

Parse and validate the transient production bootstrap before opening the first live EventSource. Seed
the source session with that `runtimeId`, then pass the same untrusted bootstrap bytes through the normal
boundary consumer. Invalid bootstrap opens no transport. A first or later validated snapshot with a
different runtime identifier closes the active source, retains the last validated mission state, locks
mutations, and exposes a real document reload. Runtime change is derived only by comparing those two
validated anchors; health remains liveness-only.

The browser-owned source-signal v1 vocabulary is exactly `connecting`, `disconnected`, `offline`, and
`recovered`. It never carries `runtimeChanged`.

On the first EventSource error in one outage, emit `disconnected` immediately and start one six-second
timer. If no `open` callback occurs by that deadline, emit `offline` once. An `open` callback before or
after the deadline cancels the timer and emits `recovered`; disposal also cancels the timer. Repeated
errors during the same outage neither add timers nor repeat state edges. This is dashboard transport
visibility only and never infers drone connectivity.

Production browser evidence adds two cases without changing the fixed 64-case fixture inventory. The
test runner, outside the browser, may use the explicit mission-control Compose project to exhaust the
eight SSE slots and restart the dashboard API before the page accepts a snapshot, or stop Caddy for
longer than six seconds and start it again against the same API process. No production-only control
route, browser global, request interception, or fixture source is added.

## Consequences

- A page cannot silently adopt a replacement API process merely because the restart happened before its
  first snapshot.
- Offline and recovered are now real production transport states with fake-clock unit evidence and a
  packaged outage path.
- The six-second browser timer adds one bounded timer per disconnected source. It is deliberately
  independent from EventSource's implementation-selected reconnect schedule and from drone heartbeat
  policy.
- Stopping Caddy and restarting the API are disruptive test-runner actions. Production acceptance runs
  serially against a uniquely named disposable project and restores the publisher during teardown.
- A same-runtime transport outage does not force a reload; a runtime mismatch always does.

## Alternatives considered

- **Accept the first snapshot as the runtime anchor.** Rejected because it loses restart detection when
  a page was bootstrapped by the prior process but had not yet accepted mission state.
- **Read runtime identity from health.** Rejected by ADR-0124 because health would duplicate an identity
  already carried by the two independent anchors and would add another compatibility surface.
- **Emit `runtimeChanged` from EventSource.** Rejected because disconnect and reconnect callbacks contain
  no authenticated process identity.
- **Treat every EventSource error as immediately offline.** Rejected because a transient retry should be
  visible as disconnected without claiming a sustained outage.
- **Add a production restart endpoint for Playwright.** Rejected because an acceptance convenience must
  not enlarge the application attack surface.
