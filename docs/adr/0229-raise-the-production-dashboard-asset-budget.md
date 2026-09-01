# ADR-0229: Raise the production dashboard asset budget to 1,550,000 bytes

- **Status:** Accepted
- **Date:** 2026-09-01
- **Deciders:** Alex Anglin
- **Amends:** [ADR-0122](0122-bound-production-dashboard-script-and-style-bytes.md), as to the figure only

## Context

The operator-command control added by [ADR-0227](0227-give-the-operator-a-rescue-escalation-control.md)
validates its request and response at the browser boundary, which means
`operator-command-request.schema.json` and `command-response.schema.json` join the Ajv standalone
registry. Their generated validators add 27,063 bytes to the production bundle.

Measured on 2026-09-01, before and after, over the JavaScript chunk and CSS asset the budget plugin
counts:

| | bytes |
| --- | --- |
| Before | 1,486,623 |
| ADR-0122 ceiling | 1,500,000 |
| Headroom | 13,377 |
| After | 1,513,686 |

The ceiling was already within 13,377 bytes, so it would have refused the next browser boundary of
any size, not merely this one. The alternative to registering both schemas is to hand-narrow the
response, which `apps/dashboard/AGENTS.md` forbids: unknown input is narrowed only after the offline
Ajv registry accepts it.

## Decision

**`PRODUCTION_SCRIPT_AND_STYLE_BUDGET_BYTES` becomes 1,550,000.** ADR-0122's mechanism, its
measurement, and what it counts are unchanged; only the figure moves, and the
`operating-parameters.md` row carries the new value with the measurement above.

## Consequences

- The escalation control's browser trust boundary fits, with about 36,000 bytes of headroom.
- **The page is permitted to be larger.** 1,550,000 is a 3.3% increase and the measured bundle uses
  97.7% of it, so the next addition of this size faces the same decision again.
- The real lever is untouched: MapLibre dominates the main chunk, and code-splitting it would free
  far more than any ceiling change. That work is deliberately not done here, and the raised figure
  should not be read as a substitute for it.

## Alternatives considered

- **Split MapLibre out of the main chunk.** The better long-term answer and it would have kept the
  ceiling where it was. Rejected for this change only: it alters how the dashboard loads and needs
  its own testing, which this increment could not absorb.
- **Register only the request schema and hand-narrow the response.** Rejected: it weakens a stated
  boundary to protect a number.
