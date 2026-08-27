# Contract Package Instructions

## 1. Scope and authority

These instructions apply to every file under `packages/contracts/`. Read the repository-root
[`AGENTS.md`](../../AGENTS.md) first. Its safety, TDD, documentation, security, and version-control
rules still apply. This package is the dependency-free Python implementation of the repository's wire
contracts. Small changes here can alter persisted identity, broker interoperability, approval binding,
and cross-language behavior, so compatibility is a design concern rather than a cleanup detail.

Read the authority for the concern before editing it:

| Concern | Authority or reference |
| --- | --- |
| Event envelope, topics, canonical bytes, schema identity, dashboard projection, and delivery semantics | [`docs/CONTRACTS.md`](../../docs/CONTRACTS.md) |
| Test classes, Tier 1 requirements, AAA, coverage, property testing, and mutation | [`docs/TESTING.md`](../../docs/TESTING.md) |
| Application-event namespace separation and versioning | [ADR-0014](../../docs/adr/0014-application-events-separate-from-a2a.md) |
| Integer-only canonical serialization | [ADR-0027](../../docs/adr/0027-integer-only-canonical-serialization.md) |
| Topic grammar and event-type binding | [ADR-0036](../../docs/adr/0036-ascii-topic-grammar-bound-to-event-type.md) |
| CloudEvents envelope profile and refusal order | [ADR-0037](../../docs/adr/0037-cloudevents-envelope-profile.md) |
| Schema identity and one-reason-negative fixtures | [ADR-0038](../../docs/adr/0038-reserved-host-schema-identity-and-one-reason-fixtures.md) |
| Contract artifact manifest | [ADR-0021](../../docs/adr/0021-contract-artifact-manifest.md) |
| Dashboard event projection and reduced state | [ADR-0067](../../docs/adr/0067-normalized-dashboard-events-and-reduced-state.md) |
| Dashboard generated types and independent runtime validation | [ADR-0058](../../docs/adr/0058-validate-dashboard-inputs-against-the-committed-schemas.md) |
| Schema-bound dashboard lifecycle sources, families, and projections | [ADR-0111](../../docs/adr/0111-broker-dashboard-lifecycle-sources.md) |
| Durable application processing and application family representation | [ADR-0146](../../docs/adr/0146-define-durable-application-processing.md), [ADR-0150](../../docs/adr/0150-separate-gateway-records-from-private-replies.md) |
| Closed application documents and timeline projections | [ADR-0148](../../docs/adr/0148-close-the-application-data-plane-wire-documents.md) |
| Reserved-topic RPC versus mission-scoped Gateway Record | [ADR-0150](../../docs/adr/0150-separate-gateway-records-from-private-replies.md) |
| Approval identity and consumption | [ADR-0006](../../docs/adr/0006-proposal-bound-single-use-approvals.md), [ADR-0040](../../docs/adr/0040-consume-approvals-by-recomputed-digest-and-two-clocks.md) |

An Accepted ADR governs if code, tests, fixtures, or prose disagree. A change to a wire shape,
canonicalization rule, refusal order, topic binding, schema identity, approval digest, or projection
contract requires the coordinated decision and documentation work described by the root instructions.

## 2. Package boundary and ownership

Keep this package pure, deterministic, typed, and free of runtime framework dependencies. Contract code
validates and represents data; it does not perform I/O, read clocks or randomness, publish messages,
authorize commands, or decide mission policy. Service and broker adapters own transport and Pydantic
trust boundaries. Domain code owns authorization, mission-state transitions, and safety policy. This
package owns the cross-language dashboard-event projection and pure reduced-dashboard-state contract;
that fold derives deterministic state but never authorizes or mutates a running mission.

| Path | Responsibility |
| --- | --- |
| `canonical.py` | Encode and decode the repository's exact canonical JSON profile |
| `digest.py` | Build and verify versioned, domain-separated contract identities |
| `instant.py` | Validate and convert the exact UTC millisecond instant profile |
| `topics.py` | Validate the fifteen concrete topic families, derive event types, and expose the total family delivery baseline ([ADR-0079](../../docs/adr/0079-bind-each-topic-family-to-its-delivery-guarantee.md), [ADR-0150](../../docs/adr/0150-separate-gateway-records-from-private-replies.md)) |
| `envelope.py` | Parse the envelope profile in the documented refusal order and enforce bindings |
| `integration.py` | Decode the closed direct Agent Response body and bind its topic mission and agent identities without conferring CloudEvents semantics |
| `view.py` | Project accepted application events and own the pure reduced-dashboard-state contract |
| `__init__.py` | Deliberate public Python API; exports here are compatibility surface |
| `py.typed` | Marker that makes the distributed type information part of the package |
| `tests/` | Member-local unit, refusal, property, namespace, and projection tests |

