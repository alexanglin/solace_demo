# Threat model

> **Scope:** the initial release — a single-operator local simulation on one workstation, the PubSub+
> software event broker container, local Ollama models, and a public source repository. Managed
> deployments, multi-tenant operation, and any cloud deployment are out of scope and require their own
> analysis; the Solace Cloud showcase profile in [ADR-0043](../adr/0043-docker-broker-with-solace-cloud-showcase.md)
> is a demonstration, not a deployment this model covers.
>
> **Related:** [../SAFETY.md](../SAFETY.md), [approval-bypass-catalogue.md](approval-bypass-catalogue.md),
> [../LIMITATIONS.md](../LIMITATIONS.md),
> [ADR-0024](../adr/0024-local-operator-api-boundary.md).

## What is being protected

In priority order. The ordering matters: it is what to trade away first under pressure.

1. **The approval boundary.** No automated component may cause a rescue escalation without a human act.
   This is the reason the project exists; every other asset is subordinate.
2. **The audit trail.** The record linking commands, evidence, model decisions, and operator actions must
   be complete and correctly ordered, because it is the only means of reconstructing what happened.
3. **Credentials and tenant identity.** Broker credentials and tenant-specific values must never reach a
   tracked file, a log, a fixture, a screenshot, or a published artifact. This repository is public, and
   this is the one failure that cannot be undone by a later commit.
4. **Operator situational awareness.** The dashboard must not mislead — most concretely, it must never
   present replayed or degraded state as live.
5. **Availability of telemetry and operator control**, which must survive the loss of the agent runtime or
   the models.

## Trust boundaries

| Boundary | Untrusted side | Control |
| --- | --- | --- |
| Model output → domain state | Every Ollama response | Pydantic validation before any event is published; invalid output becomes an explicit abstention, never a silent default ([ADR-0008](../adr/0008-abstention-over-recorded-substitution.md)) |
| Agent proposal → executable command | Everything an agent emits | The deterministic command gateway is the sole publisher of executable commands; agent identities are ACL-denied on those topics ([ADR-0005](../adr/0005-deterministic-command-gateway.md)) |
| Event Mesh Gateway ingress → A2A task | Any allowlisted CloudEvent | Schema validation, then a deterministic domain service normalizes before anything affects state |
| Browser → local API | The browser, and anything that can reach loopback | IPv4/IPv6 loopback-only binding; an exact Host allowlist on every request; the exact configured dashboard Origin on browser mutations; and a per-runtime bearer on state-changing endpoints ([ADR-0024](../adr/0024-local-operator-api-boundary.md)) |
| Recorded fixture → live run | Any NDJSON on disk | Replay is structurally isolated; recorded evidence is never decision-eligible in a live run ([ADR-0009](../adr/0009-isolated-side-effect-free-replay.md)) |
| Upstream dependency → runtime | Agent Mesh and its transitive tree | Pinned versions, locked files, advisories audited on every push and again daily, a recorded override or an expiring waiver for every reported finding, and the authority boundary above |
| Container image → runtime | Every pulled base image and the layers the two Dockerfiles add | Tag-plus-digest pins held by the compose policy gate; Trivy image scans daily in continuous integration, which report every advisory and enforce none, because the only lever on a third-party image is its pin; the enforced control is that each pinned digest is still the newest its tag carries ([ADR-0048](../adr/0048-scan-images-and-deploy-configuration-with-trivy.md), [ADR-0055](../adr/0055-block-on-the-image-pin-not-on-advisories-inside-it.md)) |
| Pull request → continuous integration | Any workflow change, including Dependabot's | zizmor audits every workflow and the Dependabot file offline at the commit stage, no checkout persists its token, CodeQL runs only where the token is writable, and every action is pinned to a commit ([ADR-0049](../adr/0049-audit-workflows-with-zizmor-at-the-commit-stage.md), [ADR-0050](../adr/0050-scan-python-with-codeql-in-continuous-integration-only.md)) |

