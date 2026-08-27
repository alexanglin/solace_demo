# ADR-0138: Stall the publisher, not the API, for SSE pressure

- **Status:** Accepted
- **Date:** 2026-08-26
- **Deciders:** Alex Anglin
- **Supersedes in part:** ADR-0128's API-pause mechanism and 257-event production pressure count

## Context

ADR-0128 selected a 257-event pressure run while the dashboard API process was paused. Pausing that
process freezes both halves of the boundary being measured: the store-suffix producer cannot fill its
per-client buffer, and the HTTP body consumer cannot drain it. When the process resumes, a healthy
consumer can drain between retained frames. Treating that outcome as downstream pressure would make the
test depend on scheduler timing rather than prove a stalled client.

The direct boundary has a smaller requirement. Once an HTTP body has started but its consumer stops
requesting chunks, 257 non-droppable successors must fill 256 data slots and reserve exactly one terminal
overload frame. A healthy consumer must instead drain a suffix larger than the buffer without a false
overload or an ordinal gap. The deployed path adds finite Uvicorn, Unix-socket, Caddy, and browser
transport buffering before the application body becomes blocked, so the direct 257-event witness is not
large enough to deterministically establish production network back-pressure.

## Decision

Do not read a store suffix until the streaming-response body is first iterated. Once started, retain the
cooperative scheduling point after each retained frame so an active HTTP consumer can drain while the
producer continues. Deterministic integration evidence must prove both of these cases:

- an active body receives every audit ordinal in a 328-event suffix with no overload; and
- a body that has yielded its initial snapshot and is then not resumed receives 256 data events followed
  by exactly one terminal overload after 257 non-droppable successors.

Production acceptance stalls the actual downstream relay while leaving the API producer running. After
the historical observer stream is established and the normal mission recording is exported, the runner
stops the normal fleet publisher, pauses the existing Caddy container without stopping or recreating it,
and publishes 512 acknowledged pressure events through the existing bounded pressure command. The count
is the command and suffix-page maximum already selected by ADR-0116 and ADR-0128. It leaves enough
bounded input to fill transport buffering and then the unchanged 256-frame application buffer; it does
not change the normal scenario or establish a larger fleet-throughput claim.

The runner proves all 512 events are durably recorder-linked and samples the dashboard API container and
process identity before and during pressure. It then unpauses the same Caddy container. The real browser
must observe exactly one terminal overload, make exactly one resnapshot request, and converge on the
validated current successor snapshot. Cleanup unpauses Caddy and restores the normal fleet container in
the existing `finally` path.

The normal wilderness mission still publishes exactly 280 telemetry messages, and its recording remains
bounded and exported before pressure. The disposable historical pressure suffix is not replay or fleet
publication evidence.

## Consequences

- The production test now measures a running producer against a genuinely stalled downstream relay;
  pausing the component under test can no longer manufacture the condition.
- The pressure run writes 512 synthetic lifecycle events instead of 257, increasing acceptance time and
  disposable database volume while remaining inside existing bounds.
- Caddy is temporarily unavailable at the loopback origin, but its process and established connections
  are preserved. The API remains running and independently measurable through the runner.
- The direct 257-event case and deployed 512-event case intentionally measure different buffer layers.
  Both retain the same 256-data-plus-one-terminal application contract.

## Alternatives considered

- **Keep pausing the dashboard API.** Rejected because it stops the producer and consumer together and
  therefore creates no downstream back-pressure.
- **Use 257 events after pausing Caddy.** Rejected because the deployed transport may accept part of that
  bounded suffix before its write flow stalls, leaving fewer than 257 events at the application buffer.
- **Remove the producer scheduling point so one page always fills the buffer.** Rejected because an
  active body would overload solely because its producer won one scheduler turn.
- **Lower the production client buffer.** Rejected because acceptance must exercise the unchanged
  256-frame contract.
- **Add a pressure route, browser global, or request interception.** Rejected because each would enlarge
  or bypass the production boundary.
- **Prove only a deliberately stalled raw HTTP client.** Rejected because it would not prove terminal
  handling and resnapshot in the production browser.
