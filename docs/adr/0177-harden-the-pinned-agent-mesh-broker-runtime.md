# ADR-0177: Harden the pinned Agent Mesh broker runtime

- **Status:** Accepted
- **Date:** 2026-08-26
- **Deciders:** Alex Anglin
- **Extends:** ADR-0164
- **Supersedes in part:** ADR-0044's Agent Mesh image invocation and plugin-install location, and
  ADR-0030's Agent Mesh-domain `solace-pubsubplus` 1.9.0 resolution

## Context

ADR-0164 requires TLS 1.3, certificate/date/hostname validation, bounded initial connection and active
reconnection, truthful readiness, graceful cleanup, and nonzero recovery exhaustion from every
application-data-plane PubSub+ client. The project-owned broker adapter enforces that policy, but the
pinned Solace Agent Mesh 1.28.7 runtime does not use that adapter. Its Solace AI Connector 3.3.12
dependency constructs each messaging service from its own property dictionary and pins
`solace-pubsubplus==1.9.0`.

The pinned Connector source has two `MessagingService.builder()` call sites. Both select the same
`RetryStrategy.forever_retry` value for initial connection and active reconnection, and neither supplies
a TLS minimum or explicit certificate controls. Its process main also funnels requested shutdown,
normal completion, retry exhaustion, and unexpected exceptions through a nested shutdown function that
calls `os._exit(0)`. A Compose restart policy and readiness probe cannot recover truthfully if the process
reports an unexpected broker failure as success.

