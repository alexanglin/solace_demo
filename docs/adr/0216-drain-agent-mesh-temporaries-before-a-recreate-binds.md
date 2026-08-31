# ADR-0216: Drain Agent Mesh temporaries before a recreate binds

- **Status:** Accepted
- **Date:** 2026-08-31
- **Deciders:** Alex Anglin

## Context

The pinned Agent Mesh container binds one non-durable endpoint per app, and the broker releases a
session's temporaries only after that session closes. `docker compose up --force-recreate` starts the
replacement within about two seconds of stopping the predecessor, so both incarnations count against
the same per-identity ceiling and the loser is refused with
`SOLCLIENT_SUBCODE_NO_MORE_NON_DURABLE_QUEUE_OR_TE`.

That is the refusal ADR-0196 recorded across eighteen restarts. It recurred on 2026-08-31 in a
quieter form: the Event Mesh Gateway's data-plane receiver lost the race twice at startup, never
retried, and its exception was raised inside a task nobody awaited. The container reported healthy for
twelve hours with the salient-drone-event ingress subscribed to nothing, and a read-only SEMP
inventory — one endpoint owned by `event-mesh-gateway` where the configuration requires two — is what
exposed it.

Waiting is not a tuning preference. The reap is a single sweep, not a gradual release: five measured
waits took 37.2, 48.1, 49.2, 48.3, and 59.0 seconds, with every temporary released together.

## Decision

Recreating the Agent Mesh is stop, wait for the drain, then start. `aerial_rescue_broker.drain` is the
wait: it polls the non-durable endpoints owned by each Agent Mesh identity and returns when all three
report none, refusing past `DRAIN_DEADLINE_SECONDS`.

It takes a port narrower than `MonitorTransport`, carrying only `read_monitor`, so no request it can
build reaches a write or a count it does not use. It derives its identity set from
`queues.queue_templates()`, the existing home of the roles that own an upstream non-durable template,
rather than listing them a second time.

The deadline is 120 seconds: twice the observed maximum. A single early sample suggested 60, and the
fifth measurement came within a second of it.

## Consequences

- A recreate costs roughly a minute of waiting. That is the price of not racing, and it is paid by an
  operator command rather than during a demonstration.
- Draining before starting restored the gateway to its two endpoints with no flow failures, which is
  how the twelve-hour dead ingress was found and fixed.
- The drain reports; it does not repair. A broker still holding temporaries past the deadline fails
  the command rather than starting anyway.
- This bounds the race. It does not make a residual data-plane failure audible — that task is still
  unawaited upstream, and closing it is separate work.
- Rejected: a fixed sleep. The reap is a sweep whose timing varies by twenty seconds across five
  observations, so any constant is either a guess that is too short or a wait that is too long.
