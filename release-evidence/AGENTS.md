# Release Evidence Instructions

## 1. Scope and authority

These instructions apply to every file under `release-evidence/`. Read the repository-root
[`AGENTS.md`](../AGENTS.md) first. Its safety, testing, security, documentation, and version-control
rules still apply. Although this directory sits outside `docs/`, also follow the canonical-fact and
claim-calibration rules in [`docs/AGENTS.md`](../docs/AGENTS.md).

This directory preserves dated observations from acceptance and feasibility runs. It is not the
canonical home for architecture, contracts, safety rules, parameters, delivery status, runbooks, or
decisions. A record says what one identified run observed under its stated conditions. It does not
make that behavior timeless or broaden it to another environment.

Use the root guide's one-fact/one-home routing before gathering or changing evidence. Every record also
needs the exact criterion from [`docs/IMPLEMENTATION_PLAN.md`](../docs/IMPLEMENTATION_PLAN.md), the
producer's meaning from [`docs/TESTING.md`](../docs/TESTING.md), the claim ceiling from
[`docs/LIMITATIONS.md`](../docs/LIMITATIONS.md), and the supported runbook from
[`CONTRIBUTING.md`](../CONTRIBUTING.md). A record that measures an operating parameter also needs its
instrument from [`docs/operating-parameters.md`](../docs/operating-parameters.md). Then read the
concern-specific architecture, contract, safety, security, and decision sources named by the root guide.

An Accepted architecture decision record (ADR) governs if a record, current code, or another document
disagrees with it. Evidence can reveal that an implementation does not satisfy a decision; it cannot
silently revise the decision. Put a new decision in an ADR and current status in its canonical document.

## 2. Directory purpose and current records

Use one phase directory for the acceptance evidence produced by that delivery phase. Keep phase names
stable and use descriptive kebab-case filenames for new records. The existing `phase-0/` records cover
four distinct boundaries:

| Record | Observed boundary |
| --- | --- |
| `runtime-pinning.md` | Offline native-client and pinned Agent Mesh/plugin compatibility probes |
| `first-live-run.md` | The local default Compose profile, TLS handshakes, and PostgreSQL startup |
| `broker-authorization.md` | Live local broker identity and publish/connect authorization controls |
| `mesh-first-run.md` | Local Agent Mesh discovery plus Ollama-backed invocation and delegation |
| `event-mesh-gateway-first-run.md` | Live Event Mesh Gateway ingress: one salient CloudEvent becoming one structured A2A task |
| `event-mesh-tool-first-run.md` | Live Event Mesh Tool egress: one request becoming one non-actuating command-gateway response |

`phase-2/` and `phase-3/` carry the delivery, simulator, and dashboard records:

| Record | Observed boundary |
| --- | --- |
| `phase-2/guaranteed-delivery-first-run.md` | The first durable queues, their written values, settlement, redelivery, and dead-lettering |
| `phase-2/backlog-recovery-first-run.md` | The first backlog-recovery measurement: 500 spooled commands drained by a paced consumer at the reference fleet size |
| `phase-3/fleet-simulator-first-run.md` | Live direct telemetry and the mission, sector, and connectivity fold |
| `phase-3/command-dispatch-first-run.md` | One command spooled, taken by a running consumer, answered, and settled |
| `phase-3/durable-store-first-run.md` | The first Alembic revision this project applied to a cluster, on a database the run creates and drops, with both declared constraints enforced |
| `phase-3/durable-transaction-first-run.md` | The four-revision history walked up and back down, the server-side bounds and isolation read back from a session, three live races, and ADR-0006's three writes committing and rolling back together |
| `phase-3/wilderness-dashboard-production-first-run.md` | The first clean-source shared-project dashboard rebuild, fixture and production browser acceptance, bounded soak, and focused live store and broker controls |
| `phase-3/application-data-plane-first-run.md` | The first live application data plane at the merged revision: authorization probes, the gateway → evidence → dashboard → command chain, and the ADR-0186 restart with recovery |
| `phase-3/merged-runtime-first-run.md` | The merged runtime's first container composition: spool-gated broker health, the Agent Mesh entrypoint, migrations, and the services that did and did not become healthy |

