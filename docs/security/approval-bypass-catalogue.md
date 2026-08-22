# Approval-bypass catalogue

> **Why this exists:** [operating-parameters.md](../operating-parameters.md) carries the release target
> "zero authorized actions across all approval-bypass attempts". That target quantifies over a set, and a
> target whose set is not enumerated cannot fail. This document is that set.
>
> **Related:** [ADR-0005](../adr/0005-deterministic-command-gateway.md),
> [ADR-0006](../adr/0006-proposal-bound-single-use-approvals.md),
> [ADR-0009](../adr/0009-isolated-side-effect-free-replay.md),
> [ADR-0024](../adr/0024-local-operator-api-boundary.md), [SAFETY.md](../SAFETY.md).

Every row is a test case. Each must have a named negative test asserting that **no authorized command is
published** and that the attempt is recorded in the audit trail. Adding a row is cheap; removing one
requires an ADR. `Status` is the state of the test, not of the threat.

## Replay and duplication

| # | Attempt | Required outcome | Status |
| --- | --- | --- | --- |
| B01 | Re-POST an already-consumed approval with the same idempotency key | `409`, hard denial, audited as a bypass attempt. Explicitly **not** an idempotent success ([ADR-0006](../adr/0006-proposal-bound-single-use-approvals.md)) | to build |
| B02 | Re-POST a consumed approval with a *fresh* idempotency key | `409`, hard denial, audited | to build |
| B03 | Reuse one idempotency key with a different request body | `409`, refused on body-hash mismatch | to build |
| B04 | Redeliver the escalation command with the original command ID after it completed | Prior result returned; no second dispatch | to build |
| B05 | Redeliver the escalation command with a new command ID but the same approval | Refused; the approval is spent | to build |
| B06 | Replay a captured approval CloudEvent onto the broker directly | Refused; the approval store, not the event, is the authority | to build |

## Race conditions and timing

| # | Attempt | Required outcome | Status |
| --- | --- | --- | --- |
| B07 | Two concurrent consumptions of the same approval | Exactly one succeeds; the other is denied. Asserted under real concurrency, not sequentially | to build |
| B08 | Consume an approval that expired between validation and publication | Refused; expiry is evaluated inside the same transaction as consumption | domain rule tested (`packages/domain/tests/test_approvals.py`, `test_b08_…`); store, gateway, and HTTP halves to build |
| B09 | Consume with the system clock moved backwards | Refused; expiry does not rely on a mutable wall clock alone | domain rule tested (`packages/domain/tests/test_approvals.py`, `test_b09_…`); store, gateway, and HTTP halves to build |
| B10 | Approve a proposal, then supersede it, then consume | Refused; a superseded proposal is not approvable | domain rule tested (`packages/domain/tests/test_approvals.py`, `test_b10_…`); store, gateway, and HTTP halves to build |
| B11 | Kill the process mid-consumption, restart, retry | No duplicate dispatch and no lost approval. Backs the RPO-0 target | to build |

## Digest and parameter binding

