# Architecture decision records

An ADR records one decision, why it was made, and what it costs. It is written when the decision is made and is never edited afterwards except to change its status — a decision that turns out to be wrong is superseded by a new ADR, not rewritten. The value of the log is that it preserves the reasoning, including the reasoning that later proved mistaken.

## When to write one

Write an ADR when a choice has real alternatives and real consequences: a technology or version pin, a safety or security boundary, a data or contract shape, a change to how the project is built or verified, or the reversal of an earlier decision. Do not write one for a preference with no alternative, or for something the code already states plainly.

An ADR is also required in two specific cases named by the quality rules: any waiver permitting a lint or type-check suppression, and any change to a parameter that gates safety behaviour.

## How to write one

Copy [`0000-template.md`](0000-template.md), take the next free number, and use a short kebab-case slug. Keep it to one decision. State the negative consequences honestly — an ADR listing only benefits is not finished. Give every rejected alternative a reason.

Set the status to `Accepted` when the decision is in force, `Proposed` when it is awaiting a decision, and `Superseded by ADR-NNNN` when a later record replaces it. Update the superseded record's status; do not delete it.

## Index

| ADR | Title | Status |
| --- | --- | --- |
| [0001](0001-self-hosted-open-source-agent-mesh.md) | Self-hosted open-source Agent Mesh over managed Agent Mesh | Accepted |
| [0002](0002-paid-orchestration-under-enforced-budget-cap.md) | Paid orchestration models under an enforced USD $50 cap, with local Ollama at the edge | Accepted |
| [0003](0003-postgres-durable-mission-store.md) | Postgres in Docker as the durable mission store | Accepted |
| [0004](0004-split-python-runtimes.md) | Split Python runtimes for application services and Agent Mesh | Accepted |
| [0005](0005-deterministic-command-gateway.md) | A deterministic command gateway outside model control | Accepted |
| [0006](0006-proposal-bound-single-use-approvals.md) | Approvals bind to a proposal digest, are single-use, and expire | Accepted |
| [0007](0007-solace-first-implementation-policy.md) | Prefer supported Solace components over project-owned infrastructure | Accepted |
| [0008](0008-abstention-over-recorded-substitution.md) | Degraded live simulation abstains rather than substituting recorded evidence | Accepted |
| [0009](0009-isolated-side-effect-free-replay.md) | Replay is structurally isolated and side-effect free | Superseded by ADR-0094 |
| [0010](0010-uv-workspace-and-toolchain.md) | uv workspace with per-member packages | Accepted |
| [0011](0011-no-exception-lint-typecheck-and-complexity-budgets.md) | Lint and typecheck all code with no escape hatches, and enforce complexity budgets | Accepted |
| [0012](0012-git-hooks-with-ci-as-authority.md) | Staged git hooks for fast feedback, with CI as the authority | Accepted |
| [0013](0013-sar-artifact-imagery-policy.md) | The detection target is SAR artifacts, never photographs of real people | Accepted |
| [0014](0014-application-events-separate-from-a2a.md) | Application CloudEvents use a namespace separate from Agent Mesh A2A | Accepted |
| [0015](0015-tiered-quality-gates.md) | Tier quality gates by risk instead of one flat threshold | Accepted |
| [0016](0016-documentation-set-split.md) | Split the planning documents and add a precedence rule | Accepted |
| [0017](0017-mutation-tool-score-and-risk-tiers.md) | Name mutmut and a 90% mutation score, and assign every package to a risk tier | Accepted |
| [0018](0018-enforced-arrange-act-assert.md) | Enforce Arrange-Act-Assert structure in every project-owned executable test | Accepted |
| [0019](0019-fail-closed-quality-gates.md) | Make repository quality gates fail closed and run the same checks in CI | Accepted |
| [0020](0020-pin-uv-version.md) | Pin uv 0.12.5 across local development and CI | Accepted |
| [0021](0021-contract-artifact-manifest.md) | Validate contract artifacts through one offline manifest | Accepted |
| [0022](0022-recursive-diagram-integrity.md) | Verify recursive diagram source and PNG integrity | Accepted |
| [0023](0023-executable-deep-quality-gates.md) | Make complexity, duplication, and mutation gates executable | Accepted |
| [0024](0024-local-operator-api-boundary.md) | Protect local mutations with loopback, Host, Origin, and a per-runtime bearer | Superseded by ADR-0096 |
| [0025](0025-narrow-ruff-subprocess-waivers.md) | Narrow Ruff subprocess waivers and record incompatible rule choices | Accepted |
| [0026](0026-expiring-dependency-waivers.md) | Expiring, reviewed waivers for known upstream advisories | Accepted |
| [0027](0027-integer-only-canonical-serialization.md) | Canonicalize digests over an integer-only JSON profile | Accepted |
| [0028](0028-untyped-solace-client-boundary.md) | Contain the pinned Solace client's static-analysis defects at its boundary | Accepted |
| [0029](0029-verify-the-agent-mesh-domain-with-its-own-toolchain.md) | Verify the Agent Mesh domain with its own toolchain, at its own stage | Accepted |
| [0030](0030-contain-upstream-warnings-in-the-agent-mesh-domain.md) | Contain the pinned Agent Mesh runtime's upstream warnings | Superseded by ADR-0034 |
| [0031](0031-reject-the-google-adk-version-override.md) | Reject the google-adk version override and waive PYSEC-2026-344 | Accepted |
| [0032](0032-agent-mesh-semantic-configuration-validator.md) | Validate Agent Mesh configuration semantically before any is written | Accepted |
| [0033](0033-bound-directory-fan-out.md) | Bound directory fan-out and decompose by concern | Accepted |
| [0034](0034-scope-agent-mesh-warning-filters-to-upstream-modules.md) | Scope Agent Mesh warning filters to upstream modules | Accepted |
| [0035](0035-refuse-unprovable-agent-mesh-configuration.md) | Refuse Agent Mesh configuration the validator cannot yet prove | Accepted |
| [0036](0036-ascii-topic-grammar-bound-to-event-type.md) | Constrain application topics to an ASCII identifier grammar bound to the CloudEvents type | Accepted |
| [0037](0037-cloudevents-envelope-profile.md) | Profile the CloudEvents 1.0 JSON envelope with required sequence and tracing extensions over the integer payload profile | Accepted |
| [0038](0038-reserved-host-schema-identity-and-one-reason-fixtures.md) | Identify schemas by path-derived https URIs under a reserved host, reference them absolutely, and make every negative fixture fail for one reason | Accepted |
| [0039](0039-drone-connectivity-states-and-recovery.md) | Name the drone connectivity states and count transitions in heartbeat intervals | Accepted |
| [0040](0040-consume-approvals-by-recomputed-digest-and-two-clocks.md) | Consume approvals by recomputing the proposal digest and reading two clocks | Accepted |
| [0041](0041-deny-by-default-command-authority-table.md) | Close the command-type set with a deny-by-default command-authority table | Accepted |
| [0042](0042-approval-time-to-live.md) | Approval time to live of 60 seconds | Accepted |
| [0043](0043-docker-broker-with-solace-cloud-showcase.md) | Run the PubSub+ software event broker in Docker as the broker, with Solace Cloud as a non-gating showcase profile | Accepted |
| [0044](0044-docker-compose-runtime-with-official-agent-mesh-image.md) | Run every component except Ollama in Docker Compose, with Agent Mesh from its official image | Superseded by ADR-0096 |
| [0045](0045-fail-closed-compose-policy-gate.md) | Enforce a fail-closed compose policy gate at both blocking stages | Accepted |
| [0046](0046-generated-local-certificate-authority.md) | Secure the local broker with a generated per-checkout certificate authority | Accepted |
| [0047](0047-override-the-asteval-pin-to-close-cve-2026-55244.md) | Override the asteval pin to 1.0.9 and close CVE-2026-55244 | Accepted |
| [0048](0048-scan-images-and-deploy-configuration-with-trivy.md) | Scan every stack image and the deploy configuration with Trivy, blocking on fixed HIGH and CRITICAL findings under the waiver registry | Accepted |
| [0049](0049-audit-workflows-with-zizmor-at-the-commit-stage.md) | Audit the GitHub Actions workflows with zizmor at the commit stage | Accepted |
| [0050](0050-scan-python-with-codeql-in-continuous-integration-only.md) | Scan Python with CodeQL in continuous integration only | Accepted |
| [0051](0051-rescan-daily-and-let-dependabot-raise-pinned-updates.md) | Re-scan daily and let Dependabot raise pinned-update pull requests | Accepted |
| [0052](0052-hold-dependabot-to-a-seven-day-cooldown.md) | Hold Dependabot to the seven-day cooldown the workflow audit requires | Accepted |
| [0053](0053-report-scaffolded-workspace-members-instead-of-failing-them.md) | Report scaffolded workspace members instead of failing them | Accepted |
| [0054](0054-enforce-the-verification-authority-with-branch-protection.md) | Enforce the verification authority with branch protection on `main` | Accepted |
| [0055](0055-block-on-the-image-pin-not-on-advisories-inside-it.md) | Block on the image pin, not on advisories inside a pinned image | Accepted |
| [0056](0056-raise-mypy-to-every-lever-the-tree-satisfies.md) | Raise mypy to every strictness lever both trees already satisfy | Accepted |
| [0057](0057-typescript-strictness-baseline-before-the-dashboard.md) | Fix the dashboard's TypeScript baseline before the first dashboard file, and gate it | Accepted |
| [0058](0058-validate-dashboard-inputs-against-the-committed-schemas.md) | Generate the dashboard's contract types from the committed schemas and validate every untrusted input against them | Accepted |
| [0059](0059-keep-the-verification-authority-able-to-report.md) | Keep the verification authority able to report a verdict | Accepted |
| [0060](0060-postgresql-18-and-its-data-directory-layout.md) | Move the durable store to PostgreSQL 18 and adopt its data-directory layout | Accepted |
| [0061](0061-least-privilege-broker-principals-and-topic-authorization.md) | Give each component a least-privilege broker identity and deny topic access by default | Accepted |
| [0062](0062-type-check-the-agent-mesh-domain-from-its-own-directory.md) | Type-check the Agent Mesh domain from its own directory, over its whole tree | Accepted |
| [0063](0063-lock-local-models-by-manifest-digest.md) | Lock local Ollama models by manifest digest in a committed lock file | Accepted |
| [0064](0064-fix-the-agent-mesh-a2a-namespace.md) | Fix the Agent Mesh A2A namespace at `aerial-rescue-mesh` | Accepted |
| [0065](0065-validate-the-web-ui-gateway-and-keep-the-platform-service-out.md) | Validate the HTTP/SSE Web UI against its declared schema, and keep the Platform service out | Accepted |
| [0066](0066-select-commit-stage-tests-from-an-import-graph.md) | Select commit-stage tests from a project-owned import graph | Accepted |
| [0067](0067-normalized-dashboard-events-and-reduced-state.md) | Project application events into normalized dashboard events and fold them into one reduced state | Superseded by ADR-0101 |
| [0068](0068-command-gateway-request-reply-is-schema-bound-rpc.md) | The command-gateway request/reply channel is schema-bound RPC, recorded as a CloudEvent | Accepted |
| [0069](0069-close-the-gateway-operation-set-with-a-deny-by-default-table.md) | Close the gateway-operation set with a deny-by-default operation table | Accepted |
| [0070](0070-reserve-the-reply-mission-level-and-narrow-the-tool-grant.md) | Reserve `reply` as the reply channel's mission level, and narrow the Event Mesh Tool's grant to it | Accepted |
| [0071](0071-accept-the-event-mesh-gateway-temporary-data-plane-queue.md) | Accept the Event Mesh Gateway's temporary data-plane queue, and scope the no-loss claim to exclude it | Accepted |
| [0072](0072-mission-lifecycle-states.md) | Name the mission lifecycle states and separate an exhausted search from an aborted one | Accepted |
| [0073](0073-sector-lifecycle-states.md) | Name the sector lifecycle states and drive them from the connectivity edges | Accepted |
| [0074](0074-command-dispatch-lifecycle.md) | Name the command dispatch lifecycle and bound it by a send budget, not a clock | Accepted |
| [0075](0075-evidence-lifecycle-states.md) | Name the evidence lifecycle states and keep abstention distinct from rejection | Accepted |
| [0076](0076-evidence-score-bands.md) | Make the escalating evidence band unreachable by construction, not by a threshold | Accepted |
| [0077](0077-fleet-scenario-is-a-frozen-composition-boundary-value.md) | The fleet scenario is a frozen value the composition root supplies, not a file the simulator reads | Accepted |
| [0078](0078-one-tick-is-one-observation-per-drone.md) | One tick is one observation per drone, ordered by drone identifier | Accepted |
| [0079](0079-bind-each-topic-family-to-its-delivery-guarantee.md) | Bind each topic family to its delivery guarantee, and give the gateway RPC families their own value | Accepted |
| [0080](0080-provision-one-durable-queue-per-guaranteed-consumer.md) | Provision one durable queue per guaranteed consumer, owned by its client username | Accepted |
| [0081](0081-give-command-dispatch-one-interval.md) | Give command dispatch one interval, and let jitter only add | Accepted |
| [0082](0082-bind-the-drone-command-and-its-result-to-payload-schemas.md) | Bind the drone command and its result to payload schemas, one schema per command type | Accepted |
| [0083](0083-pace-the-tick-loop-at-a-fixed-rate.md) | Pace the tick loop at a fixed rate, and count what overruns | Accepted |
| [0084](0084-give-backlog-recovery-an-instrument.md) | Give backlog recovery an instrument, and say what it does not measure | Accepted |
| [0085](0085-bound-every-durable-store-wait.md) | Bound every durable-store wait, and derive each from a number the repository already carries | Superseded by ADR-0090 |
| [0086](0086-prove-the-store-on-a-database-the-run-creates-and-drops.md) | Prove the durable store on a database the run creates and drops, and keep its member suite offline | Accepted |
| [0087](0087-put-the-migration-tree-inside-the-member-that-owns-the-schema.md) | Put the migration tree inside the member that owns the schema, and cover its revisions offline | Accepted |
| [0088](0088-order-the-mission-timeline-by-a-per-mission-audit-ordinal.md) | Order the mission timeline by a per-mission audit ordinal advanced inside the writing transaction | Accepted |
| [0089](0089-state-read-committed-rather-than-inherit-it.md) | State `READ COMMITTED` on the engine rather than inherit it from the cluster | Accepted |
| [0090](0090-bound-the-lock-wait-below-the-statement-time.md) | Bound the lock wait below the statement time, so a contended row is distinguishable | Accepted |
| [0091](0091-consume-an-approval-under-its-own-row-lock.md) | Consume an approval under its own row lock, and let the domain's refusal be the denial | Accepted |
| [0092](0092-claim-an-idempotency-key-with-one-conflicting-insert.md) | Claim an idempotency key with one conflicting insert, and let the domain say what a repeat means | Accepted |
| [0093](0093-stage-the-command-outbox-under-a-counted-bound.md) | Give the command outbox three states, a counted bound, and an overflow that writes nothing | Accepted |
| [0094](0094-validate-replay-before-browser-playback.md) | Validate replay in a zero-network one-shot container before browser playback | Accepted |
| [0095](0095-persist-only-the-ui-slice-lifecycle.md) | Persist only the UI slice lifecycle, idempotency, and audit facts | Superseded by ADR-0113 |
| [0096](0096-relay-the-dashboard-over-caddy-and-a-unix-socket.md) | Publish the dashboard through Caddy and keep FastAPI on a private Unix socket | Accepted |
| [0097](0097-close-the-ui-slice-http-contract.md) | Close the UI slice HTTP contract and mutation refusal order | Accepted |
| [0098](0098-make-the-wilderness-dashboard-ui-first.md) | Make the wilderness mission dashboard a UI-first real slice | Accepted |
| [0099](0099-pin-the-dashboard-runtime-and-stack.md) | Pin the dashboard runtime and production stack | Accepted |
| [0100](0100-commit-a-strict-wilderness-scenario-catalog.md) | Commit a strict wilderness scenario catalog with explicit 20 plus 3 participation | Accepted |
| [0101](0101-order-dashboard-events-outside-the-five-field-projection.md) | Order dashboard events outside the five-field projection and resnapshot bounded SSE | Accepted |
| [0102](0102-start-the-agent-mesh-with-the-default-profile.md) | Start the Agent Mesh with the default profile, behind an ordered startup | Accepted |
| [0103](0103-move-the-system-node-runtime-to-26.md) | Move the system Node runtime to 26.7.0 and keep the provisioned hooks on 24 LTS | Accepted |
| [0104](0104-run-every-commit-stage-hook-at-pre-push.md) | Run every commit-stage hook at pre-push as well | Accepted |
| [0105](0105-adjudicate-dashboard-coverage-and-separate-browser-evidence.md) | Adjudicate dashboard coverage and require separate browser evidence | Accepted |
| [0106](0106-bound-dashboard-schema-strings-and-arrays-explicitly.md) | Bound dashboard schema strings and arrays explicitly | Accepted |
| [0107](0107-authenticate-private-scenario-and-fleet-run-control.md) | Authenticate private scenario and fleet run control over bounded HTTP | Accepted |
| [0108](0108-register-strict-python-wire-models-before-http-runtime.md) | Register strict Python wire models before the HTTP runtime | Accepted |
| [0109](0109-enable-the-pydantic-mypy-plugin-with-typed-constructors.md) | Enable the Pydantic mypy plugin with typed constructors | Accepted |
| [0110](0110-scope-the-duplication-gate-to-authored-source.md) | Scope the duplication gate to authored source | Accepted |
| [0111](0111-broker-dashboard-lifecycle-sources.md) | Broker dashboard lifecycle sources as schema-bound application events | Accepted |
| [0112](0112-witness-ordered-dashboard-events-outside-reduced-state.md) | Witness ordered dashboard events outside reduced state and correct v1 anchors | Accepted |
| [0113](0113-persist-dashboard-runtime-after-the-current-store-head.md) | Persist the dashboard runtime after the current store head | Accepted |
| [0114](0114-extend-private-scenario-control-with-catalog-and-recovery.md) | Extend private scenario control with catalog and lost-run recovery | Accepted |
| [0115](0115-record-normalized-events-and-serve-session-neutral-replay.md) | Record normalized events and serve session-neutral replay | Accepted |
| [0116](0116-bound-dashboard-ingress-cursors-and-streams.md) | Bound dashboard ingress, cursors, and streams | Accepted |
| [0117](0117-select-the-exact-mission-control-service-closure.md) | Select the exact mission-control service closure | Accepted |
| [0118](0118-provision-command-queues-only-for-executable-members.md) | Provision command queues only for executable members | Accepted |
| [0119](0119-parameterize-disposable-non-ui-host-ports.md) | Parameterize disposable non-UI host ports | Accepted |
| [0120](0120-run-only-the-recorder-endpoints-the-dashboard-consumes.md) | Run only the recorder endpoints the dashboard consumes | Accepted |
| [0121](0121-reconstruct-synthetic-mission-lifecycle-witnesses.md) | Reconstruct synthetic mission-lifecycle witnesses from stable run identity | Accepted |
| [0122](0122-bound-production-dashboard-script-and-style-bytes.md) | Bound production dashboard script and style bytes | Accepted |
| [0123](0123-isolate-mission-control-state-and-broker-identities.md) | Isolate mission-control state and broker identities | Accepted |
| [0124](0124-remove-unconsumed-dashboard-wire-values.md) | Remove dashboard wire values with no producer-to-consumer effect | Accepted |
| [0125](0125-anchor-browser-runtime-and-bound-transport-outages.md) | Anchor browser runtime identity and bound transport outages | Accepted |
| [0126](0126-instrument-the-dashboard-soak-with-bounded-process-growth.md) | Instrument the dashboard soak with bounded process growth | Accepted |
| [0127](0127-bind-live-runs-to-their-mission-scenario.md) | Bind live runs to their mission scenario | Accepted |
| [0128](0128-prove-sse-overload-through-authenticated-pressure.md) | Prove SSE overload through bounded authenticated pressure | Accepted |
| [0129](0129-generate-only-consumed-local-secrets.md) | Generate only local secrets with active consumers | Accepted |
| [0130](0130-enforce-dashboard-tier-one-coverage-per-file.md) | Enforce dashboard Tier 1 coverage per file | Accepted |
| [0131](0131-isolate-loopback-publishers-and-forward-startup-flags.md) | Isolate loopback publishers and forward startup flags | Accepted |
| [0132](0132-precompile-dashboard-validators-for-the-production-csp.md) | Precompile dashboard validators for the production CSP | Accepted |
| [0133](0133-persist-the-one-shot-replay-handoff.md) | Persist the one-shot replay handoff | Accepted |
| [0134](0134-prove-fleet-publication-separately-from-recorder-receipt.md) | Prove fleet publication separately from recorder receipt | Accepted |
| [0135](0135-keep-overload-recovery-observable-without-delaying-state.md) | Keep overload recovery observable without delaying state | Accepted |
| [0136](0136-bind-live-snapshots-to-accepted-run-identities.md) | Bind live snapshots to accepted run identities | Accepted |
| [0137](0137-remove-unconsumed-recovery-and-recorder-results.md) | Remove unconsumed recovery and recorder results | Accepted |
| [0138](0138-stall-the-publisher-not-the-api-for-sse-pressure.md) | Stall the publisher, not the API, for SSE pressure | Accepted |
| [0139](0139-reuse-the-aerial-rescue-mesh-runtime-for-the-dashboard.md) | Reuse the aerial-rescue-mesh runtime for the dashboard | Accepted |
| [0140](0140-scope-live-telemetry-producers-to-one-mission.md) | Scope live telemetry producers to one mission | Accepted |
| [0141](0141-exhaust-deployed-sse-buffers-with-two-bounded-producers.md) | Exhaust deployed SSE buffers with two bounded producers | Accepted |
| [0142](0142-retain-dashboard-pressure-history-in-the-shared-runtime.md) | Retain dashboard pressure history in the shared runtime | Accepted |
| [0143](0143-let-durable-terminal-state-establish-reset-cancellation.md) | Let durable terminal state establish reset cancellation | Accepted |
| [0144](0144-serialize-the-related-dashboard-test-hook.md) | Serialize the related dashboard test hook | Accepted |

