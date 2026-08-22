# Documentation Instructions

## 1. Scope and authority

These instructions apply to every file under `docs/`. Read the repository-root
[`AGENTS.md`](../AGENTS.md) first. Its safety, testing, security, and version-control rules still apply.
This file governs how documentation is maintained; it is not another home for architecture facts,
contracts, parameters, or decisions.

Accepted architecture decision records (ADRs) are authoritative for why a decision was made and for
its current status. If an Accepted ADR conflicts with another document, the ADR governs and the other
document is defective. If documents conflict without an Accepted ADR resolving them, preserve the
stricter statement and record the decision before weakening it.

## 2. Put each fact in its canonical document

Update the one canonical owner and link to it from every consumer. Do not copy a fact into several
documents merely to keep them aligned.

| Fact or concern | Canonical owner |
| --- | --- |
| Decision rationale, alternatives, consequences, and status | [`adr/`](adr/README.md) |
| Component responsibilities, runtime layout, and operating modes | [`ARCHITECTURE.md`](ARCHITECTURE.md) |
| Event envelope, topics, HTTP API, and delivery semantics | [`CONTRACTS.md`](CONTRACTS.md) |
| Delivery sequence, milestones, risks, and release criteria | [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md) |
| Safety invariants, approval protocol, and privacy boundary | [`SAFETY.md`](SAFETY.md) |
| Test classes, risk tiers, coverage gates, stages, and toolchain | [`TESTING.md`](TESTING.md) |
| Numeric parameters and the instrument or evidence that measures each one | [`operating-parameters.md`](operating-parameters.md) |
| Capability and evidence claim ceiling | [`LIMITATIONS.md`](LIMITATIONS.md) |
| Threats, trust boundaries, and enumerated approval bypasses | [`security/`](security/threat-model.md) |

When a change crosses owners, update each affected owner in the same change, but keep the shared fact in
only one place. In particular, inspect these relationships:

- contract changes against schemas, the contract manifest, fixtures, validators, and Python and
  TypeScript consumers;
- runtime changes against deployment configuration, runbooks, architecture diagrams, and live evidence;
- safety changes against the threat model, bypass catalogue, approval semantics, and negative tests;
- test or tooling changes against scripts, hook registration, continuous integration, the `justfile`,
  and contributor instructions;
- release or implementation-status changes against committed release evidence and `CHANGELOG.md`.

Do not turn a planning statement into a current capability claim. Distinguish design intent, committed
configuration, static verification, deterministic replay, and measurement from a running system. Label
live, degraded-live, offline, simulated, and replay behavior truthfully. `LIMITATIONS.md` bounds every
claim even when another document describes the intended end state.

Every operational or gating parameter and service-level target belongs in `operating-parameters.md`
together with its unit, source, measurement method, and status. Reference the parameter instead of
repeating its current value elsewhere. An ADR still records the value or version selected by the
decision and its rationale, and numeric exit criteria remain with the plan they govern. If an operational
parameter is not yet established, use the repository's exact provisional marker:
`(provisional -- confirm in Phase 0)`.

## 3. Architecture decision records

Follow [`adr/README.md`](adr/README.md) and [`adr/0000-template.md`](adr/0000-template.md):

1. Confirm that the change requires an ADR and search the index for an existing decision.
2. Copy the template, use the next free four-digit number and a kebab-case slug, and record one decision.
3. Describe the context, decision, negative consequences, and rejected alternatives. Cite external facts
   close to the claim they support.
4. Add the record to the ADR index, keep its index row synchronized on every status change, and update
   the open-decisions ledger when its state changes.
5. Keep implementation work dependent on a Proposed ADR out of claims that imply the decision is
   Accepted.

Once an ADR is Accepted, do not edit, rename, or move its prose. A reversal or material refinement needs
a new ADR; update the old ADR's status only to point to the superseding record. A path fixed by an
Accepted ADR is also part of the decision and must not move without a superseding ADR. Resolve any
status ambiguity from `adr/README.md`; do not invent a lifecycle from the template alone.

The automated ADR-index check proves only that each numbered file is listed. Review the title, status,
number sequence, duplicate numbers, and open-decisions ledger manually. No gate proves that Accepted
ADR prose remained unchanged, so inspect its Git history whenever an Accepted record appears in a diff.

## 4. Architecture diagrams

Editable diagram sources live recursively under `architecture/`. Every `.dot` source must have the
matching generated `.png` and `.dot.sha256` sidecar required by
[ADR-0022](adr/0022-recursive-diagram-integrity.md).

- Edit the DOT source, then run `just diagrams` or `scripts/diagrams.sh` from the repository root.
- Commit the source, PNG, and sidecar as one artifact triplet. Never hand-edit the PNG or either digest.
- The generator walks the entire source tree. Inspect the complete diff for rendering changes outside
  the diagram you intended to update.
- Open every changed PNG and inspect legibility, clipping, arrow direction, labels, color contrast, and
  correspondence with the current architecture and safety boundaries.
- A matching signature and digest prove artifact integrity, not semantic accuracy or visual quality.

Add both editable source and generated PNG for every new architecture or workflow diagram. Keep the
diagram near the prose it supports, and keep citations and third-party asset attribution near the
relevant content.

## 5. Writing and runbook standards

- Use relative links resolved from the containing document for tracked files and descriptive links for
  external sources. Check external URLs and heading anchors manually because the local link gate does
  not validate them.
- State prerequisites, exact commands, expected success evidence, failure symptoms, and recovery steps
  in operational instructions. Keep check-only validation distinct from commands that mutate runtime or
  external state.
- Keep examples secret-safe and tenant-neutral. Never include credentials, authorization headers,
  prompts, private tenant values, raw configuration exports, or screenshots containing them.
- Treat model output, event payloads, scan reports, and generated evidence as untrusted inputs. Describe
  validation and abstention behavior without claiming that the input itself is authoritative.
- Verify safety claims against `SAFETY.md` and the governing Accepted ADRs. Reference those sources
  instead of creating a second formulation of an invariant.
- Prefer precise present-tense statements backed by committed or live evidence. Put intended future
  behavior in the implementation plan and unresolved limitations in `LIMITATIONS.md`.
- Update existing canonical prose instead of adding an ad hoc work-summary document.

The facts-and-links gate checks local file targets and selected fact rules. It does not validate external
URLs, bare heading anchors, semantic drift, visual diagrams, or whether two documents paraphrase a fact
differently. Perform those reviews explicitly.

## 6. Required verification

Run focused documentation checks from the repository root:

```sh
pre-commit run markdownlint-cli2 --all-files --hook-stage pre-commit
pre-commit run typos --all-files --hook-stage pre-commit
pre-commit run docs-facts-and-links --all-files --hook-stage pre-commit
pre-commit run docs-strict --all-files --hook-stage pre-commit
scripts/hooks/docs/check-diagrams-all.sh
uv run --frozen pytest -q tools/quality_gate_tests/test_diagram_integrity.py
```

For a new untracked document, pass its path explicitly to the relevant pre-commit hooks before staging;
diff-based discovery cannot see it. For a diagram change, regenerate the complete artifact set, inspect
every changed PNG, and run:

```sh
pre-commit run diagrams-fresh-all --all-files --hook-stage pre-push
```

Finish every documentation change with the repository-wide stages required by the root instructions:

```sh
pre-commit run --all-files --hook-stage pre-commit
pre-commit run --all-files --hook-stage pre-push
git diff --check
```

Review the complete diff after the gates. A passing syntax, link, hash, or phrase check does not prove
that the document is true, that an ADR was followed, or that a diagram is readable.
