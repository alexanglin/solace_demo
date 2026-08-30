# ADR-0211: Let the mission-lifecycle observer outlive one failed observation

- **Status:** Accepted
- **Date:** 2026-08-29
- **Deciders:** Alex Anglin
- **Supersedes in part:** ADR-0209, whose consequences accepted the behaviour this replaces

## Context

[ADR-0209](0209-publish-the-mission-lifecycle-from-observed-run-status.md) named a weakness in its own
decision: "if an unexpected exception escapes `observe_once` … the task ends and the shutdown path
re-raises it. Mission publication then stops while the API stays up and readiness stays true, and
nothing surfaces that until shutdown."

The first live run hit it on the first mission.

The observer staged the `SEARCHING` edge one second after Start; the outbox published it, the recorder
applied it, and the browser rendered it. The fleet then ran its fourteen ticks to completion — 280
telemetry, 42 sector, and 3 connectivity events all recorded — and the mission stayed `SEARCHING` for
the next three minutes. Private control was answering `{"state":"EXHAUSTED"}` correctly throughout, and
PostgreSQL logged no error. Restarting the API published the `EXHAUST` edge within five seconds, which
is what proved the logic was right and the task was dead.

Nothing recorded which exception ended it, and that is the second half of the defect. A task created
with `asyncio.create_task` and held in an attribute is never garbage collected, so asyncio's
"Task exception was never retrieved" handler never fires; the only awaiting code was `stop()`, at
shutdown, long after the mission had ended. The failure was therefore invisible in exactly the window
where it mattered, and it remains unidentified.

## Decision

The observer loop catches an escaping exception, records it, and keeps observing.

`observe_once` still converts every dependency failure it can expect into a typed outcome, and the set
it converts is widened to include `STORE_BOUNDARY_ERRORS`. The scenario client already redacts its own
transport failures into `ApiError`; the lifecycle transactions did not, because they raise the store's
own repository and driver errors directly. Those are ordinary dependency loss, not defects, and the
next observation reads the same durable state and reaches the same decision.

Anything that still escapes is a defect. It is reported through an injected sink — the composition
supplies one that names the exception's module and class and nothing else, because a traceback here
could carry a database URL and a class name cannot carry a credential. The loop counts every failure
and reports once per failing *episode*: at a one-second interval, a broken dependency would otherwise
become one log line per second, and the useful signal is that failures started, not that they
continued.

Continuing is safe because mission publication is not an authorization boundary. The approval
protocol, the command-authority table, and the gateway are untouched by it; losing a mission event
costs the operator visibility, which is exactly what stopping the loop also costs, permanently.

## Consequences

- A mission now reaches its ending even when one observation fails, which is what the live run needed
  and did not get.
- A failure that used to be invisible until shutdown is named when it starts. It is named by class
  only, so it says which dependency or invariant broke without saying what value it held.
- The exception that ended the first live run remains unidentified. It did not recur on the run that
  followed this change, so this record names the defect it fixes and does not claim to name its cause.
- Negative: a permanently broken observation now retries once a second forever rather than stopping.
  That is a bounded cost — three indexed reads and at most one status call — and it is reported, but a
  reader who sees one log line must know to read `failures` rather than assume a single blip.
- Negative: catching `Exception` around the observation is exactly the broad catch the repository
  otherwise refuses. It is justified here only because the alternative is the silent permanent stop
  this record exists to remove, and it is paired with reporting rather than swallowing.
- Negative: reporting once per episode means a failure that alternates with success reports every
  other observation. The counter is what distinguishes that from a single event.

## Alternatives considered

- **Keep the loop fragile and make the death visible instead.** Rejected: a visible stop is still a
  stopped mission. The operator needs the ending, not a notification that they will not get one.
- **Fail readiness when the observer stops.** Rejected: it converts a lost mission event into a lost
  dashboard. ADR-0189's fail-safe direction is that losing a subordinate capability must not disable
  telemetry, operator visibility, or the approval boundary.
- **Restart the task instead of continuing the loop.** Rejected as the same thing with more moving
  parts: a new task would meet the same failure on its first observation, and the restart count would
  have to be bounded, which is the failure counter this record already keeps.
- **Log the full traceback.** Rejected without a redactor: SQLAlchemy and asyncpg frames can carry a
  connection URL, and this repository is public. Naming the class is the most that is safe here, and a
  redacting log helper with two real consumers is separate work.
