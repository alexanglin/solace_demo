# ADR-0207: Drain the recorder fan-in without a wait per channel

- **Status:** Accepted
- **Date:** 2026-08-29
- **Deciders:** Alex Anglin
- **Supersedes in part:** none

## Context

The deployed recorder is `console.py` → `RecorderBrokerReceiver`. Its `receive()` polls **one**
channel and returns **at most one** message, and `serve()` calls it once per iteration. The receiver's
channels are the Direct receiver plus every durable queue name, which for the reference fleet is ten.
Every poll spent the full `RECEIVE_WINDOW_MILLISECONDS` of 1,000 ms.

An idle durable queue therefore consumes a whole second and yields nothing. One complete revolution of
the fan-in costs about ten seconds and admits at most one message. Measured on the running stack, a
mission whose fleet reported 280 publications produced telemetry into the durable store at roughly one
event per ten seconds — against a scenario that emits 280 events across fourteen one-second ticks.
The recorder was never overloaded; it was serialised behind its own idle channels.

`docs/operating-parameters.md` already carried two rows that disagree about this, because the recorder
has two compositions (`TECH_DEBT.md` §3):

- "Recorder fair per-channel receive window | 1,000 ms for each Direct or durable channel poll" —
  the deployed shape;
- "Recorder poll cycle | one total blocking wait of 100 ms, followed by zero-wait round-robin drain to
  a maximum batch of 64 messages" — the parallel `CaptureLoop.poll_once` shape.

The second is the correct instinct and is what the document's own burst test claims to measure. It was
never true of the composition Compose runs.

## Decision

The recorder spends its blocking wait on the fan-in as a whole, not on each channel.

`RecorderBrokerReceiver` retains the count of consecutive polls that found nothing. A poll waits the
configured receive window only once that count reaches the number of channels — that is, only once a
complete revolution has proved there is nothing to drain. Every other poll uses a zero wait.

The consequences follow directly. While traffic is present the fan-in runs at the producer's rate,
because any admitted message resets the count and the next revolution costs no wait. When the fan-in
is genuinely idle the receiver waits a full window on each poll exactly as before, so an idle recorder
never spins. The transition costs one zero-wait revolution, which is the price of discovering that the
quiet ended.

The receive window keeps its value and its name. This record changes when the window is spent, not how
long it is.

## Consequences

- The recorder admits at the producer's rate rather than at one message per revolution, which is what
  the dashboard needs in order to fold a mission while it is running.
- `docs/operating-parameters.md`'s per-channel row is replaced by a fan-in row that states the same
  window with its new spending rule, so the two rows no longer contradict each other. The parallel
  composition's row keeps its own entry until that composition is retired.
- Negative: a zero-wait poll can return nothing from a channel that is about to deliver, so a message
  may wait one extra revolution. A revolution with traffic is now bounded by the store transaction
  rather than by ten seconds of timeouts, so the delay is far below the previous cost.
- Negative: the receiver is now stateful across calls. A caller that constructs a receiver per poll
  would pay the wait every time; the deployed composition constructs it once, and the type is not
  exported for per-poll use.
- Negative: this does not bound a batch. The parallel composition stops a cycle at 64 messages; the
  deployed receiver returns one message per call and lets `serve()` decide. Bounding the deployed drain
  is a separate decision, and until it exists a sustained burst is bounded only by the per-message
  durable transaction.

## Alternatives considered

- **Shorten the receive window.** Rejected: a window small enough to make ten sequential waits cheap is
  a busy poll, and it would apply to the idle case where the wait is doing useful work.
- **Adopt `CaptureLoop.poll_once` wholesale.** Rejected here: it belongs to the parallel composition
  with its own batch bound and cycle accounting, and porting it would change the deployed serve loop's
  shape as well as its pacing. This record changes one rule and leaves the loop alone.
- **Poll every channel concurrently.** Rejected: the Solace receivers are synchronous and each poll is
  already dispatched to a thread; fanning ten threads per revolution to save idle time trades a bounded
  wait for unbounded concurrency against a five-connection store pool.
- **Deploy the parallel composition instead.** The real fix, and out of scope here. `TECH_DEBT.md` §3
  still carries it.