The record itself owns the dated observation. Current capability claims belong in `README.md`, phase
status and unfinished delivery risks belong in the implementation plan, measured parameters belong in
`operating-parameters.md`, explicitly accepted technical debt belongs in `TECH_DEBT.md`, and security
residual risk belongs in `docs/security/`. Link those consumers to the evidence instead of copying the
evidence into them.

Do not use this directory for:

- replay fixtures, golden contract fixtures, mission recordings, or operational audit events;
- raw test logs, broker exports, environment dumps, raw scan reports, unreviewed screenshots, or
  generated caches;
- design proposals, work summaries, runbooks, or release notes; or
- application inputs of any kind.

Release evidence is inert public documentation. Runtime code must never read it to make a live,
degraded-live, replay, approval, or escalation decision. Recorded results never substitute for live
work ([ADR-0008](../docs/adr/0008-abstention-over-recorded-substitution.md)).

## 3. Calibrate every claim to its producer

Name the evidence class and claim only what that class directly establishes:

| Producer | It can establish | It cannot establish by itself |
| --- | --- | --- |
| Static configuration or policy gate | The inspected files satisfy that gate | A container starts or a provider behaves that way |
| Offline unit, contract, or semantic test | The tested code and inputs produce the asserted result | Live broker, model, network, or Cloud behavior |
| Native compatibility probe without a connection | A pinned library loads and crosses the exercised native boundary | Broker connectivity, TLS, authorization, or delivery |
| Local live probe | The named local components produced the observed result on the recorded host | Solace Cloud parity, another profile, or release-scale behavior |
| Cloud or paid-provider probe | The result came from the named provider, model, and configuration in a stable redacted tenant context | Local-only behavior, another tenant, or general model quality |
| Replay or simulation | The recorded or synthetic input produced a deterministic result | Operationally live sensing or decision-making |

Preserve these review rules:

- State the exact profile, component, protocol path, test cases, and environment exercised. An omitted
  component is unverified, not implicitly covered.
- Separate configured, statically verified, started, healthy, connected, exchanged traffic, and
  behaviorally asserted. A healthcheck proves only the condition it measures.
- A security denial needs a permitted positive control through the same path. A system that refuses
  every request has not proven least privilege.
- A model invocation or tool call is not evidence of reasoning quality, safety, accuracy, or task
  completion. Record each separately only when its own instrument asserts it.
- A local container result is not Solace Cloud evidence. A Cloud showcase result is non-gating unless
  an Accepted ADR changes that status.
- Report the exact test count, exit status, sample count, statistic, and failures that were observed.
  Do not turn a partial run, skip, retry, or selected subset into an all-tests claim.
- Distinguish measurement from inference. Label an inference, state its inputs, and keep it out of the
  list of facts the run directly settles.
- Include explicit "what this does not settle" coverage. Known gaps are part of the result, not prose
  to remove when the rest of the run succeeds.

## 4. Create a reproducible record

Gather live-container, network, Cloud, provider, or paid-model evidence only with explicit human
authorization. Follow the current runbook in `CONTRIBUTING.md`; do not reconstruct an old command from
an evidence record. Evidence capture does not authorize deployment, secret rotation, destructive
cleanup, Cloud mutation, or spend beyond the approved run.

Paid-model runs must also preserve the pre-call cap and spend-ledger boundary in
[ADR-0002](../docs/adr/0002-paid-orchestration-under-enforced-budget-cap.md). Evidence gathering is
never a reason to bypass or retroactively reconstruct that ledger.

Prefer a clean committed revision. Every new independent record must identify:

1. the recording date and, when event order matters, timestamp and time zone;
2. the repository revision and whether the worktree was clean;
3. the relevant host architecture, runtime versions, image or model digests, and bounded resource
   allocation;