| [0145](0145-bound-solace-recovery-and-queue-retirement.md) | Bound Solace recovery and queue retirement | Superseded in part by ADR-0192 |
| [0146](0146-define-durable-application-processing.md) | Define durable application processing | Accepted |
| [0147](0147-admit-pubsub-integration-to-blocking-ci.md) | Admit PubSub+ integration to blocking continuous integration | Accepted |
| [0148](0148-close-the-application-data-plane-wire-documents.md) | Close the application data-plane wire documents | Accepted |
| [0149](0149-preserve-mission-scoped-gateway-response-records.md) | Preserve mission-scoped gateway response records | Superseded by ADR-0150 |
| [0150](0150-separate-gateway-records-from-private-replies.md) | Separate mission gateway records from private replies | Accepted |
| [0151](0151-require-migrated-sqlalchemy-durable-tables.md) | Require migrated SQLAlchemy durable tables | Accepted |
| [0152](0152-bind-proposals-to-the-complete-source-event.md) | Bind proposals to the complete source event | Accepted |
| [0153](0153-own-bounded-least-privilege-pubsub-clients.md) | Own bounded least-privilege PubSub+ clients | Accepted |
| [0154](0154-isolate-dead-messages-and-monitor-queues-without-enumeration.md) | Isolate dead messages and monitor queues without enumeration | Superseded by ADR-0157 |
| [0155](0155-propagate-solace-trace-context-and-scope-topology-practices.md) | Propagate Solace trace context and scope topology practices | Superseded by ADR-0156 |
| [0156](0156-pin-solace-native-trace-propagation.md) | Pin Solace native trace propagation and bind it by TraceID | Accepted |
| [0157](0157-pace-and-coalesce-read-only-semp-monitoring.md) | Pace and coalesce read-only SEMP monitoring | Superseded in part by ADR-0190 |
| [0158](0158-keep-scenario-control-brokerless.md) | Keep scenario control brokerless | Accepted |
| [0159](0159-gate-applicable-solace-best-practices.md) | Gate every applicable Solace best practice | Accepted |
| [0160](0160-bound-public-dashboard-mutation-bodies.md) | Bound public dashboard mutation bodies before canonical decoding | Accepted |
| [0161](0161-give-the-broker-a-twenty-minute-clean-stop.md) | Give the broker twenty minutes to stop cleanly | Superseded in part by ADR-0194 |
| [0162](0162-generate-and-validate-per-image-cyclonedx-sboms.md) | Generate and validate per-image CycloneDX SBOMs | Accepted |
| [0163](0163-retain-ci-volumes-until-hosted-runner-disposal.md) | Retain integration volumes until hosted-runner disposal | Accepted |
| [0164](0164-require-tls13-for-pubsub-clients.md) | Require TLS 1.3 for PubSub+ clients | Accepted |
| [0165](0165-size-g1-bursts-to-the-complete-flow-set.md) | Size G-1 bursts to the complete Guaranteed flow set | Accepted |
| [0166](0166-disable-unused-pubsub-protocol-services.md) | Disable unused PubSub+ protocol services | Accepted |
| [0167](0167-qualify-production-broker-hosts-separately.md) | Qualify production broker hosts separately | Accepted |
| [0168](0168-bind-application-identities-to-one-connection.md) | Bind application identities to one long-lived connection | Accepted |
| [0169](0169-request-immediate-acks-for-confirmed-publications.md) | Request immediate ACKs for individually confirmed publications | Accepted |
| [0170](0170-force-dmq-eligibility-at-the-publisher.md) | Force DMQ eligibility at the Guaranteed publisher | Accepted |
| [0171](0171-close-dashboard-idempotency-kinds.md) | Close durable idempotency over every public dashboard mutation | Superseded in part by ADR-0189 |
| [0172](0172-complete-the-protected-dashboard-operator-flow.md) | Complete the protected dashboard operator flow | Accepted |
| [0173](0173-follow-the-retained-broker-event-log-without-runtime-authority.md) | Follow the retained broker event log without runtime authority | Superseded in part by ADR-0195 |
| [0174](0174-recompute-evidence-digests-at-the-dashboard-boundary.md) | Recompute evidence digests at the dashboard boundary | Accepted |
| [0175](0175-project-every-recorded-event-into-the-dashboard.md) | Project every recorded event into the ordered dashboard stream | Accepted |
| [0176](0176-bound-dashboard-sse-clients-and-keepalives.md) | Bound dashboard SSE clients and keepalives | Accepted |
| [0177](0177-harden-the-pinned-agent-mesh-broker-runtime.md) | Harden the pinned Agent Mesh broker runtime | Accepted |
| [0178](0178-qualify-production-agent-mesh-separately.md) | Qualify production Agent Mesh separately | Accepted |
| [0179](0179-make-the-official-agent-mesh-checklist-the-production-gate.md) | Make the official Agent Mesh checklist the production gate | Accepted |
| [0180](0180-persist-direct-ingress-refusals-without-stopping-consumers.md) | Persist Direct ingress refusals without stopping consumers | Accepted |
| [0181](0181-gate-continuous-semp-monitoring-on-vpn-scoped-operator-provisioning.md) | Gate continuous SEMP monitoring on VPN-scoped operator provisioning | Accepted |
| [0182](0182-bind-agent-responses-to-transport-authenticated-context.md) | Bind Agent Responses to transport-authenticated context | Accepted |
| [0183](0183-bind-approval-authority-to-the-command-gateway-clock.md) | Bind approval authority to the command-gateway clock | Accepted |
| [0184](0184-package-the-dashboard-and-gate-relay-startup-on-readiness.md) | Package the dashboard and gate relay startup on application readiness | Accepted |
| [0185](0185-pause-active-fleet-runs-during-broker-recovery.md) | Pause active Fleet runs during broker recovery | Accepted |
| [0186](0186-delegate-one-broker-restart-without-project-authority.md) | Delegate one broker restart without project authority | Accepted |
| [0187](0187-map-mypy-modules-from-explicit-project-bases.md) | Map mypy modules from explicit project bases | Accepted |
| [0188](0188-route-root-mypy-through-discovered-source-bases.md) | Route root mypy through discovered source bases | Accepted |
| [0189](0189-reconcile-dashboard-runtime-with-the-solace-data-plane.md) | Reconcile the accepted dashboard runtime with the Solace application data plane | Accepted |
| [0190](0190-count-active-queue-binds-through-transmit-flow-aggregates.md) | Count active queue binds through transmit-flow aggregates | Accepted |
| [0191](0191-reserve-one-subscription-for-the-sdk-reply-inbox.md) | Reserve one subscription per session for the SDK reply inbox | Accepted |
| [0192](0192-cover-a-reference-host-broker-restart-with-the-reconnection-budget.md) | Cover a reference-host broker restart with the reconnection budget | Accepted |
| [0193](0193-size-the-audit-kind-for-event-types.md) | Size the audit kind column for event types | Accepted |
| [0194](0194-gate-broker-health-on-guaranteed-messaging.md) | Gate broker health on guaranteed messaging | Accepted |
| [0195](0195-run-the-event-monitor-as-the-retained-log-s-owner.md) | Run the event monitor as the retained log's owner | Accepted |
| [0196](0196-count-the-coordinator-s-reply-queue-in-the-agent-mesh-endpoint-ceiling.md) | Count the coordinator's reply queue in the Agent Mesh endpoint ceiling | Accepted |
| [0197](0197-standardize-scenario-control-on-the-console-composition.md) | Standardize scenario control on the console composition | Accepted |
| [0198](0198-give-the-coordinator-a-model-and-a-tool-surface-that-answer.md) | Give the coordinator a model and a tool surface that answer the structured request | Accepted |
| [0199](0199-terminate-the-owned-agent-mesh-entrypoint.md) | Terminate the owned Agent Mesh entrypoint | Accepted |
| [0200](0200-give-the-coordinator-a-tool-capable-model.md) | Give the coordinator a tool-capable model | Accepted |
| [0201](0201-gate-agent-mesh-readiness-on-asynchronous-initialization.md) | Gate Agent Mesh readiness on asynchronous initialization | Accepted |
| [0202](0202-accept-the-cyclonedx-version-the-pinned-trivy-emits.md) | Accept the CycloneDX version the pinned Trivy emits | Accepted |
| [0203](0203-make-the-broker-s-mounted-secrets-readable-by-the-pinned-image-s-user.md) | Make the broker's mounted secrets readable by the pinned image's user | Accepted |
| [0204](0204-resolve-embedded-production-probe-references-at-commit-time.md) | Resolve the dashboard's embedded production probes against workspace source | Accepted |
| [0205](0205-project-the-committed-envelope-at-the-dashboard-store-adapter.md) | Project the recorder's committed envelope at the dashboard store adapter | Accepted |
| [0206](0206-link-the-broker-provenance-the-dashboard-watermark-reads.md) | Link the broker provenance the dashboard watermark reads | Accepted |
| [0207](0207-drain-the-recorder-fan-in-without-a-wait-per-channel.md) | Drain the recorder fan-in without a wait per channel | Accepted |
| [0208](0208-publish-the-dashboard-outbox-on-the-serving-cycle.md) | Publish the dashboard application outbox on the serving cycle | Accepted |
| [0209](0209-publish-the-mission-lifecycle-from-observed-run-status.md) | Publish the mission lifecycle from observed private run status | Accepted |
| [0210](0210-publish-the-ending-a-reset-gives-its-predecessor.md) | Publish the ending a reset gives its predecessor | Accepted |

