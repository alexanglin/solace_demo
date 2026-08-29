# ADR-0169: Request immediate ACKs for individually confirmed publications

- **Status:** Accepted
- **Date:** 2026-08-26
- **Deciders:** Alex Anglin
- **Extends:** ADR-0153 and ADR-0159

## Context

The application outbox publishes one Guaranteed message at a time and treats the broker's positive
publication receipt as the only success. PubSub+ normally sends a publish acknowledgment when either
one third of the publish window is reached or one second passes. Those defaults serve a sustained
publisher well, but a low-rate publisher waiting synchronously for each confirmation can pay the
timer on every row. A 50-row recovery batch could therefore spend most of its ten-second drain target
waiting for acknowledgment coalescing rather than broker or application work.

Solace recommends the ACK Immediately message property when a low-rate publisher needs an individual
acknowledgment without waiting for the window threshold or timer. The pinned Python 1.11 API exposes
that property as `message_properties.PERSISTENT_ACK_IMMEDIATELY`. See the official
[published-message acknowledgment guidance](https://docs.solace.com/API/API-Developer-Guide/Acknowledging-Published-.htm)
and
[Python property reference](https://docs.solace.com/API-Developer-Online-Ref-Documentation/python/source/rst/solace.messaging.config.solace_properties.html).

## Decision

The shared Guaranteed publisher sets `PERSISTENT_ACK_IMMEDIATELY` to `True` on every application
message before calling `publish_await_acknowledgement`. The adapter owns the value: a caller-supplied
`False` is replaced, so delivery latency cannot vary by call site. Direct and request/reply publishers
do not receive this Guaranteed-only property.

The publisher still uses the bounded 50-message reject buffer and ten-second confirmation timeout.
An immediate ACK changes only when the broker emits its positive acknowledgment; it does not convert
an absent route, rejection, timeout, or interruption into success, and it does not weaken durable
outbox reconciliation. Connected-path live evidence measures single-message latency and the 50-row
drain rather than treating the property as performance proof.

## Consequences

- Low-rate durable publications no longer wait for the broker's normal one-second ACK timer.
- Every confirmed outbox row uses one consistent acknowledgment policy independent of the caller.
- Recovery batches have enough timing margin for database work and scheduling within the ten-second
  drain target.
- Negative: one acknowledgment per application message uses more broker/client control traffic than
  windowed ACKs. The reference rates are low and confirmation latency is the selected tradeoff; a
  future high-rate batch path needs its own measured decision rather than disabling the property here.

## Alternatives considered

- **Keep the default windowed ACK.** Rejected because the application waits per row and the fixed timer
  can dominate its bounded recovery path.
- **Publish all 50 rows concurrently.** Rejected because each row needs an exact receipt and durable
  publication transition; mixed acceptance and ambiguity would complicate ordered reconciliation.
- **Let call sites choose.** Rejected because delivery behavior belongs to the typed broker adapter,
  and caller choice would make latency and recovery semantics inconsistent.