4. prerequisites and pre-existing external state that affect the outcome;
5. the exact scope and explicit exclusions;
6. secret-safe commands in execution order, including the exact test selector;
7. observed exit statuses, before/after state, measurements, and failure symptoms;
8. what the run directly proves and what remains unverified;
9. any remediation performed between attempts;
10. the final external state and any authorized cleanup or recovery performed; and
11. a redaction statement naming the sensitive classes removed.

If a run covers uncommitted code, record that limitation and rerun it against the committed revision
before using it to satisfy a release criterion. Never paste an uncommitted diff into an evidence file.
The four existing Phase 0 records predate the revision and cleanliness rule above and omit that
metadata; treat this as a legacy reproducibility limitation. Do not backfill missing metadata in an old
record from memory or inference; add a dated note that says what was not recorded. Cite a primary source
and retrieval date for material external facts.

Use the instrument already named in `docs/operating-parameters.md`. If it is absent or underspecified,
define it there before presenting a number as a measured parameter. Record units, start and end points,
clock source, warm-up state, sample count, and statistic when they affect interpretation. A measurement
may support a parameter decision, but the evidence record is not where the parameter is selected.

Include compact, relevant output or a table of observed values. Preserve enough context for a reviewer
to connect each conclusion to a command or readback, but do not commit an indiscriminate log dump. If a
failure exposed a defect that changed the implementation, retain the failed observation and describe
the remediation and rerun rather than narrating only the final green state.

## 5. Preserve history and correct it visibly

Evidence is historical, so later code changes do not make an old observation false. In particular,
do not rewrite an old count, version, namespace, failure, or exclusion merely to match the current tree.

- Fix spelling, formatting, and links in place only when the edit does not change the observation.
- For a factual transcription or interpretation error, add a dated correction that names the original
  statement, the corrected fact, and the effect on the record's conclusions. Do not silently replace it.
- Add a dated amendment only for a correction, clarification, or additional readback from the same
  execution, revision, and environment.
- Create a new record for an independent rerun, changed environment, changed revision, changed provider,
  or changed acceptance scope. Link the records and state what changed.
- The PostgreSQL section appended to `phase-0/first-live-run.md` is a legacy exception: it records a
  changed-revision rebuild in the original file. Preserve it, but do not repeat that pattern.
- Retain material failures and superseded states when they explain the measured result. Never edit a
  red run into a green one.
- Do not rename, move, or delete a published record without explicit human approval and a complete
  inbound-link review.

If sensitive data is discovered in a record or Git history, stop normal editing. Report the exposure,
identify the affected credential or tenant without repeating its value, and coordinate rotation and
history remediation with a human. Deleting the visible line in a later commit does not remove the
disclosure.

## 6. Redaction and public-data boundary

This is a public repository. Never commit:

- passwords, tokens, provider keys, private keys, authorization headers, or expanded secret values;
- tenant identifiers, tenant URLs, Cloud connection files, private broker addresses, or internal host
  and user names;
- `.env` contents, generated role files, raw configuration exports, or secret-bearing request bodies;
- model prompts, completions, chain-of-thought, raw tool payloads, or secret-bearing provider metadata;
- raw mission telemetry, real-person information, biometric data, or unreviewed imagery; or
- logs and screenshots that have not been reviewed field by field.

Use variable names and ignored file paths in commands, never the values they resolve to. Public image
digests, package versions, loopback endpoints, project-owned role names, topic patterns, and public
certificate fingerprints may be recorded when they are material to the claim. Redact tenant-specific
variants even when a similar local value is public.

Paid-run evidence may include a redacted ledger summary. When a run is used to satisfy the release
criterion in `docs/IMPLEMENTATION_PLAN.md`, commit that reviewed summary as release evidence and include
the fields ADR-0002 requires: timestamp, provider, model, role, tranche, input, cached-input and output
token counts, computed cost, and cache-hit rate. Exclude account identifiers, request identifiers,
prompts, completions, and raw provider payloads. The durable store remains the ledger's canonical
runtime home; an acceptance summary is not a replacement for it.

