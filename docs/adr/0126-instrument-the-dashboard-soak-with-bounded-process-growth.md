# ADR-0126: Instrument the dashboard soak with bounded process growth

- **Status:** Accepted
- **Date:** 2026-08-25
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

The operating profile required a thirty-minute dashboard soak with no unbounded process, queue, or SSE
client growth, but there was no committed command that could pass or fail that statement. Merely leaving
a page open would exercise the stack without measuring it. A finite run also cannot prove mathematical
boundedness unless it declares a concrete observation cadence and an accepted growth envelope.

The browser needs to prove its normal production surface remains connected and ready throughout the
same interval. Resource sampling must not add a production endpoint or browser-global test hook, and it
must target only the explicitly named disposable mission-control project.

## Decision

Add one dedicated production Playwright soak configuration and one case outside both the fixed 64-case
fixture inventory and the normal production workflow inventory. The case runs for exactly thirty minutes
and takes 61 samples including both endpoints at a thirty-second cadence.

At every sample it records the rendered connection state, mode-specific readiness, map visibility,
alerts, latest audit ordinal, and the dashboard API process sample. The runner resolves the dashboard API
container from the explicit Compose project, records its container identity and Docker-reported host PID,
and executes a fixed, credential-free Python probe inside that container to read PID 1's RSS and open file
descriptor count from `/proc`. No production HTTP route, query switch, bootstrap field, browser global, or
request interception is added.

The run fails if the container or PID changes, if any sampled RSS exceeds the post-connect baseline by
more than 64 MiB, or if any sampled descriptor count exceeds that baseline by more than eight. It also
fails on any non-`CONNECTED` transport sample, non-`READY` readiness sample, hidden map, alert, regressed
audit ordinal, remote browser request, missing sample, or malformed process probe. The normal production
inventory remains serial and orders the replay case last; the soak is invoked separately through
`pnpm --dir apps/dashboard run test:e2e:soak`.

## Consequences

- The soak target has a real bounded instrument rather than an unevaluated elapsed-time claim.
- Sixty-four MiB and eight descriptors are release envelopes, not assertions that healthy runtimes use no
  additional resources. Measurements at the limit require investigation before any proposed change.
- Restarting the dashboard API fails the soak even if readiness recovers, because replacement would hide
  the resource history being measured.
- The runner requires Docker access and a uniquely named disposable mission-control project. Ordinary
  unit, integration, fixture Playwright, and normal production Playwright runs do not invoke it.
- Browser state and process resources are sampled on the same cadence, making a transport failure and a
  process-growth failure independently visible.

## Alternatives considered

- **Leave the page open for thirty minutes without sampling.** Rejected because it does work but produces
  no falsifiable resource evidence.
- **Use browser heap values as the process authority.** Rejected because browser-specific heap APIs do not
  measure the dashboard API's SSE buffers or file descriptors.
- **Expose process statistics from the dashboard API.** Rejected because acceptance convenience must not
  enlarge the production attack surface.
- **Allow environment overrides for the accepted bounds.** Rejected because a release run could silently
  weaken the criterion it claims to prove.
