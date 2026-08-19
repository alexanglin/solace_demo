# Safety, privacy, and security

> **Authority:** this document is the single home for the safety invariants, the approval protocol, and the privacy and security posture. `docs/IMPLEMENTATION_PLAN.md` and
> `AGENTS.md` reference it and must not restate it ([ADR-0016](adr/0016-documentation-set-split.md)).
> Where this document and an `Accepted` ADR disagree, the ADR governs.
>
> **Related:** [ADR-0005](adr/0005-deterministic-command-gateway.md) (command gateway), [ADR-0006](adr/0006-proposal-bound-single-use-approvals.md) (approval binding), [ADR-0013](adr/0013-sar-artifact-imagery-policy.md) (imagery policy), [ADR-0009](adr/0009-isolated-side-effect-free-replay.md) (replay containment), [ADR-0024](adr/0024-local-operator-api-boundary.md) (local operator boundary). The threat model is in [security/threat-model.md](security/threat-model.md) and the enumerated bypass attempts in [security/approval-bypass-catalogue.md](security/approval-bypass-catalogue.md).

- Keep the project defensive and humanitarian. Military personnel recovery is a future use case, not a separate executable scenario for the initial release.
- Do not identify individuals. A candidate is represented as an anonymous rescue subject.
- The detection target is search-and-rescue **artifacts** — a high-visibility jacket, tarp, pack, tent, reflective panel, or disturbed ground — composited onto public-domain wilderness backgrounds by a committed script. Photographs of real people are forbidden regardless of claimed license, as are photorealistic AI-generated faces. Thermal evidence is synthesised structured data, not imagery. Every image carries a per-scenario asset record with source URL, verbatim license text, retrieval date, checksum, compositing-script hash, and a no-identifiable-person statement ([ADR-0013](adr/0013-sar-artifact-imagery-policy.md)).
- Never place broker credentials, the local operator bearer, model keys, cloud URLs containing credentials, or tenant identifiers in tracked files.
- Provide `.env.example` with names and safe placeholders only; `.env` is ignored.
- Create separate least-privilege broker clients for the simulator, dashboard, Agent Mesh gateways and agents, recorder, and command gateway. Only the command gateway can publish executable or authorized command topics.
- Use `tcps` with hostname and certificate validation. Document WSS on port 443 as a restricted-network fallback.
- Validate all event payloads and model results before use.
- Derive an approval's `operator_identity` only from the successfully validated current-runtime bearer;
  reject any identity supplied by the request body. The exact HTTP boundary lives in
  [CONTRACTS.md](CONTRACTS.md#local-http-api).
- Redact secrets and sensitive configuration from logs and captured replays.
- Emit structured JSON logs with mission, drone, event, command, correlation, and trace identifiers.
- Maintain a threat model covering credential theft, topic abuse, replay, event spoofing, prompt injection through sensor data, model-output manipulation, denial of service, and unauthorized approval.

The initial approval protocol is `REQUESTED -> APPROVED | REJECTED | EXPIRED | SUPERSEDED -> EXECUTED`. The immutable proposal and decision record include the bearer-derived, non-secret operator identity, issue and expiry times, and exact action parameters. Only the command gateway may move an approved proposal to `EXECUTED`, and it must do so atomically with idempotency and outbox persistence. The initial release emits a simulated rescue-escalation authorization; it does not contact a real dispatch system or control a real aircraft.
