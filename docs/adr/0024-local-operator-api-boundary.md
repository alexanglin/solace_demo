# ADR-0024: Protect local mutations with loopback, Host, Origin, and a per-runtime bearer

- **Status:** Superseded by ADR-0096
- **Date:** 2026-08-19
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

The dashboard API is a single-operator surface on the reference workstation, not a multi-user service.
Loopback binding limits network reachability, but it does not by itself stop a hostile web page from
targeting a local service or a DNS-rebinding name from resolving to loopback. An Origin check alone does
not stop DNS rebinding, and a client-supplied `operator_identity` would let an untrusted request choose the
identity recorded against an approval.

The API also exposes deliberately read-only routes used for health probes, readiness, scenario discovery,
and the dashboard event stream. Requiring a credential on those routes would complicate local probes and
SSE without strengthening the state-change boundary. The contract therefore needs to say precisely which
controls apply to every request and which apply only to a state-changing request.

## Decision

Bind the dashboard API only to IPv4 or IPv6 loopback addresses, never to a wildcard or non-loopback
interface. Validate every request's parsed `Host` value against the exact configured API host-and-port
allowlist; do not accept wildcard, suffix, or substring matches. Reject a missing, malformed, duplicated,
or non-allowlisted Host before route handling.

For browser requests to a state-changing endpoint, require the parsed `Origin` tuple to equal the one
configured dashboard origin, including scheme, host, and port. Do not accept a wildcard origin, the
`null` origin, a suffix match, or an omitted Origin from the browser dashboard.

Generate one cryptographically random bearer credential at each dashboard API process start, with the
entropy defined in [operating-parameters.md](../operating-parameters.md#local-operator-credential).
Invalidate it when that process exits. Require it as `Authorization: Bearer <credential>` on exactly these
state-changing endpoints:

- `POST /api/v1/scenarios/{scenarioId}/start`
- `POST /api/v1/scenarios/current/reset`
- `POST /api/v1/missions/{missionId}/approvals`

Never accept the credential from a cookie, query parameter, request body, or URL. Never persist or log its
value. The credential-free routes are `GET /api/v1/health`, `GET /api/v1/readiness`,
`GET /api/v1/scenarios`, and `GET /api/v1/events`; Host validation still applies to them.

Successful validation of the current runtime's bearer is the sole source of the non-secret local
`operator_identity` recorded for an approval. A request body cannot supply or override that identity, and
the bearer value itself is never written to the audit trail.

## Consequences

- A browser page cannot mutate the simulation merely because it can reach loopback; it must pass the
  independent Host, Origin, and bearer checks.
- DNS rebinding is rejected on every route, including credential-free reads.
- Restarting the API invalidates the prior bearer, so the dashboard must receive the new credential through
  the local startup path and retry only after re-establishing that runtime context.
- Health probes, readiness probes, scenario discovery, and SSE remain usable without bearer configuration.
  Their synthetic read-only data is intentionally outside the credential boundary.
- This is not an account system, role model, delegation mechanism, or proof of real-world authority. A
  hostile local user who can inspect the process or its memory remains outside the initial threat model.
- Contract and security tests must cover wildcard binding, missing and malformed Host, DNS rebinding,
  wrong or missing browser Origin, absent and stale bearer credentials, and body-supplied operator identity.

## Alternatives considered

- **Loopback binding and an Origin allowlist only.** Rejected: Origin does not stop DNS rebinding, and
  loopback does not establish which local browser action authorized a mutation.
- **Require the bearer on every endpoint.** Rejected: read-only health, readiness, discovery, and event
  streaming do not change state, while credentialing them would complicate probes and SSE.
- **Use a durable user database and role-based access control.** Rejected: multi-user identity, delegation,
  and authorization are explicitly outside the single-operator local simulation.
- **Put the bearer in a cookie, query parameter, or request body.** Rejected: cookies are ambient authority
  exposed to CSRF concerns, URLs leak through history and diagnostics, and a body field can be confused
  with domain data. The explicit authorization header keeps the trust boundary visible.