## Decisions still open

These are known unresolved questions, recorded here so they are tracked rather than assumed. Each needs an ADR once settled.

| Question | Why it matters | Settled by |
| --- | --- | --- |
| How large is the capability gap between a local Ollama model and a paid model on Agent Mesh orchestration — discovery, delegation, multi-step tool calling, schema-constrained output? | No longer a kill criterion: [ADR-0002](0002-paid-orchestration-under-enforced-budget-cap.md) permits paid orchestration, so this is now a measured comparison that sets how usable the local-only configuration is. | Phase 0 evaluation |
| ~~Is Agent Mesh 1.28.7 compatible with the independently released `sam-event-mesh-gateway` 1.1.0 and `sam-event-mesh-tool` 0.1.1?~~ **Settled 2026-08-19: yes.** The three resolve into one 251-package lock for both supported platforms, the gateway's entry point loads against the runtime, the tool imports by module path, and every runtime symbol each plugin depends on is present and callable. `agent-mesh/tests/test_pinned_plugin_compatibility.py` is the executable evidence. Upstream warning findings are in [ADR-0030](0030-contain-upstream-warnings-in-the-agent-mesh-domain.md). | No single upstream artifact attests the combination. | Phase 0 gate |
| ~~Can a trial/standard Solace Cloud service carry A2A traffic, application events, durable queues, and per-component ACL identities together?~~ **Settled 2026-08-20: the question no longer gates anything.** [ADR-0043](0043-docker-broker-with-solace-cloud-showcase.md) makes the PubSub+ software broker container the broker for every gated path; the Developer-class Cloud service is a non-gating showcase profile whose connection budget Phase 0 measures. | Determines whether the shared-broker design holds. | Phase 0 gate |
| Does the whole stack fit on one workstation — Agent Mesh, one Ollama daemon serving five model roles, a broker container, Postgres, the API, and a browser? | Sets whether the SLO targets are reachable at all. | Phase 0 resource measurement |
| Which provider and model serve the `general` and `planning` roles, on measured capability-per-dollar? | Anthropic and OpenAI are both permitted; the choice is deferred to measurement rather than preference. | Phase 0 evaluation |
| Are the indicative OpenAI rates in [ADR-0002](0002-paid-orchestration-under-enforced-budget-cap.md) correct against first-party pricing? | They were taken from secondary aggregators. The committed price data drives cap enforcement, so a wrong rate means a wrong cap. | Phase 0 gate |
| What replaces `llama3:8b`? It is the April 2024 original with legacy quantization and an 8K context, which caps evidence fusion. | Affects the summarisation role and total resident memory. | Phase 0 model selection |
| ~~Does `solace-pubsubplus` 1.11 actually function on Python 3.14.7, not merely install?~~ **Settled 2026-08-19: yes.** The native library loads, session creation marshals its callback structures, and the API version, application identifier, and a message payload read back, all without a broker. `tests/phase0/test_solace_messaging_runtime.py` is the executable evidence. Two upstream hygiene defects surfaced and are contained by [ADR-0028](0028-untyped-solace-client-boundary.md). | Its wheels are tagged `py36-none-<platform>`, so pip and uv will install it on 3.14 without complaint — which means a runtime incompatibility would surface silently, after Phase 1 has frozen both lockfiles. The whole split-runtime decision in [ADR-0004](0004-split-python-runtimes.md) rests on it. | Phase 0 gate |
| ~~What is the version-controlled lock representation for a local Ollama model?~~ **Settled 2026-08-21: the Ollama manifest digest, in `agent-mesh/model-lock.toml`, compared for membership offline and for equality at readiness.** Measured against the running daemon, Ollama refuses both `name@sha256:<hex>` and `name:sha256-<hex>`, so the digest cannot live in the identifier and the offline half can only prove that an identifier is listed. [ADR-0063](0063-lock-local-models-by-manifest-digest.md) records the form, the home, and both comparisons; the readiness half is owed and carried in [TECH_DEBT.md](../../TECH_DEBT.md). | Until it existed every local-model configuration failed `MODEL_LOCK_REQUIRED` ([ADR-0035](0035-refuse-unprovable-agent-mesh-configuration.md)), so no local-only Agent Mesh configuration could be committed. | [ADR-0063](0063-lock-local-models-by-manifest-digest.md) |
| ~~What is the post-trial broker substrate once the Solace Cloud trial expires?~~ **Settled 2026-08-20: the PubSub+ software event broker container, pinned by digest.** [ADR-0043](0043-docker-broker-with-solace-cloud-showcase.md) makes it the broker for development, integration, continuous integration, acceptance, and release; the trial's expiry ends only the showcase. | Phase 5, Phase 8 and the release criteria all require Solace Cloud, while the local container is scoped to integration tests only, so an expiry leaves the release criteria with no exit. | ADR once decided |
| Does the fleet's identity count fit the Developer-class service's limit of 100 connections? | The showcase profile in [ADR-0043](0043-docker-broker-with-solace-cloud-showcase.md) runs the full fleet against that service; 23 drones plus the services, gateways, and agents are expected to need about 40 identities, and a measurement above the limit means the showcase must trim identities or the service class must change. | Phase 0 measurement |
| ~~Waiver or version override for `google-adk` 1.18.0 / CVE-2026-4810?~~ **Settled 2026-08-19: waiver.** The override was attempted and is unsatisfiable, and the advisory is reported as `PYSEC-2026-344` rather than under its CVE alias. Recorded in [ADR-0031](0031-reject-the-google-adk-version-override.md) with uv's verbatim conflict output; the accepted risk is carried in [TECH_DEBT.md](../../TECH_DEBT.md). | Agent Mesh 1.28.7 pins `google-adk==1.18.0` exactly; the advisory is unauthenticated remote code execution, fixed upstream in 1.28.1. A `[tool.uv] override-dependencies` bump must be tried against the black-box compatibility suite before a waiver is accepted. | Phase 0 gate |
| Where does a service's healthcheck live — the compose file, which the compose policy gate requires, or the Dockerfile, which Trivy's `DS-0026` check expects? | `trivy config` reports `DS-0026` at LOW on both Dockerfiles on every pre-push run; it is informational today, but two gates disagree about the same fact. | ADR once the first live run shows which form the broker and Agent Mesh honour |
| Does Dependabot's bundled uv regenerate `uv.lock` under the manifests' `required-version`, and does it leave `override-dependencies` alone? | If it cannot, every uv pull request arrives with a stale lock and `lockfiles-current` turns it red; the asteval override must be removed by hand regardless ([ADR-0047](0047-override-the-asteval-pin-to-close-cve-2026-55244.md)). | The first Dependabot uv pull request |
| ~~What is the approval time to live?~~ **Settled 2026-08-21: 60 seconds.** [ADR-0042](0042-approval-time-to-live.md) is accepted and the operating-parameters row carries the number. `packages/domain` still injects the value with no default, so the composition root supplies it. | [ADR-0006](0006-proposal-bound-single-use-approvals.md) requires the window chosen and justified; until it was, the parameter row stayed open. | [ADR-0042](0042-approval-time-to-live.md) |
