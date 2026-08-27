# Evidence Service Instructions

## 1. Scope and authority

These instructions apply to every file under `services/evidence_service/`. Read the repository-root
[`AGENTS.md`](../../AGENTS.md) first. Its TDD, safety, security, documentation, and version-control
rules still apply.

This active Tier 2 member accepts untrusted evidence observations, independently verifies durable
provenance and canonical hashes, coordinates the pure evidence lifecycle and score, and constructs the
versioned evidence decision and audit publications. Its durable transaction adapters and long-running
broker composition plus Compose entry-point wiring are implemented; live-stack qualification remains
adoption work. Read the authority for each concern before changing it:

| Concern | Authority or reference |
| --- | --- |
| Component responsibility, runtime layout, and operating modes | [`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md) |
| Event envelope, topics, hashes, delivery, and failure semantics | [`docs/CONTRACTS.md`](../../docs/CONTRACTS.md) |
| Safety invariants, privacy posture, and approval boundary | [`docs/SAFETY.md`](../../docs/SAFETY.md) |
| Tier 2 gates, AAA, coverage, and test classes | [`docs/TESTING.md`](../../docs/TESTING.md) |
| Numeric values and their measuring instruments | [`docs/operating-parameters.md`](../../docs/operating-parameters.md) |
| Honest evidence-score and model limitations | [`docs/LIMITATIONS.md`](../../docs/LIMITATIONS.md) |
| Delivery sequence and evidence-panel obligations | [`docs/IMPLEMENTATION_PLAN.md`](../../docs/IMPLEMENTATION_PLAN.md) |
| Model manipulation, provenance, and mode-crossing threats | [`docs/security/threat-model.md`](../../docs/security/threat-model.md) |
| Enumerated approval-bypass attempts, including B31 and B32 | [`docs/security/approval-bypass-catalogue.md`](../../docs/security/approval-bypass-catalogue.md) |
| Pure evidence lifecycle and score | [`packages/domain/AGENTS.md`](../../packages/domain/AGENTS.md) |
| Wire validation, canonicalization, digest, and projection | [`packages/contracts/AGENTS.md`](../../packages/contracts/AGENTS.md) |
| Solace transport, acknowledgement, retry, and shutdown | [`packages/broker/AGENTS.md`](../../packages/broker/AGENTS.md) |
| Durable provenance, inbox/outbox, and audit ordering | [`packages/store/AGENTS.md`](../../packages/store/AGENTS.md) |
| Shared diagnostic primitives and claim limits | [`packages/observability/AGENTS.md`](../../packages/observability/AGENTS.md) |
| Runtime, credentials, healthchecks, and Compose coordination | [`deploy/AGENTS.md`](../../deploy/AGENTS.md) |
| Cross-component test ownership and evidence limits | [`tests/AGENTS.md`](../../tests/AGENTS.md) |
| Contract schema and manifest coordination | [`schemas/AGENTS.md`](../../schemas/AGENTS.md) |
| Golden-fixture ownership and privacy | [`fixtures/AGENTS.md`](../../fixtures/AGENTS.md) |
| Durable evidence provenance and audit authority | [ADR-0003](../../docs/adr/0003-postgres-durable-mission-store.md) |
| Application and Agent Mesh runtime split | [ADR-0004](../../docs/adr/0004-split-python-runtimes.md) |
| Sole executable-command publisher | [ADR-0005](../../docs/adr/0005-deterministic-command-gateway.md) |
| Degraded live simulation must abstain | [ADR-0008](../../docs/adr/0008-abstention-over-recorded-substitution.md) |
| Structurally isolated, side-effect-free replay | [ADR-0009](../../docs/adr/0009-isolated-side-effect-free-replay.md) |
| Prepared artifact imagery and its provenance gate | [ADR-0013](../../docs/adr/0013-sar-artifact-imagery-policy.md) |
| Application-event and Agent Mesh namespace separation | [ADR-0014](../../docs/adr/0014-application-events-separate-from-a2a.md) |
| Tier 2 assignment and Tier 1 evidence ownership | [ADR-0017](../../docs/adr/0017-mutation-tool-score-and-risk-tiers.md) |
| Integer-only canonical serialization | [ADR-0027](../../docs/adr/0027-integer-only-canonical-serialization.md) |
| Topic grammar and event-type binding | [ADR-0036](../../docs/adr/0036-ascii-topic-grammar-bound-to-event-type.md) |
| Closed CloudEvents envelope and refusal order | [ADR-0037](../../docs/adr/0037-cloudevents-envelope-profile.md) |
| Honest scaffold classification | [ADR-0053](../../docs/adr/0053-report-scaffolded-workspace-members-instead-of-failing-them.md) |
| Least-privilege evidence-service broker role | [ADR-0061](../../docs/adr/0061-least-privilege-broker-principals-and-topic-authorization.md) |
| Dashboard projection and never-droppable evidence class | [ADR-0067](../../docs/adr/0067-normalized-dashboard-events-and-reduced-state.md) |
| Event Mesh Gateway temporary-queue limitation | [ADR-0071](../../docs/adr/0071-accept-the-event-mesh-gateway-temporary-data-plane-queue.md) |
| Evidence lifecycle and explicit abstention | [ADR-0075](../../docs/adr/0075-evidence-lifecycle-states.md) |
| Evidence score, bands, and corroboration floor | [ADR-0076](../../docs/adr/0076-evidence-score-bands.md) |
| Durable application transaction and fixed simulation parameters | [ADR-0146](../../docs/adr/0146-define-durable-application-processing.md) |
| Complete Alembic and typed SQLAlchemy Core boundary | [ADR-0151](../../docs/adr/0151-require-migrated-sqlalchemy-durable-tables.md) |
| Complete source-event digest binding | [ADR-0152](../../docs/adr/0152-bind-proposals-to-the-complete-source-event.md) |
| Durable malformed-Guaranteed-ingress refusal | [ADR-0159](../../docs/adr/0159-gate-applicable-solace-best-practices.md) |

An Accepted architecture decision record (ADR) governs if code, tests, deployment, or prose disagrees.
Do not settle an evidence shape, provenance field, hash coverage, score weight, band boundary, source-
independence rule, broker grant, delivery guarantee, mode boundary, or verification change in a
service-local constant or comment. Put each fact in its canonical authority and make the coordinated
change required by the root guide.

## 2. Preserve the current implementation truth

This member is active and measured at Tier 2. Its current production surface is:

| Path | Current responsibility |
| --- | --- |
| `pyproject.toml` | Declares broker, contracts, domain, store, Pydantic, console, Python, build, and Tier 2 dependencies |
| `wire.py` | Independently validates the closed proposal CloudEvent and topic binding |
| `source.py` | Recomputes complete source-event and provenance digests and refuses missing or mismatched authority |
| `evaluation.py` | Derives lifecycle and fixed simulation weights, then delegates scoring to the Tier 1 domain |
| `publication.py` | Builds and self-validates exact evidence-decision and audit events plus the authoritative audit row |
| `processing.py` | Coordinates inbox dedupe, proposal/source reads, evidence, decision, audit, outbox, inbox completion, post-commit acceptance, and durable body-free refusal before malformed-message rejection |
| `source_processing.py` | Validates and durably records salient drone events and their initial sensor provenance before settlement |
| `store_adapter.py` | Maps the service transaction port onto the store-owned SQLAlchemy unit of work without importing SQLAlchemy here |
| `outbox.py` | Bounds recovery work to 50 rows and preserves confirmation, refusal, and ambiguity semantics |
| `runtime.py` | Owns the two durable input channels, schema admission, outbox recovery, readiness, fair bounded polling, and reconnect exhaustion |
| `console.py` | Composes one least-privilege Solace session, lazy SQLAlchemy resources, cancellation, and broker-before-store shutdown |
| `tests/evidence_service_tests/` | Member-local validation, provenance, scoring, transaction, publication, recovery, and refusal evidence |

The console entry point, broker loop, lifecycle/readiness handoff, SQLAlchemy composition, and Compose
entry-point/dependency wiring are application code. Its Compose process probe is liveness rather than the
service's application-readiness state, and no shared-stack run yet proves startup, live settlement,
reconnect recovery, cancellation, or shutdown.

## 3. Keep validation, policy, representation, persistence, and effects separate

The evidence service is a Tier 2 orchestrator around narrower owners. It validates inputs, invokes pure
decisions, coordinates durable effects, and exposes typed outcomes. It does not become a second owner for
the evidence lifecycle, score, wire profile, broker authorization, or storage semantics.

- Use `packages/domain` for `EvidenceState`, `EvidenceEvent`, legal transitions, terminality,
  `Contribution`, score calculation, bands, the recorded-origin refusal, and the distinct-source floor.
  Never copy a transition table, terminal set, saturation rule, band order, boundary comparison, or
  refusal branch into this service.
- Use `packages/contracts` for canonical bytes, domain-separated digests, identifiers, instants, topic
  binding, CloudEvents validation, and dashboard projection. Do not create a service-local canonicalizer,
  hash profile, topic parser, envelope, schema identifier, or normalized dashboard event.
- Keep every direct `solace` import, vendor callback type, settlement primitive, publisher
  acknowledgement, reconnect loop, and transport exception inside `packages/broker`. Vendor objects and
  broad `Any` values must not cross into evidence policy.
- Keep evidence provenance, inbox/outbox state, idempotency results, and the append-only audit timeline
  behind `packages/store`. A process-local cache is not durable authority, and a log line is not an
  evidence or audit record.
- Let the edge agents and Agent Mesh own prompts, model invocation, and agent proposals. This service
  accepts untrusted observations and proposals only through the committed closed contracts and
  independently validates every input at its own boundary; upstream validation is not
  transitive trust. It does not import Agent Mesh internals, call an A2A topic, or acquire model authority
  merely because it validates model output.
- Keep shared logging, metrics, trace helpers, and redaction primitives in `packages/observability` once
  two real consumers establish them. Evidence events are application data, not diagnostic output.
- Agents may propose; only the deterministic command gateway may consume approval and publish executable
  commands. An evidence band, candidate, model result, or audit record never authorizes an action.
- Declare every imported workspace member and third-party distribution in this member's manifest. The
  root environment installs all workspace members together and can mask an undeclared dependency.

Prefer explicit typed ports for observation input, provenance lookup, persistence transaction, broker
transport, clock, identifiers, and configuration. Add a shared abstraction only after two real consumers
need it. Keep package imports side-effect free: no environment or file reads, model loads, clock reads,
random identifiers, sockets, database connections, threads, tasks, or signal handlers at import time.

## 4. Validate observations before they can contribute

Treat every model response, broker message, scenario reference, and persisted row as untrusted at its
boundary. Use Pydantic to validate external shapes, then adapt accepted values into focused typed domain
values. Do not pass loose mappings, caller-owned enums, or unvalidated strings into lifecycle or score
logic.

Preserve the semantics fixed by ADR-0075:

- Refuse a request whose required artifact provenance is absent before creating an evidence item. There
  is deliberately no `REQUESTED` to `REJECTED` edge for work the system should never have started.
- A timeout, transport error, model error, explicit declination, or other failure to assert moves
  `REQUESTED` to `ABSTAINED`. A declination is not an observation.
- Invalid JSON, a schema failure, or another response that cannot become a typed assertion is a model
  failure before `OBSERVED` and follows the abstention path. A successfully parsed assertion moves
  `REQUESTED` to `OBSERVED`; deterministic semantic or feasibility refusal then moves it to
  `REJECTED`, while accepted validation moves it to `VALIDATED`.
- Policy may admit a validated item directly or refer it to `MANUAL_REVIEW`. A human decision then
  admits or dismisses it through the domain table; do not invent a shortcut edge.
- Only `CONTRIBUTING` items may enter scoring. `CONTRIBUTING`, `ABSTAINED`, and `REJECTED` are
  terminal. A contradicting observation is a new item, never a withdrawal or mutation of an admitted one.
- Abstention, rejection, manual review, and a low score are different outcomes. Preserve a typed reason
  for timeout, transport failure, declination, invalid output, policy referral, or human dismissal so the
  future audit trail and evidence panel can explain a shared terminal state.

The lifecycle deliberately carries no record, provenance, artifact hash, score, or reason shape. Define
those shapes through their owning contract and decision rather than smuggling them into the pure state
machine or inventing an ad hoc service dictionary.

Do not trust the model to supply mission authority, artifact identity, source identity, observation
origin, model digest, frame identity, weight, score version, band boundary, corroboration, or lifecycle
state. Derive or verify control fields from authenticated transport, committed configuration, provenance,
and deterministic policy outside model control.

Treat imagery, text rendered into imagery, observation detail, and proposal prose as hostile data. They
never enter a system-prompt position, become instructions, establish an approval, or acquire command
authority. A fully manipulated model may degrade evidence quality or waste search effort; it must still
be unable to authorize an action.

The threat model asks that observations sharing a drone, model digest, or source frame not
self-corroborate. The current Tier 1 score enforces only distinct `source_id` values. Do not claim the
broader T6 independence rule is implemented, and do not hide it inside a clever source-string convention.
Its exact identity and refusal semantics are a safety-boundary decision that must be settled, implemented,
and tested before the service claims independent corroboration.

## 5. Make provenance and hashes explicit, canonical, and durable

Provenance is an admission prerequisite and an operator-visible explanation, not decorative metadata.

- Accept only prepared search-and-rescue artifact imagery that passed ADR-0013's entry gate. Every image
  needs its per-scenario source URL, verbatim license text, retrieval date, checksum, compositing-script
  hash, and no-identifiable-person statement. Never load or commit a photograph of a real person,
  photorealistic generated face, biometric input, or unreviewed runtime capture.
- Thermal evidence is synthetic structured data, not imagery. Keep its origin truthful and do not give it
  an invented image-license record.
- Record the model tag, Ollama version, prompt version, generation parameters, and resolved model digest
  required by the architecture. Preserve the authenticated drone and source-frame identities needed by
  the eventual T6 independence decision; do not accept any of these facts from model-authored output.
- Store accepted source events, provenance, evidence items, decisions, audits, and publications through
  the Alembic- and SQLAlchemy-owned durable-store boundary. An in-memory record or fixture cannot support
  a durability claim.
- Use `aerial_rescue_contracts.digest.digest` with `Context.EVIDENCE` and the contracts package's
  integer-only canonical profile. A digest-covered object declares `canonicalizationVersion`; never
  hash a floating-point score, default JSON bytes, object representation, or provider response directly.
- Preserve the committed evidence document, covered members, schema identity, digest placement, and
  version policy. A change to any of them is a contract and ADR change, not a service-local variation.
- Treat the digest as integrity identity over canonical content. Context separation is not encryption,
  a signature, broker authorization, model attestation, source independence, or proof that an observation
  is true. Preserve full provenance and audit facts beside it.
- Recompute every supplied digest with the contracts package and compare it through `digest.matches`.
  Never trust a caller-supplied hash or use ordinary string equality.

Do not persist or log raw prompts, raw model completions, provider credentials, authorization headers,
tenant configuration, or unrestricted model metadata. Expected validation and provenance failures become
typed, redacted outcomes. Unexpected failures retain their stack traces in redacted structured logs.

For guaranteed proposal ingress, claim the inbox; read the immutable proposal and source provenance;
persist every evidence item, the decision, and the authoritative audit row; stage both exact application
events; and complete the inbox in one store-owned transaction. Settle only after that context commits.
A rollback leaves the delivery unsettled. Use the store's append-only audit ordinal to order the mission
timeline; a producer sequence orders only that producer's stream.

## 6. Delegate scoring without weakening B31 or B32

Build `Contribution` values only from evidence items whose lifecycle state is `CONTRIBUTING`. The
scoring module does not inspect lifecycle state, so this service must enforce that orchestration
precondition and test it.

- Invoke the pure `decision_band` function rather than summing weights, sorting bands, comparing raw
  scores with thresholds, or applying a service-local corroboration rule.
- Preserve integer weights and the saturating score range defined by the domain. Carry
  `SCORE_VERSION` beside every persisted or published score; never infer a version from the current
  package release.
- Preserve B31: if any contribution has origin `RECORDED`, the domain refuses the entire
  decision-eligible computation and names the source. Do not score it as zero, silently drop it, retry it
  as live, or branch on run mode to make it acceptable.
- Preserve B32: `CORROBORATED` requires at least two distinct source identifiers even under hostile
  boundary values. Do not split one source into aliases or let model text declare independence.
- Distinct sources are not distinct source kinds, and ADR-0076 does not require a non-model contribution:
  two genuinely independent live model observations may corroborate. The current domain cannot prove
  T6's drone, resolved-model, and source-frame independence from a `source_id` string. Until that mapping
  is decided and validated, do not infer independence merely from different labels or drone identifiers.
- Surface a `ScoreError` as a typed, eventually auditable refusal. Do not turn it into `NONE`, catch
  and discard it, or emit a decision from a partial contribution set.

ADR-0146 fixes the simulation-only boundaries at 25, 50, and 75 and the live sensor/model weights at 40
and 35. Keep those values in their canonical operating-parameter owner, never accept them from a model,
and do not represent them as calibrated field confidence.

The score is a versioned demonstration heuristic, not a calibrated probability or confidence estimate.
`CORROBORATED` makes a candidate eligible for rescue escalation, still subject to the separately owned
proposal, approval, and command-gateway controls. It does not approve, dispatch, contact a real rescue
service, or authorize a command. Preserve the full contribution and audit explanation instead of
presenting the saturated number as certainty.

## 7. Preserve the closed wire, store, and broker boundaries

`AGENT_PROPOSAL`, `EVIDENCE_DECISION`, and the typed evidence-decision `AUDIT` record have closed schemas,
topic/envelope bindings, fixtures, canonical digests, and complete Alembic/SQLAlchemy representations. Do
not encode a decision as only an audit event, repurpose another family, hand-build an unknown CloudEvent,
or write around the registered validators and package-store repositories.

`Principal.EVIDENCE_SERVICE` subscribes only to `DRONE_EVENT` and `AGENT_PROPOSAL`, publishes only
`EVIDENCE_DECISION` and `AUDIT`, and has no A2A grant. Resolve permissions through the total domain grant
table and broker projection rather than duplicating or widening the matrix here.

Consume authoritative application topics on the evidence service's own identity. Do not depend on the
Event Mesh Gateway data-plane queue: it is temporary, is absent while disconnected, and cannot be the
authoritative evidence path. `packages/broker` supplies the durable receivers, settlement capability,
and confirmed publisher used by the service's long-running composition. Do not claim live delivery or
recovery from the deterministic port tests alone; retain the shared-stack acceptance obligation.

Validate the CloudEvents envelope against the concrete topic before it affects lifecycle or storage.
Preserve mission, drone, proposal, source, correlation, causation, sequence, time, schema, and trace
semantics through typed values. Trace context links diagnostics; it never establishes provenance,
authority, ordering, or corroboration.

## 8. Make run modes, lifecycle, and resource bounds structural

Live, degraded live, and replay are distinct operating modes; replay additionally has a structurally
isolated process graph:

- In live simulation, a timeout, transport or model failure, declination, invalid JSON, or schema failure
  yields `ABSTAINED`; a typed assertion later refused by deterministic semantic or feasibility
  validation yields `REJECTED`; and only validated policy-referred evidence enters `MANUAL_REVIEW`.
  Recorded evidence is never a substitute for any of them.
- Degraded evidence processing must fail safely without weakening the system-wide telemetry, operator
  visibility, replay, or approval boundary. A broken model or evidence dependency cannot create a
  contributing item or an authorized action.
- The evidence service is absent from replay's live graph: construct no evidence broker consumer or
  publisher, model client, provenance/store writer, approval writer, or escalation executor. Pure
  validation and scoring helpers may be reused without effects, but a `RECORDED` contribution still
  triggers B31's refusal; a mode flag never makes it decision-eligible. The full replay attempts zero
  outbound connections.

Validate all required settings and provenance material before opening a transport or accepting work.
Readiness remains false until the dependencies required by the selected mode are usable. A package import
or inherited Compose healthcheck is not readiness. Do not require a live-only credential or sink in a
replay composition root and then promise not to call it.

Inject broker, store, clock, identifier, configuration, and filesystem boundaries. Bound every model
output, receive queue, transaction wait, timeout, retry, reconnect, concurrency fan-out, drain, and
shutdown deadline with values owned by `docs/operating-parameters.md`. An open bound blocks the behavior
that needs it; it is not permission for an unreviewed local default.

Make cancellation explicit. Startup validates before side effects; shutdown stops intake, allows only a
bounded drain, settles messages after durable outcomes, closes resources, and leaves ambiguous work
recoverable. Preserve idempotency under duplicates and redelivery, reject stale producer sequences within
their own stream, and never use arrival order or producer sequence as the mission timeline.

## 9. Build tests at the boundary that owns the claim

For every behavior, run the existing focused suite, add the smallest AAA test under
`services/evidence_service/tests/`, observe the intended red result, and then add the minimum production
code. Never weaken or alter an existing expected behavior merely to make implementation pass.

Service-level tests should cover the orchestration this member owns:

- missing provenance refused before item creation;
- timeout, transport error, model error, and declination becoming abstentions;
- invalid JSON and schema failures becoming abstentions; typed assertions refused by semantic or
  feasibility validation becoming rejections; and validated referral/admission/dismissal paths;
- only `CONTRIBUTING` items becoming contributions;
- source, origin, model/frame identity, and weight remaining outside model control;
- evidence digest context separation, canonicalization failure, tampering, and recomputation;
- B31 recorded-source refusal and B32 single-source cap under hostile valid boundaries;
- duplicate source identifiers, two truly distinct live model sources, score saturation, invalid
  boundaries, and score-version propagation;
- duplicate and out-of-order input, transaction rollback, commit-before-acknowledgement, redelivery, and
  recovery through the durable contracts;
- broker-port allowed/denied outcomes, topic/envelope mismatch, malformed payloads, and unknown event
  types;
- model, broker, and store loss; bounded output, queues, retries, concurrency, cancellation, and shutdown;
- live and degraded-live construction, plus proof that this service's replay composition seam constructs
  no side-effect port; and
- provenance privacy, prepared-asset policy, and credential/prompt/completion/log redaction.

Keep exhaustive transition, score, refusal-order, property, and mutation evidence in `packages/domain`
and `packages/contracts`. Service tests prove validation, call order, mapping, transaction coordination,
and failure behavior; they do not duplicate pure tables. Cross-component tests belong under `tests/`
when they exercise multiple deployable boundaries.

A fake proves the service's reaction to a controlled port. It does not prove PubSub+ settlement,
PostgreSQL durability, Ollama behavior, TLS, process isolation, recovery, or a delivery guarantee. Pair
offline tests with authorized live positive and negative controls under the root integration, security,
and replay suites before making those claims. Real PubSub+ ACL enforcement and the full replay graph's
zero-outbound-connection proof do not belong in a member-local fake.

This member declares Tier 2. Satisfy the current coverage and test-class requirements in
`docs/TESTING.md` rather than copying mutable thresholds here. Evidence lifecycle and scoring are Tier
1 even when this Tier 2 service calls them; changes to those packages retain their own mutation and
cross-language obligations.

## 10. Workspace hygiene and required verification

- Use the repository-root `.venv`, `pyproject.toml`, and `uv.lock`; do not create a member-local
  environment or lockfile and never install this package globally.
- Run commands from the repository root. The uv workspace discovers `services/*`, so keep local guidance
  inside `services/evidence_service/` rather than placing documentation directly under `services/`.
- Keep `CLAUDE.md` as a relative symlink whose literal target is `AGENTS.md`; do not duplicate this
  text.
- Keep raw prompts, completions, provider responses, unsanitized or ad hoc runtime evidence captures,
  credentials, generated certificates, caches, coverage, and build output untracked. Approved sanitized
  fixtures, prepared assets, and replay artifacts live only under their canonical owners and follow
  `fixtures/AGENTS.md` and the applicable local guide.
- Pass a new untracked guide explicitly to file-based hooks because diff discovery does not see it.

For a guide-only change, run from the repository root:

```sh
uv sync --all-packages --frozen
uv run --frozen pytest -q \
  tools/quality_gate_tests/coverage/test_member_scaffold.py \
  tools/quality_gate_tests/deploy/test_broker_identity_wiring.py
pre-commit run --files services/evidence_service/AGENTS.md services/evidence_service/CLAUDE.md --hook-stage pre-commit
```

For implementation changes, start with the focused member and every affected owner:

```sh
uv run --frozen pytest -q services/evidence_service/tests
uv run --frozen pytest -q packages/domain/tests packages/contracts/tests packages/broker/tests
just check-aaa
```

Add store, contract, integration, security, replay, dashboard, and live-resource suites when the change
reaches those boundaries. Run the complete formatting, Ruff, strict mypy, security, coverage, build, and
production gates required by the root guide and `docs/TESTING.md`.

Before handoff, run:

```sh
just check-types
just check-commit
just check-push
git diff --check
```

Inspect the complete diff, verify the symlink target, and report every check that could not run. An
unactivated deployment, unavailable model, or unproven live dependency is a blocking or unverified
obligation, never evidence that the path passed.