Do not put Pydantic models, broker subscriptions, storage models, mutable dashboard presentation state,
or service configuration in this package. The normalized state document, pure reducer, and replay-state
digest do belong here under ADR-0067. Do not create a second canonicalizer or topic parser in a Python
consumer. ADR-0027 and ADR-0058 deliberately require independent TypeScript implementations in
`apps/dashboard/src/contracts/`; hold them to the same schemas, fixtures, refusal order, and reduced-state
digest. Other consumers needing a narrower domain type should validate with this package first and then
adapt the accepted value explicitly.

The current vocabulary has fourteen unique topic families. Eleven are notification-only,
`GATEWAY_REQUEST` and `GATEWAY_RESPONSE` carry request/reply RPC, `GATEWAY_RECORD` carries the direct
mission CloudEvent, and `AGENT_RESPONSE` carries the direct non-CloudEvent integration body. The families
are wire- and delivery-disjoint under ADR-0150. A delivery router derives the capability from the parsed
family; a caller-supplied delivery enum is not contract authority.

## 3. Exactness and refusal discipline

Contract validators fail closed. Preserve the documented validation sequence so an input that violates
several rules receives the same first refusal in every implementation. Refusal codes, structured
offending values, exception types, and ordering are compatibility surfaces. Human-readable explanation
text is diagnostic unless the contract explicitly says otherwise; tests should prefer structured
reasons over brittle prose matching.

Never silently normalize an invalid wire value to make it acceptable:

- Canonical JSON remains integer-only and rejects unsupported numbers and ambiguous input. Preserve the
  exact UTF-8, Unicode normalization, object-key, duplicate-key, ordering, and scalar rules in the ADR.
- Digests remain domain-separated and versioned inside the bytes being hashed. `digest()` excludes a
  generic digest-covered object's top-level `digest` member from itself; the closed CloudEvents envelope
  has no such member. Preserve that distinction and use constant-time verification for supplied
  identities.
- Instants require the exact UTC millisecond spelling and a real calendar value. Do not accept another
  offset, add omitted precision, round a value into conformance, or let platform date parsing define the
  contract.
- Topics are concrete ASCII publish topics bound to the envelope event type. Wildcard subscriptions are
  broker-adapter concerns; do not accept wildcards or repair case, separators, or segments here.
- Envelopes bind the topic, event type, subject, schema identifier, and payload as documented. Parse
  bytes without losing duplicate-key evidence or coercing numbers before validation.
- Dashboard projection derives presentation input from an already accepted application event. Strip
  transport-only members, then fold events through the pure total reduced-state function without wall
  clocks, trace context, or independent mission policy in `view.py`.
- The ADR-0148 projections add timeline-only `operatorCommand`, `operatorApproval`, `agentProposal`,
  `evidenceDecision`, `droneCommand`, and `auditRecord` variants. All are non-droppable. Projection
  removes `missionId` from every payload and additionally removes the self-integrity
  `evidenceDecisionDigest` from an evidence decision. A direct Agent Response is never projected as an
  ordered event.

Keep validation functions explicit and small. Do not use a generic JSON helper whose permissive defaults
change number handling, duplicate-key behavior, Unicode treatment, or error precedence. A performance
optimization must prove byte-for-byte and refusal-for-refusal equivalence before it replaces readable
code.

## 4. Coordinate contract artifacts atomically

A contract change is rarely local to this directory. Inspect and update every affected owner in the
same change:

- the Python implementation and public exports in this package;
- member-local unit, refusal, and property tests;
- [`docs/CONTRACTS.md`](../../docs/CONTRACTS.md), the governing ADR, and a new or superseding record when
  the decision changes; never rewrite Accepted ADR prose;
- the matching files under [`schemas/`](../../schemas/), including the schema identifier and
  [`contract-manifest.toml`](../../schemas/contract-manifest.toml);
- at least one accepted fixture and the required one-reason-negative fixtures under
  [`fixtures/golden/`](../../fixtures/golden/);
- the root schema-identity and golden-fixture oracles under [`tests/contract/`](../../tests/contract/);
- every domain, broker, service, replay, recorder, or dashboard consumer reached by the change;
- generated TypeScript contract types and their freshness gate when the dashboard package exists;
- the dashboard's runtime validation against committed schemas, refusal-order parity, projection parity,
  reducer parity, and replay-state digest parity; and