## Threats

### T1 — Approval bypass

**The primary threat.** Any path from an automated decision to a dispatched escalation without a human
act. Fully enumerated in [approval-bypass-catalogue.md](approval-bypass-catalogue.md) — 35 cases across
replay, races, digest binding, identity, model influence, and mode crossing. Mitigation is architectural:
approval is proposal-digest-bound, single-use, expiring, and consumed atomically by the one component that
may publish. Residual risk is **sociotechnical** — an operator who habitually approves without reading is
not defended against by any of these controls, which is why the presentation requirements exist.

### T2 — Credential and tenant-value disclosure in a public repository

Broker credentials, the local operator bearer, model keys, cloud URLs, and tenant identifiers reaching Git history, fixtures,
screenshots, or exported configuration. **Irreversible once pushed.** Mitigations: the operator bearer is
generated in memory for one API process lifetime and is never persisted or logged; `.env` is ignored and
`.env.example` carries placeholders only; gitleaks runs on the staged diff and again over full history at
pre-push; CI asserts no credential secrets are configured; recorded fixtures are sanitized by a
deny-by-default field allowlist with deterministic pseudonymisation of broker host, message-VPN, client,
and queue names; screenshots are redacted before commit; the stack's own secrets — the broker admin
password, the Postgres password, the Event Management Agent's read-only SEMP credential, and the
per-checkout certificate authority — are files under the ignored `deploy/secrets/` mounted as compose
secrets rather than passed as environment literals, which the compose policy gate enforces
([ADR-0045](../adr/0045-fail-closed-compose-policy-gate.md),
[ADR-0046](../adr/0046-generated-local-certificate-authority.md)). **Residual risk:** the fixture sanitizer is
project-owned code, and a field added to the envelope without updating the allowlist would leak by
default — which is why the allowlist denies by default rather than permitting by default.

### T3 — Unauthorized topic access

A component publishing or subscribing outside its remit — an edge agent on a command topic, the recorder
on anything, replay credentials on a live topic. Mitigations: separate least-privilege broker identities
for the simulator, dashboard, Agent Mesh gateways and agents, recorder, and command gateway, each with an
explicit ACL, plus negative tests asserting denial. **The ACL matrix is load-bearing and must be specified
before the components that depend on it are built** ([ADR-0005](../adr/0005-deterministic-command-gateway.md)).

[ADR-0061](../adr/0061-least-privilege-broker-principals-and-topic-authorization.md) is that
specification: nine authorization roles, two total publish and subscribe tables over the eleven topic
families, a separate A2A grant, `disallow` as every owned ACL profile's default action, and the factory
`default` client username disabled so no denial is bypassable by connecting as it. The negative tests
are catalogue cases B17, B18, and B19; their status is tracked in
[approval-bypass-catalogue.md](approval-bypass-catalogue.md), not here. **Residual risk:** the roles are
coarser than the processes, so three edge agents share one authority.

### T4 — Event spoofing and replay on the data plane

Injecting or replaying application events to fabricate evidence, drive a reassignment, or manufacture a
candidate. Mitigations: ACLs restrict who may publish what; event IDs and command IDs are idempotency
keys; consumers reject stale sequence numbers within a producer's stream; the durable audit ordinal, not a
producer's claim, orders the timeline. **Residual risk:** the initial release does not sign events, so an
identity with publish rights to a topic is trusted on that topic. The ingress adapter calls `envelope.check_topic_binding` from `packages/contracts`, so an event's type,
subject, and identifier parameters are checked against the topic it arrived on; whether the publishing
identity may use that topic at all remains the broker ACL's responsibility.

### T5 — Prompt injection through sensor data