| # | Attempt | Required outcome | Status |
| --- | --- | --- | --- |
| B12 | Alter an action parameter after approval, keeping the proposal ID | Refused on digest mismatch | domain rule tested (`packages/domain/tests/test_approvals.py`, `test_b12_…`); store, gateway, and HTTP halves to build |
| B13 | Add a field to the proposal outside the digest's covered field set | Impossible: the proposal schema sets `additionalProperties: false` | to build |
| B14 | Exploit alternate coordinate representations or float formatting to alias two different coordinates before hashing | Impossible: no floating-point value is representable in a digest-covered payload and coordinates are integer microdegrees, so distinct coordinates cannot share a canonical form. A real number at the boundary is rejected, never coerced ([ADR-0027](../adr/0027-integer-only-canonical-serialization.md), [CONTRACTS.md](../CONTRACTS.md#canonical-serialization)) | to build |
| B15 | Present an approval for mission A against a proposal in mission B | Refused; the binding includes the mission ID | domain rule tested (`packages/domain/tests/test_approvals.py`, `test_b15_…`); store, gateway, and HTTP halves to build |
| B16 | Present an approval whose `scoreVersion` differs from the candidate's | Refused; the score version is inside the digest | domain rule tested (`packages/domain/tests/test_approvals.py`, `test_b16_…`); store, gateway, and HTTP halves to build |

## Authority and identity

| # | Attempt | Required outcome | Status |
| --- | --- | --- | --- |
| B17 | Publish to an executable command topic using an edge-agent identity | Denied by broker ACL. This is the load-bearing control in [ADR-0005](../adr/0005-deterministic-command-gateway.md) | **proven live** against the container (`tests/security/test_broker_authorization.py`, `test_b17_…`), with the command gateway publishing the same topic as the positive control ([ADR-0061](../adr/0061-least-privilege-broker-principals-and-topic-authorization.md)) |
| B18 | Publish to a command topic using the Event Mesh Tool identity | Denied by ACL; the tool may reach only command-gateway request topics | **proven live** (`tests/security/test_broker_authorization.py`, `test_b18_…`); the tool's one permitted family is asserted in the same run |
| B19 | Publish using the recorder or dashboard identity | Denied by ACL | **proven live** (`tests/security/test_broker_authorization.py`, `test_b19_…`); the recorder is separately asserted to hold no publish grant on any family |
| B20 | Drive an escalation from the Agent Mesh Web UI on loopback `:8000` | No effect; that surface cannot dispatch or approve | to build |
| B21 | Reach a state-changing endpoint without the current runtime's bearer, with a prior runtime's bearer, or with a credential outside the authorization header | Refused; for approval, the current bearer is the sole source of the operator identity (`operatorIdentity` on the wire) ([ADR-0024](../adr/0024-local-operator-api-boundary.md)) | to build |
| B22 | Reach any local API endpoint from a rebound DNS name resolving to loopback | Refused by the exact Host allowlist on every request, which an Origin check alone does not stop ([ADR-0024](../adr/0024-local-operator-api-boundary.md)) | to build |
| B23 | Escalate an action type absent from the command-authority table | Refused; the table is deny-by-default | domain rule tested (`packages/domain/tests/test_authority.py`, `test_b23_…`); store, gateway, and HTTP halves to build |
| B24 | Write an `APPROVED` row directly into the durable store, then dispatch | Detectable in the audit trail; the state machine is not the only guard | to build |

The three rows above are the first catalogue cases settled against a running broker rather than a
unit test. The state they were measured against, including the state before the matrix was applied —
in which an identity that did not exist published an executable rescue-escalation command — is in
[`release-evidence/phase-0/broker-authorization.md`](../../release-evidence/phase-0/broker-authorization.md).

## Model and ingress influence

| # | Attempt | Required outcome | Status |
| --- | --- | --- | --- |
| B25 | Model output that asserts it is already approved, or emits an approval token | No effect; approval is never derived from model output | domain rule tested (`packages/domain/tests/test_approvals.py`, `test_b25_…`); store, gateway, and HTTP halves to build |
| B26 | Prompt injection via sensor text aimed at triggering escalation | No effect; the model has no dispatch authority | to build |
| B27 | Prompt injection rendered **into an image** the vision agent reads | No effect; same authority boundary. Requires an adversarial image in the evaluation set | to build |
| B28 | Forge a salient CloudEvent into the Event Mesh Gateway to fabricate a proposal | Proposal may be created but is not approved; escalation still blocked | to build |
| B29 | Exploit the `google-adk` tool-confirmation advisory to forge a framework-level approval | No effect: Agent Mesh's confirmation mechanism is **not** this project's approval gate. This is the concrete payoff of [ADR-0005](../adr/0005-deterministic-command-gateway.md) and must be asserted, not assumed | to build |

## Mode and provenance crossing

| # | Attempt | Required outcome | Status |
| --- | --- | --- | --- |
| B30 | Consume an approval while in replay mode | Impossible: the approval writer refuses construction ([ADR-0009](../adr/0009-isolated-side-effect-free-replay.md)) | to build |
| B31 | Feed recorded evidence into a live run to reach an escalating evidence-score band | Refused, naming the source, rather than scored as zero; recorded evidence is never decision-eligible live ([ADR-0008](../adr/0008-abstention-over-recorded-substitution.md), [ADR-0076](../adr/0076-evidence-score-bands.md)) | domain rule tested (`packages/domain/tests/test_scoring.py`, `test_b31_…`); the evidence service's use of it is to build |
| B32 | Reach an escalating evidence-score band from a single model-generated observation | Impossible by construction of the evidence-score band rule: the escalating band requires two distinct sources, and that rule reads neither the boundaries nor the origins, so no boundary value reaches it from one source ([ADR-0076](../adr/0076-evidence-score-bands.md)) | domain rule tested (`packages/domain/tests/test_scoring.py`, `test_b32_…`), including under the lowest boundaries the range permits; the evidence service's use of it is to build |
| B33 | Double-submit the approval control in the UI | One approval; the control is disabled on submit and the server denies the second | to build |
| B34 | Approve while the displayed proposal differs from the one being consumed | Refused; the operator is shown the digest and the server re-checks it | to build |
| B35 | Send a browser mutation with a missing, `null`, malformed, or non-allowlisted `Origin` | Refused before route handling by the exact browser-Origin allowlist ([ADR-0024](../adr/0024-local-operator-api-boundary.md)) | to build |
