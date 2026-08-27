# ADR-0179: Make the official Agent Mesh checklist the production gate

- **Status:** Accepted
- **Date:** 2026-08-26
- **Deciders:** Alex Anglin
- **Extends:** ADR-0178

## Context

ADR-0178 correctly classifies the single-container Agent Mesh as a development, integration,
acceptance, and reference-demonstration profile rather than a production deployment. It summarizes the
major requirements that a future production profile must satisfy. A summary can nevertheless become a
weaker parallel checklist: an implementation could satisfy its seven broad rows while omitting a
specific requirement that Solace added or that the summary did not enumerate.

Solace describes its current
[Production Readiness Checklist](https://docs.solace.com/Agent-Mesh/Framework/administering/production-readiness-checklist.htm)
as the gate a deployment passes before carrying production traffic. In addition to topology, OIDC,
transport security, durable stores, observability, probes, and rollback, the checklist explicitly
requires secret-resolution canaries, a strong environment-unique session signing key, workload identity
where supported, rehearsed rotation and on-call vault access, enabled audit delivery with aggregator-side
retention, exact task/feedback/SSE retention, disk-full protection, and an immutable production binary or
image selection. Those are independently testable obligations, not details safely implied by a broad
heading.

## Decision

The version-current official Solace Agent Mesh Production Readiness Checklist is the complete production
gate. ADR-0178 remains the local topology decision and minimum summary; it is not an alternative checklist
and cannot be used to waive, replace, or infer an upstream checkbox.

Before any repository artifact is described as a production Agent Mesh profile, its release evidence
must:

1. record the selected supported Agent Mesh version, the official checklist URL, retrieval date, and the
   complete checkbox inventory reviewed for that version;
2. map every official checkbox to target-specific configuration, an executable or operational evidence
   reference, an owner, and a pass result;
3. retain every unchecked item as a dated exception with rationale, impact, compensating control,
   remediation owner, and expiry or review date; and
4. obtain the target-specific experienced-operator review required by the official sign-off section.

The mapping must include the following closed evidence groups even when another ADR uses a broader name:

| Evidence group | Required target-specific proof |
| --- | --- |
| Secret resolution and custody | No literal credentials in YAML; every substitution or file mount resolves nonempty at boot; a canary task proves credential resolution before traffic; the session-cookie signing key has at least 32 bytes of entropy and is unique per environment; supported object stores use workload identity; a high-impact rotation is rehearsed in staging and revokes the old value; and at least one current on-call operator can access the secret manager. |
| Audit delivery and retention | Audit logging remains enabled, structured JSON reaches the selected aggregator, and the aggregator enforces the accepted immutable retention contract. Runtime log emission alone is not retention evidence. |
| Data retention and capacity | `task_retention_days`, `feedback_retention_days`, and `sse_event_retention_days` are set to the accepted reach-back requirements; the sweep is observed; log rotation or a disk-full alert prevents an unbounded local log from exhausting the workload filesystem. |
| Reproducible production artifact | The deployment manifest pins the exact supported binary version or complete container image digest. A floating tag, rebuild-from-source instruction, or local Compose pin does not qualify the production manifest. |

The remaining official groups—deployment topology, broker tier, PostgreSQL and artifact durability,
OIDC/RBAC, all three TLS boundaries, certificate monitoring, backups and clean restore, structured logs,
metrics, trace correlation, alerts and dashboards, differentiated per-workload probes, runbooks and
access, ordered upgrade, automatic rollback, and final operator sign-off—remain required exactly as the
official checklist states. The release mapping, rather than this ADR, carries their current wording and
target evidence.

The checklist is re-read whenever the Agent Mesh version changes and immediately before production
sign-off. If the official checklist has changed since the last mapping, the new or changed items enter the
release gate before traffic; a prior green mapping does not grandfather them away. If the official page is
unavailable, production sign-off stops rather than silently falling back to ADR-0178's summary.

This decision adds no production deployment to the current repository and does not weaken ADR-0178's
explicit nonproduction classification of the local profile.

## Consequences

- A local summary can no longer drift into a false completeness claim.
- Specific secret, audit, retention, capacity, and artifact controls have named evidence even though the
  current local profile does not attempt production qualification.
- A future production release has to preserve a dated, target-specific mapping and exceptions ledger,
  which adds review and operational evidence work.
- Upstream checklist changes can block production sign-off until the deployment demonstrates the new
  requirement; this is deliberate fail-closed maintenance behavior.

## Alternatives considered

- **Expand ADR-0178's seven rows and treat that expansion as complete.** Rejected because another summary
  can drift again and Accepted ADR prose is not rewritten after acceptance.
- **Pin only the checklist wording current on 2026-08-26.** Rejected because a production claim must be
  reviewed against the supported product version and current official safety guidance, not a permanently
  frozen paraphrase.
- **Permit broad evidence groups to imply their detailed controls.** Rejected because a generic “secrets”
  pass does not prove nonempty resolution, signing-key entropy, rotation, revocation, or on-call access;
  the same problem applies to audit and retention.
- **Allow unchecked items without structured exceptions.** Rejected because the official checklist
  requires known gaps to remain visible to the next operator, and an unowned blank checkbox is unmanaged
  risk.
