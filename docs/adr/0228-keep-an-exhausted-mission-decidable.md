# ADR-0228: Keep an exhausted mission decidable

- **Status:** Accepted
- **Date:** 2026-09-01
- **Deciders:** Alex Anglin

## Context

With the agent hop and the escalation control both working, the approval gate rendered live with a
real corroborated candidate and its controls were disabled. `proposalDecisionSourceState` answered
`exhausted` for any mission whose lifecycle was `EXHAUSTED`, and the panel grouped `exhausted` with
`offline` and `retrying` as states that permit no mutation.

That ordering is not incidental. The fleet publishes its one salient observation when the drone
carrying the scenario's heartbeat absences completes its sweep, which is the same moment the last
sector is searched and the mission reaches `EXHAUSTED`; the agent answers roughly thirty-five
seconds later. **Every real proposal is therefore post-exhaustion**, and the gate could never be
used for the candidate it exists to authorise.

The short-circuit had a second defect in the other direction: it answered `exhausted` whatever the
stream was doing, so an exhausted mission whose stream had dropped would have been reported as a
decidable state rather than an offline one.

## Decision

**An exhausted mission is decidable when its stream is healthy.** The mission lifecycle no longer
short-circuits `proposalDecisionSourceState`; the source status governs, so a connected stream
answers `connected` and a dropped one still answers `offline` or `retrying`. `exhausted` remains a
source state that `live-source` publishes for a healthy stream over a finished mission, and both
operator panels accept it alongside `connected` and `recovered`.

## Consequences

- The approval gate and the escalation control work for the candidate the scenario actually
  produces. This was the last thing between the demo and a complete run.
- A genuine source fault still disables both, and now does so even on an exhausted mission, which
  the short-circuit previously prevented.
- **The window in which a human may authorise a rescue is wider than it was.** A decision is no
  longer bounded by the search being active, only by the stream being healthy and the approval's
  own sixty-second time to live ([ADR-0042](0042-approval-time-to-live.md)). That is the intended
  behaviour for a search that has exhausted its sectors and holds one corroborated lead, and it is
  a widening of an authority boundary rather than a presentational change.
- The committed test that grouped `exhausted` with `offline` and `retrying` changes with it, and a
  new case pins the opposite: an exhausted mission keeps its decision enabled.

## Alternatives considered

- **Move the salient observation earlier in the scenario** so the proposal arrives while the mission
  is still `SEARCHING`. Rejected for this change: it alters scenario data and the fleet simulator's
  derived trigger, which `services/fleet_simulator/AGENTS.md` requires to stay derived from the
  scenario rather than named, and it leaves the second defect — the masked source fault — in place.
- **Leave the rule and accept that the gate never fires.** Rejected: it makes the approval boundary
  unreachable, which is the safety story the project is built to demonstrate.
