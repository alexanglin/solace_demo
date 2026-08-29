# ADR-0192: Cover a reference-host broker restart with the reconnection budget

- **Status:** Accepted
- **Date:** 2026-08-28
- **Deciders:** Alex Anglin
- **Supersedes in part:** ADR-0145, as to the active reconnection attempt count

## Context

[ADR-0145](0145-bound-solace-recovery-and-queue-retirement.md) gives every project-owned PubSub+
client an active reconnection budget of 30 attempts 1 000 ms apart and ends recovery when it is
exhausted: the service shuts down and exits non-zero so its supervisor sees a failure. The budget was
chosen before the merged data plane had run against a broker restart.

The first live application data-plane runs on the Apple Silicon Docker Desktop reference host
(2026-08-27 and 2026-08-28, `release-evidence/phase-3/application-data-plane-first-run.md`) measured
the restart the ADR-0186 controller performs: about 14 s of graceful stop under the merged
`stop_grace_period`, then about 20 s of boot before the broker enables its listen ports. While the
ports are closed each reconnection attempt is refused at once, so 30 attempts 1 000 ms apart are spent
in about 30 s, and every application session reached `EXHAUSTED` roughly five seconds before the
broker was back. Nothing the restart exists to prove — degraded readiness, Guaranteed spooling across
the outage, rebind, drain, and readiness recovery — can be observed with a budget shorter than the
restart it must survive.

## Decision

The active reconnection budget is 60 attempts 1 000 ms apart: about 60 s of a closed broker port,
covering the measured 35 s restart with margin for a slower stop or boot. The wait between attempts,
the connection-attempt timeout, the initial connection retries, and every other ADR-0145 rule —
readiness removed on disconnect, restored only after rebind and drain, non-zero exit on exhaustion —
are unchanged. The value is pinned by an exact SDK-property test and carried by the
operating-parameters row; the ADR-0186 restart in the live data-plane probe is the instrument that
proves the budget covers a restart on the reference host.

## Consequences

- A service survives a reference-host broker restart instead of exiting, and the post-restart
  observations become measurable.
- A service that has genuinely lost its broker now takes up to about a minute, rather than half of
  one, to exit and surface the failure to its supervisor.
- The synchronized-burst concern ADR-0145 records is unchanged in kind: the same clients retry once
  per second, for twice as long.
- Rejected: shortening the broker's graceful stop, which ADR-0161 chose deliberately; and widening the
  probe alone, which would only hide that production sessions die on a restart the reference host
  performs.