- every other cross-language consumer reached by the change.

Each negative golden fixture demonstrates one reason for refusal. Do not reuse an invalid fixture that
also violates another earlier rule, and do not weaken an oracle to accommodate drift. Preserve the exact
identity mapping: a manifest schema path corresponds to that schema's reserved-host `$id`; an envelope
`dataschema` and Python `BINDINGS` row identify the payload schema; and the composed event schema has its
own event-schema `$id` while binding and referencing that payload schema. Register each fixture under
its one owning schema entry.

Adding an application event also requires the projection decision, normalized state effect, fixtures,
and manifest work required by ADR-0067. Do not leave a recognized application event implicitly ignored
or claim a dashboard consumer exists before it is present in the tree.

The manifest now owns 66 schemas, including 23 dashboard schemas. Twelve application payload/event
documents, one standalone Agent Response integration document, and four dashboard HTTP documents are
the ADR-0148 increment. The dashboard API has 21 server-facing schema twins and two browser-only
documents; those service-local Pydantic types do not belong here. These contract artifacts and pure
oracles do not implement runtime JSON-Schema execution at broker ingress, broker I/O, FastAPI handlers,
PostgreSQL transactions, or a continuously running data plane.

Approval-related contract changes require extra care. Proposal identity must be recomputed from accepted
canonical content at consumption time; never trust a caller-supplied digest as authorization and never
move single-use, expiry, or replay policy into this representation package.

## 5. Tests and compatibility evidence

The member's `pyproject.toml` declares its current risk tier. Satisfy the corresponding requirements in
[`docs/TESTING.md`](../../docs/TESTING.md) rather than copying mutable thresholds here. For authorized
behavior changes, follow red-green-refactor and the repository's mandatory Arrange-Act-Assert structure.
Never modify an established expectation merely to make new implementation code pass.

Tests should cover accepted boundaries and explicit refusals, including:

- deterministic repeated encoding and cross-process identity;
- canonical encode/decode round trips and malformed byte input;
- Unicode, duplicate keys, unsupported numeric shapes, and object ordering;
- valid and invalid calendar instants at precision boundaries;
- every topic family, topic/event binding, and wildcard refusal;
- each envelope validation stage and multi-invalid-input precedence;
- digest context separation, versioning, recomputation, and tampering; and
- projection completeness, transport-member stripping, and droppable-event policy;
- reduced-state sorting, state-document shape, pure total fold behavior, and insertion-order
  independence; and
- replay-state digest determinism and Python/TypeScript parity when the second implementation exists.

Use Hypothesis for invariants across large input spaces, while retaining focused examples that explain
each boundary. A property test supplements the shared committed fixtures; it does not replace them.
Mutation survivors in this Tier 1 package require a stronger assertion or an explicit, time-bounded
decision under repository policy, not a blanket exclusion.

## 6. Workspace and path hygiene

- Use the repository-root `.venv`, root `pyproject.toml`, and root `uv.lock`. Do not create a package-local
  virtual environment or lockfile, and never install this member globally.
- Run commands from the repository root unless a tool explicitly requires the member directory.
- Keep local guidance inside `packages/contracts/`. The root workspace discovers members through
  `packages/*`; placing documentation directly under `packages/` can be misread as a workspace member.
- Keep `CLAUDE.md` as a relative symlink whose literal target is `AGENTS.md`; do not duplicate the text.
- Do not track caches, build output, coverage data, mutation artifacts, or generated environments.
- Before staging a new guide, pass it explicitly to file-based hooks because diff discovery does not see
  an untracked file.

## 7. Required verification

Run the focused package and cross-tree contract suites from the repository root:

```sh
uv run --frozen pytest -q packages/contracts/tests
uv run --frozen pytest -q tests/contract
just check-aaa
just check-contracts
```

For implementation changes, also run the tests of every affected consumer and the current Tier 1
coverage and mutation gates. Run formatting, Ruff, strict mypy, security checks, and the production build
at the scope required by [`docs/TESTING.md`](../../docs/TESTING.md).

Before handoff, run:

```sh
just check-types
just check-commit
just check-push
git diff --check
```

Inspect the complete diff and verify every changed schema, fixture, manifest entry, and document link.
Report any check that could not run; an unavailable cross-language consumer, broker, or external runtime
is not evidence that compatibility passed.
