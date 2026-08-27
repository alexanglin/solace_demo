# ADR-0141: Exhaust deployed SSE buffers with two bounded producers

- **Status:** Accepted
- **Date:** 2026-08-26
- **Deciders:** Alex Anglin
- **Supersedes in part:** ADR-0138's single-batch production pressure input

## Context

ADR-0138 correctly moved production pressure from a paused dashboard API to a paused downstream Caddy
relay. Its single 512-event producer was not sufficient on the reference Docker Desktop runtime. The
accepted connectivity events occupied 105,216 canonical audit-payload bytes before SSE framing, which fit
the deployed Unix-socket send path. Uvicorn therefore handed the whole suffix to the stopped relay without
filling the application buffer, and the browser correctly observed no terminal overload.

The pressure command already refuses more than 512 events, and that bound remains useful: one producer
cannot turn an acceptance probe into unbounded broker or database work. Enlarging the command, adding a
pressure route, intercepting browser requests, or pausing the API would weaken a different boundary.

## Decision

Keep the pressure command maximum at 512 events. Production overload acceptance runs exactly two
sequential pressure producers, each with a distinct canonical UUIDv4-derived source identity and exactly
512 acknowledged connectivity events. Both producer receipts must independently prove 512 unique
sequences from zero through 511 before Caddy resumes.

The total deployed pressure input is therefore 1,024 events, while every invocation and producer remains
bounded at 512. The normal fleet stays stopped, Caddy stays paused, and the dashboard API container and
process stay running and unchanged throughout publication. The browser must still observe one terminal
overload, make exactly one resnapshot request, and converge on the current validated successor snapshot.

This count is production transport evidence only. It does not change the scenario, per-client 256-frame
buffer, 512-event store page, replay bound, or 280-message fleet-publication claim.

## Consequences

- The reference Unix-socket and relay buffering is exhausted without adding a runtime-only capability.
- Each pressure producer remains inside the existing command, broker, recorder, and sequence bounds.
- The disposable historical mission gains 1,024 synthetic lifecycle rows; normal mission and replay
  evidence are exported before pressure and remain separately measured.
- If deployed transport buffering changes again, the acceptance input must be remeasured and superseded;
  a passing direct buffer unit test cannot substitute for this browser path.

## Alternatives considered

- **Increase one producer above 512.** Rejected because it weakens the accepted command bound.
- **Add a pressure-only HTTP or SSE control.** Rejected because it would be a shipped capability with no
  operator use.
- **Throttle or intercept the browser request.** Rejected because it would stop proving the unmodified
  Caddy-to-browser path.
- **Accept the single-batch pass from another socket configuration.** Rejected because the reference
  runtime is the release environment this evidence must exercise.
