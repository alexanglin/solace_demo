# ADR-0135: Keep overload recovery observable without delaying state

- **Status:** Accepted
- **Date:** 2026-08-26
- **Deciders:** Alex Anglin
- **Supersedes in part:** ADR-0128

## Context

ADR-0128 requires the production browser to receive one terminal `stream-overloaded` frame, request one
fresh snapshot, and converge on that snapshot. The implementation did all three. Because the replacement
snapshot can arrive in the same browser task, however, its connected state could replace the transient
`resynchronizing` label before an operator, assistive technology, or production Playwright assertion had
an opportunity to observe it.

Delaying the replacement snapshot would make an announcement easier to see at the cost of delaying
authoritative mission state. Retaining `resynchronizing` in server state after a successful snapshot
would instead make transport state dishonest. Neither trade is acceptable.

## Decision

Apply replacement snapshots immediately and retain only a presentation-owned overload notice for at
least 1,000 milliseconds after each validated overload frame. The dashboard-state live region gives this
notice precedence over the current lifecycle label during that interval. A repeated overload restarts
the interval, and unmount cancels the timer.

The notice does not enter server state, reduced mission state, the timeline, replay state, a digest, or
the event-source retry decision. The replacement snapshot remains authoritative as soon as it validates.

The integration instrument delivers an overload and an immediate replacement snapshot through the
production source/session boundary, proves that the replacement mission state is already visible while
the notice remains observable, and proves the lifecycle label returns after 1,000 milliseconds. The
production pressure workflow remains the end-to-end instrument for the real broker-to-browser path.

## Consequences

- Operators and assistive technology can observe that a bounded stream overload occurred.
- Recovery does not delay or falsify validated mission state.
- The one-second interval is presentation timing and therefore cannot change replay determinism or state
  digests.
- The application owns one bounded timer that must be cancelled when the dashboard unmounts.

## Alternatives considered

- **Delay resnapshot by one second.** Rejected because presentation pacing must not delay authoritative
  recovery.
- **Keep server status at `resynchronizing`.** Rejected because a validated connected snapshot has
  already superseded that transport status.
- **Show only the final connected state.** Rejected because the real overload path would have no
  operator-observable acceptance signal.
- **Add an audit or timeline event.** Rejected because overload is transport state, not mission history.