Solace documents that Python API TLS 1.3 requires version 1.10 or later, that the default minimum remains
TLS 1.2, and that secure messaging uses `tcps` with certificate validation enabled. The API reference
defines the minimum-protocol, trust-store, reject-expired, hostname-validation, connection retry,
reconnection, and keepalive property keys used here. See
[Messaging Service](https://docs.solace.com/API/API-Developer-Guide-Python/Python-API-Messaging-Service.htm),
[Supported Environments](https://docs.solace.com/API/API-Developer-Guide-Python/Python-API-supported-Environments.htm),
and the
[Python property reference](https://docs.solace.com/API-Developer-Online-Ref-Documentation/python/source/rst/solace.messaging.config.solace_properties.html).

The current local single-container Agent Mesh remains an integration/reference topology, not a qualified
production topology; ADR-0178 governs that applicability boundary. This decision closes the practices
that are applicable inside the supported local image without asserting the production checklist.

## Decision

The isolated Agent Mesh project overrides only the Connector's SDK leaf from 1.9.0 to exactly
`solace-pubsubplus==1.11.0`. The supported combination is exactly Agent Mesh 1.28.7, Solace AI Connector
3.3.12, and Solace Python API 1.11.0. The lock carries only the macOS universal and Linux aarch64 wheels
selected by ADR-0010; the derived runtime image installs the Linux wheel by its frozen SHA-256 hash.
Runtime startup refuses any other installed tuple.

An owned compatibility package wraps `MessagingService.builder` before constructing the Connector. Every
builder produced through either attested upstream call site is a chaining proxy that:

- refuses a missing host, `tcp`, `ws`, `wss`, and every scheme other than `tcps` before calling the SDK;
- refuses a missing or blank trust-store path before calling the SDK;
- overwrites caller-selected transport values with TLS minimum `TLSv1.3`, certificate validation true,
  reject-expired true, and validate-servername true;
- sets connection-attempt timeout to 1,000 milliseconds, initial connection retries to 2, and per-host
  connection retries to 0;
- replaces the Connector's shared forever strategy with 2 initial retries at 1,000 milliseconds and 30
  active-session reconnection attempts at 1,000 milliseconds;
- sets keepalive interval to 3,000 milliseconds and the without-response limit to 3; and
- attaches a real SDK `ServiceInterruptionListener` to every built service. The first terminal
  interruption marks recovery exhausted and wakes the process stop signal exactly once.

The Connector source shape is a compatibility contract. Deterministic sentinels require the exact two
builder sites, retry-builder sites, initial/active retry calls, and upstream zero-exit shape. Any upstream
change blocks the upgrade until the wrapper is reviewed. The SDK distribution has no `py.typed` marker;
this ADR admits the single exact mypy suppression needed to subclass its runtime-checked
`ServiceInterruptionListener`. No other owned type suppression follows from that exception.

The image no longer invokes `sam run` or `solace_ai_connector.main:main`. Its owned entrypoint uses only
the system environment, discovers and merges the pinned configuration through the upstream loaders,
installs SIGINT/SIGTERM handlers, constructs `SolaceAiConnector`, and always runs `stop()` then
`cleanup()`. Only an explicitly requested signal shutdown can return zero. Broker retry exhaustion,
unexpected flow completion, construction/runtime exceptions, stop failure, and cleanup failure return
nonzero after the maximum safe cleanup. Diagnostics contain only the exception class, never upstream
exception text or configuration.

The derived image uses the pinned official Agent Mesh image unchanged as its base. A hashed, no-dependency
builder stage installs the two Event Mesh plugins and SDK 1.11 into `/opt/plugins`; the final non-root
stage copies that closure plus the two project-owned packages into the fixed
`/opt/aerial-rescue-runtime` leaf overlay and selects it with a fixed `PYTHONPATH`. The Docker build context
re-includes only those two owned package directories; the isolated virtual environment, configuration,
tests, and lock remain outside the image context.

Deterministic acceptance covers hostile caller properties, exact retry objects and service properties,
terminal-listener behavior, supported-version metadata, upstream source shape, redacted lifecycle failure,
shutdown ordering, hashed image shape, duplicate-free Compose loading, and every existing Agent Mesh
compatibility probe. A Linux live probe must connect successfully with TLS 1.3 and must refuse a
TLS-1.2-only endpoint before the image is release evidence; a macOS SDK probe is compatibility evidence,
not that Linux production-container claim.

## Consequences

- Agent Mesh, Event Mesh Gateway, and Event Mesh Tool sessions can no longer silently inherit TLS 1.2 or
  unbounded Connector retries.
- Connector-selected retry values and insecure schemes are refused or replaced at the last shared seam
  before SDK construction, including plugin-created SAC messaging services.
- Terminal broker recovery becomes observable to Compose as nonzero process termination, while ordinary
  SIGINT/SIGTERM retains graceful cleanup and zero status.
- The SDK override is narrow, frozen, hashed, runtime-guarded, and exercised against every symbol the
  pinned plugins use; an upstream Connector pin change deliberately makes the review tests fail.
- Negative: this is an owned compatibility layer around upstream internals. It must be removed or revised
  when Agent Mesh exposes equivalent TLS, retry, and lifecycle controls.
- Negative: live Linux TLS-1.2 refusal remains a release gate and cannot be inferred from deterministic
  property inspection or a macOS wheel.
- This hardening does not qualify the single-container topology for production; ADR-0178 still governs
  identity, workload separation, durable state, availability, and operations.

## Alternatives considered

- **Rely on SDK defaults.** Rejected because the documented minimum defaults to TLS 1.2 and the pinned
  Connector supplies forever retry.
- **Enforce TLS 1.3 only at the broker.** Rejected because the supported broker service does not provide a
  per-client substitute for the application boundary and the same image may target another supported
  broker; client construction must prove its own minimum.
- **Patch or vendor Solace AI Connector source.** Rejected because a small source-shape-attested builder
  and lifecycle wrapper preserves the released upstream artifact and makes the compatibility exception
  explicit.
- **Keep SDK 1.9 and set an undocumented property.** Rejected because Solace documents TLS 1.3 support
  only from Python API 1.10 onward.
- **Continue invoking the vendor main and infer failure from readiness.** Rejected because its unconditional
  zero exit destroys the terminal distinction Compose needs and bypasses ordinary Python cleanup.
- **Allow WSS on port 443 as an automatic fallback.** Rejected because ADR-0178 selects `tcps` for the
  local Agent Mesh path. A restricted-network profile needs a separate accepted decision and evidence;
  it may not widen this image silently.
