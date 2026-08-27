# ADR-0164: Require TLS 1.3 for PubSub+ clients

- **Status:** Accepted
- **Date:** 2026-08-25
- **Deciders:** Alex Anglin
- **Supersedes in part:** ADR-0153's TLS 1.2 minimum

## Context

ADR-0153 required certificate-expiry and hostname validation but retained TLS 1.2 as the minimum
protocol. The applicable-practice review in ADR-0159 found that both ends of the supported reference
path are project-owned and pinned: PubSub+ Software Event Broker 10.26.0.8799 and the Python
`solace-pubsubplus` 1.11 client. That path supports TLS 1.3, so continuing to admit TLS 1.2 would preserve
compatibility the supported topology does not need and would conflict with ADR-0159's stronger TLS row.

## Decision

Every application-data-plane PubSub+ client in the supported reference topology requires TLS 1.3. The
shared broker adapter configures the SDK's minimum secure protocol as TLS 1.3, continues to require
certificate-expiry and hostname validation, trusts only the project CA selected by deployment, and
refuses every plaintext transport before broker I/O. No service may weaken that shared setting or build
an alternate client path.

Deterministic tests must prove the exact SDK property selection and that every composition uses the
shared adapter. Live release evidence must negotiate TLS 1.3 successfully and refuse plaintext, a wrong
host, an expired certificate, and a TLS-1.2-only endpoint. A Linux production-container connection is
the authority for the production claim; a bundled development OpenSSL probe is compatibility evidence
only.

A deployment that must interoperate with a TLS-1.2-only broker is unsupported until a new accepted ADR
names the external constraint, bounds its lifetime, and adds separate deployment and negative-downgrade
evidence. It may not silently lower the shared default through an environment variable.

## Consequences

- ADR-0153's bounded session, identity, retry, keepalive, buffer, readiness, and shutdown decisions remain
  accepted; only its TLS 1.2 minimum is replaced.
- The implementation and ADR-0159 now have one unambiguous TLS minimum.
- A TLS-1.2-only broker fails at connection setup rather than creating a weaker operational mode.
- Negative: older brokers and intercepting middleboxes without TLS 1.3 are not supported by the reference
  deployment.

## Alternatives considered

- **Keep TLS 1.2 for compatibility.** Rejected because the project pins and owns both supported endpoints,
  and no accepted deployment requires that compatibility.
- **Make the minimum protocol configurable per service.** Rejected because service-local downgrade knobs
  would fragment the transport boundary and make deployment evidence ambiguous.
- **Prefer TLS 1.3 but allow automatic fallback.** Rejected because a preference is not a security
  boundary and cannot prove that the negotiated production path used the stronger protocol.