Adversarial text reaching a model and altering its behaviour. **The vision agent reads imagery, so
image-borne injection — instruction text rendered into a photograph — is the concrete case**, not a
hypothetical one. Mitigations, in order of strength: the model has no dispatch authority at all, so a
fully compromised model still cannot escalate; untrusted content is never placed in system-prompt
position; output is schema-validated; and the evaluation set includes an adversarial image and an
unsafe-request refusal subset. **Residual risk:** injection can still degrade evidence quality and waste
search effort, and can push a candidate toward a wrong location. It cannot cause an unapproved action.

### T6 — Malicious or malformed model output

Invalid JSON, schema violations, absurd coordinates, fabricated corroboration, or output crafted to reach
an escalating evidence-score band. Mitigations: validation at the trust boundary; a deterministic feasibility
predicate that rejects schema-valid but physically or geometrically impossible commands; an independence
rule so that observations sharing a drone, model digest, or source frame cannot self-corroborate; and an
escalating band unreachable from a single model-generated source.

### T7 — Vulnerabilities in pinned upstream dependencies

Agent Mesh 1.28.7 pins its dependency tree exactly and is the latest release, so an advisory against a
pinned package has no upstream fix to take. Two cases are decided. `google-adk` 1.18.0 carries
PYSEC-2026-344 (missing authentication, unauthenticated remote code execution), remediated upstream in
1.28.1; the override was tried against the black-box compatibility suite, was unsatisfiable, and the
advisory is an expiring waiver ([ADR-0031](../adr/0031-reject-the-google-adk-version-override.md)).
`asteval` 1.0.6 carried CVE-2026-55244, a sandbox escape reachable from model output through Agent
Mesh's math embeds; the single-package override to 1.0.9 is the case ADR-0031's rule admits, and a
probe proves it against the pinned runtime on every push
([ADR-0047](../adr/0047-override-the-asteval-pin-to-close-cve-2026-55244.md)). A second `google-adk`
advisory concerns forged tool confirmations. Mitigations: the self-hosted Web UI binds to loopback;
enabled surfaces are minimised; Phase 0 must enumerate which framework surfaces the pinned
configuration actually starts and whether any binds beyond loopback; both resolved lockfiles and all
seven stack images are audited on every push and again daily, and every reported finding is fixed,
overridden, or bound to an expiring waiver
([ADR-0026](../adr/0026-expiring-dependency-waivers.md),
[ADR-0048](../adr/0048-scan-images-and-deploy-configuration-with-trivy.md),
[ADR-0051](../adr/0051-rescan-daily-and-let-dependabot-raise-pinned-updates.md)). **Note the
framework-level tool-confirmation advisory is
survivable by design**: Agent Mesh's confirmation mechanism is not this project's approval gate. That is
asserted by case B29, not assumed.

### T8 — Denial of service and resource exhaustion

The workstation, the agent runtime, and one Ollama daemon share a failure and resource domain. Model
eviction, unbounded inference concurrency, telemetry flooding, queue backlog, or unbounded SSE client
buffers can each degrade the system. Mitigations: bounded inference concurrency, sequential model warm-up,
pinned Ollama daemon limits, bounded queues and outboxes with a defined overflow rule, per-client SSE
bounds with a droppable-class allowlist that never includes audit or approval events, and explicit
timeouts everywhere. **Failure must be safe:** loss of the agent runtime or the models degrades to
abstention and preserves telemetry, operator visibility, replay, and the approval boundary.

### T9 — Misleading the operator

Presenting replayed or degraded state as live, showing a proposal that differs from the one being
consumed, or hiding the mode. Mitigations: the mode badge cannot be hidden or confused; the operator is
shown the exact digest and the server re-checks it on submission; abstention is visually distinct from a
low evidence score; and the plan forbids claiming that replay or simulated behaviour is operationally live.

## Out of scope for the initial release

A hostile local user with filesystem access to the workstation; supply-chain compromise of the pinned
wheels and images beyond advisory auditing, image scanning, and the seven-day update cooldown; physical
security; multi-operator authorization and delegation; event
signing; and any deployed or multi-tenant topology. Each is a deliberate exclusion, recorded so it is not
mistaken for an oversight.