Prefer a short hand-produced excerpt over a raw export. Secret scanners are a backstop, not proof that
an artifact is safe: they do not recognize every tenant identifier, prompt, personal datum, or newly
issued credential. Review both the rendered Markdown and the underlying diff.

A curated screenshot may be committed when a user-interface acceptance criterion requires visual
evidence. Crop it to the asserted state, redact every sensitive field before it enters Git, preserve
required map attribution, record third-party asset licensing, and confirm that it contains no real or
identifiable person. Keep an editable text description of what the screenshot establishes and omits.

## 7. Coordinate evidence with canonical owners

When a new run changes the support for a current claim, inspect each affected owner:

- update `README.md` only to the capability level the run establishes;
- update the relevant milestone or release criterion in `docs/IMPLEMENTATION_PLAN.md`;
- add measured values and their instruments to `docs/operating-parameters.md`;
- update `docs/LIMITATIONS.md` when the claim ceiling moves;
- add or clear explicitly accepted technical debt in `TECH_DEBT.md` only with its exact clearing
  evidence;
- update the threat model or bypass catalogue when a security case gains or loses evidence;
- update `CONTRIBUTING.md` when the supported runbook or recovery sequence changes; and
- update `CHANGELOG.md` when the evidenced capability changes the public project status.

Do not edit an Accepted ADR's prose to reflect a later measurement. Write a new ADR when the evidence
causes a decision or changes an ADR-governed or safety-gating parameter. Keep the historical record
pointed at the governing decision, and keep each current canonical claim linked to the record that
supports it.

If evidence contradicts a current claim, treat that mismatch as a finding. Narrow the current claim,
record the limitation or risk, and preserve the observation. Never broaden or sanitize the evidence to
make the documents agree.

## 8. Review and verification

The fast Python test selector deliberately excludes `release-evidence/`, so a green documentation
commit does not rerun the producer of an evidence record. For a new or substantively amended evidence
claim, rerun the exact authorized probe named by the record and every affected positive and negative
control. Report any live, Cloud, paid, or platform-specific path that was not exercised. An editorial
or guide-only change requires no live-system mutation.

Run focused checks from the repository root. Pass new untracked files explicitly because diff-based
discovery cannot see them:

```sh
pre-commit run markdownlint-cli2 --files release-evidence/AGENTS.md \
  release-evidence/CLAUDE.md --hook-stage pre-commit
pre-commit run typos --files release-evidence/AGENTS.md \
  release-evidence/CLAUDE.md --hook-stage pre-commit
pre-commit run docs-facts-and-links --files release-evidence/AGENTS.md \
  release-evidence/CLAUDE.md --hook-stage pre-commit
pre-commit run docs-strict --files release-evidence/AGENTS.md \
  release-evidence/CLAUDE.md --hook-stage pre-commit
pre-commit run check-symlinks --files release-evidence/AGENTS.md \
  release-evidence/CLAUDE.md --hook-stage pre-commit
pre-commit run destroyed-symlinks --files release-evidence/AGENTS.md \
  release-evidence/CLAUDE.md --hook-stage pre-commit
pre-commit run detect-private-key --files release-evidence/AGENTS.md \
  release-evidence/CLAUDE.md --hook-stage pre-commit
```

For another Markdown evidence file, replace the two guide paths with every changed record. For an image
or structured artifact, pass its path to the complete file-level commit stage and inspect or validate
the artifact directly; Markdown hooks will skip it. Check external URLs, heading anchors, semantic
drift, command safety, and the correspondence between claims and output manually. Before committing,
the staged diff must pass the private-key and gitleaks checks.

Finish with the repository-wide stages required by the root guide:

```sh
pre-commit run --all-files --hook-stage pre-commit
pre-commit run --all-files --hook-stage pre-push
git diff --check
```

Inspect the complete diff, confirm that `CLAUDE.md` is a relative symlink whose literal target is
`AGENTS.md`, and verify that no raw artifact, credential, tenant value, personal datum, generated cache,
or unrelated change is tracked. A passing hook proves neither that the recorded run occurred nor that
its conclusion is calibrated correctly; both require human review of the evidence chain.
