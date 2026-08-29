# ADR-0199: Terminate the owned Agent Mesh entrypoint

- **Status:** Accepted
- **Date:** 2026-08-28
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

[ADR-0177](0177-harden-the-pinned-agent-mesh-broker-runtime.md) replaced the vendor's unconditional
zero exit with an owned lifecycle so that "terminal broker recovery becomes observable to Compose as
nonzero process termination". On 2026-08-28 that observability failed in the way the decision was
written to prevent, and the failure was in the termination rather than in the status.

Under host contention the Agent Mesh container logged nothing for about four minutes, and at 19:49:59
the broker disconnected every one of its sessions with `CLIENT_CLIENT_DISCONNECT … reason(Too Many TCP
Retransmissions)` — seven `agent-mesh-agent` sessions, three `event-mesh-gateway`, and one
`event-mesh-tool`. Docker restarted the container, and the new process could not bind its flows: the
evicted sessions' temporary endpoints still occupied the client username's ceiling
([ADR-0196](0196-count-the-coordinator-s-reply-queue-in-the-agent-mesh-endpoint-ceiling.md)), so
`flow topic add sub` returned `SOLCLIENT_SUBCODE_NO_MORE_NON_DURABLE_QUEUE_OR_TE` and the Connector
raised while initialising flows.

The owned lifecycle then did exactly what ADR-0177 requires. It ran `stop()`, ran `cleanup()`, logged
`Cleanup completed` at 19:50:09, and returned a failure status. **The process never exited.** Docker
reported the container `Up` with `RestartCount` frozen at 7 for fourteen minutes, until an operator
stopped it by hand. No Agent Mesh recommendation was possible for that entire period, and nothing in
Compose could see it.

The mechanism is interpreter shutdown. `raise SystemExit(main())` unwinds to the interpreter, which
joins every nondaemon thread before exiting. Solace Agent Mesh 1.28.7 and Solace AI Connector 3.3.12
create only daemon threads, and the pinned Solace Python API 1.11.0 marks its five explicit threads
daemon as well. Its executors are the exception: `MessagingService`, the transactional service, the
message publisher, the request-reply publisher, the message receiver, and the serialized-executor
helper each construct a `concurrent.futures.ThreadPoolExecutor`, whose workers are created without a
daemon argument and therefore inherit nondaemon status from the main thread. `concurrent.futures.thread`
registers `_python_exit` through `threading._register_atexit`, and that hook joins every such worker
with no timeout. One worker parked in a native SDK call holds the process open forever.

## Decision

The owned entrypoint guarantees that the process terminates. `aerial_rescue_runtime_compat.lifecycle`
gains `terminate_process`, and the module guard becomes
`raise SystemExit(terminate_process(main()))`.

`terminate_process` runs only after `main()` has returned, which means only after the owned lifecycle
has already run `stop()` and `cleanup()` in full and settled on its exit status. It counts the
nondaemon threads other than its own, and while any survive it waits, polling every 0.5 seconds up to
a settle bound of 15 seconds recorded in [operating-parameters.md](../operating-parameters.md). If the
interpreter settles within that bound it returns the status unchanged and ordinary interpreter
shutdown proceeds. If threads still survive the bound, it logs the surviving **count** and nothing
else, flushes the logging handlers, and calls `os._exit` with the same status the lifecycle chose.

The forced exit never invents a status, never runs before cleanup, and never fires while the
interpreter is still capable of exiting on its own.

## Consequences

- A failed Agent Mesh run becomes visible to Compose again: the container exits with the lifecycle's
  status within seconds instead of remaining `Up` with a frozen `RestartCount`, and `restart:
  unless-stopped` can act on it.
- A restart that cannot bind its flows now produces a restart loop, bounded by Docker's backoff, until
  the broker reaps the evicted sessions' temporary endpoints. That is noisy, and it is the honest
  signal: the underlying scarcity is ADR-0196's ceiling, which this decision does not change.
- Negative: `os._exit` skips the remaining `atexit` handlers and any buffered output the owned flush
  does not cover. This is accepted only on the path where the alternative is a process that never
  exits at all, and the owned diagnostics are flushed first.
- Negative: the settle bound is a judgement, not a measurement. Too short truncates a slow but
  legitimate shutdown; too long delays the supervisor. Fifteen seconds sits well above the Connector's
  own bounded joins and well below the container healthcheck's failure window, and a forced exit on an
  ordinary SIGTERM would be evidence that the bound is wrong.
- The owned compatibility layer grows by one function, deepening the coupling ADR-0177 already accepted
  and already commits to removing when Agent Mesh exposes equivalent lifecycle control.

## Alternatives considered

- **Call `os._exit` unconditionally at the end of the entrypoint.** Rejected for the reason ADR-0177
  rejected the vendor main: an unconditional hard exit bypasses ordinary Python cleanup. The forced
  path must remain the exception that fires only after the interpreter has demonstrated it cannot
  exit on its own.
- **Mark the SDK's executor threads daemon.** Rejected because it reaches inside a pinned third-party
  library's thread construction to change semantics its own shutdown logic depends on, and it would
  have to be reapplied and re-attested at every SDK bump.
- **Shorten or bound the Connector's own thread joins.** Rejected because the unbounded join is
  `concurrent.futures`' `_python_exit`, not the Connector's; the Connector already joins with
  timeouts of 0.1 and 1.0 seconds.
- **Add an external watchdog or a container-level stop timeout.** Rejected because it adds a component,
  and a shorter Docker stop timeout only converts the zombie into a `SIGKILL` after the wait — the
  process would still not report its own status.
- **Leave it and rely on the healthcheck.** Rejected because the readiness endpoint is served from a
  daemon thread that dies with the failed flow, so an unhealthy container that never exits is exactly
  the state that was observed for fourteen minutes.
