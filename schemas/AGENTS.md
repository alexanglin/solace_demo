# Schema Contract Instructions

## 1. Scope and authority

These instructions apply to `schemas/contract-manifest.toml` and every versioned schema under
`schemas/`. Read the repository-root [`AGENTS.md`](../AGENTS.md) first. Its safety, TDD,
documentation, security, and version-control rules still apply. Also read
[`packages/contracts/AGENTS.md`](../packages/contracts/AGENTS.md) before changing a schema that the
Python contract implementation consumes.

Schemas are the normative, language-neutral executable encoding of the wire contract, not documentation
examples and not output generated from Python or TypeScript types. A change here can alter what broker
ingress accepts, which bytes are safe to digest, what generated TypeScript exposes, and whether replay or
approval consumers agree. Use the source below for each concern rather than duplicating its current
values in this guide:

| Concern | Authority or reference |
| --- | --- |
| Wire shapes, allowed schema vocabulary, canonical profile, and validator-only rules | [`docs/CONTRACTS.md`](../docs/CONTRACTS.md) |
| Test classes, AAA, fixtures, contract tests, and verification stages | [`docs/TESTING.md`](../docs/TESTING.md) |
| Numeric contract bounds and their measuring instruments | [`operating-parameters.md`](../docs/operating-parameters.md) |
| Application-event namespace and major version separation | [ADR-0014](../docs/adr/0014-application-events-separate-from-a2a.md) |
| Offline manifest ownership and Draft 2020-12 | [ADR-0021](../docs/adr/0021-contract-artifact-manifest.md) |
| Integer-only canonical serialization | [ADR-0027](../docs/adr/0027-integer-only-canonical-serialization.md) |
| Topic grammar and event-type derivation | [ADR-0036](../docs/adr/0036-ascii-topic-grammar-bound-to-event-type.md) |
| Closed CloudEvents envelope and refusal order | [ADR-0037](../docs/adr/0037-cloudevents-envelope-profile.md) |
| Reserved-host identity, absolute references, fixture layout, and one-reason negatives | [ADR-0038](../docs/adr/0038-reserved-host-schema-identity-and-one-reason-fixtures.md) |
| Explicit string and array cardinality assertions | [ADR-0104](../docs/adr/0104-bound-dashboard-schema-strings-and-arrays-explicitly.md) |
| Generated dashboard types and runtime validation | [ADR-0058](../docs/adr/0058-validate-dashboard-inputs-against-the-committed-schemas.md) |
| Ordered dashboard events, reduced state, and SSE frames | [ADR-0101](../docs/adr/0101-order-dashboard-events-outside-the-five-field-projection.md) |

An Accepted architecture decision record (ADR) governs whenever a schema, fixture, implementation, or
document disagrees with it. Never rewrite Accepted ADR prose. Create a new or superseding record before
changing a decided wire shape, dialect, identifier scheme, allowed vocabulary, version boundary, or
verification mechanism.

## 2. Directory ownership

| Path | Responsibility |
| --- | --- |
| `contract-manifest.toml` | Complete one-owner inventory of every schema and golden fixture |
| `v1/canonical.schema.json` | Recursive canonical value profile and shared version-one definitions |
| `v1/envelope.schema.json` | Structural closed-member CloudEvents envelope profile |
| `v1/payload/` | Application-event payload shapes |
| `v1/event/` | Composed schemas binding an envelope's `type`, `dataschema`, and `data` |
| `v1/rpc/` | Request/reply bodies that are not application events ([ADR-0068](../docs/adr/0068-command-gateway-request-reply-is-schema-bound-rpc.md)) |
| `v1/topic-cases.schema.json` | Common shape of accepted and refused topic case files |

Keep reusable wire definitions in `canonical.schema.json` and reference them instead of copying patterns
or bounds into payloads. Every application-event payload has a matching composed event schema. A body
under `v1/rpc/` has neither, because it is a question rather than a statement that something happened;
a reply that is *also* republished as an event lives under `v1/payload/` and carries the composed
schema, so one definition serves both uses. The
payload schema has its own `$id`, which the envelope `dataschema` and Python binding name; the composed
event schema has a distinct event-schema `$id` and constrains the payload, event type, and schema binding
together.

Schemas describe representation and structural validation. They do not own mission authorization,
state-transition policy, broker subscriptions, persistence, or transport settlement. Dashboard event and
reduced-state documents are cross-language contract shapes under ADR-0067; mutable browser presentation
state is not.

## 3. Author schemas for identical cross-language behavior

- Use only JSON Schema Draft 2020-12 and the exact keyword subset listed in
  [`docs/CONTRACTS.md`](../docs/CONTRACTS.md). A new keyword needs cross-engine evidence and the decision
  work required by the root instructions.
- Never use `format`. Its assertion behavior is implementation-dependent. Express supported structural
  rules with the approved explicit vocabulary and leave rules that JSON Schema cannot express to the
  Python and TypeScript validators.
- Keep regular expressions ASCII-explicit and valid in both Python and ECMA-262. Use explicit character
  classes such as `[0-9]`; do not introduce shorthand classes, locale behavior, or engine-specific
  constructs.
- Keep shared patterns and bounds equal to their Python contract constants and the canonical prose or
  operating parameter that owns them. Change all owners atomically; neither a copied constant nor a test
  expectation becomes authority through repetition.
- Close wire objects deliberately with `required`, `properties`, and `additionalProperties: false`.
  Preserve the recursive canonical object's intentional open value shape. Do not add defaults, coercion,
  mutation, or permissive catch-all branches.
- Keep `required` and `properties` in the documented contract order so reviews can compare a schema with
  the envelope or payload table directly. Avoid mechanical reordering that obscures a semantic change.
- Treat descriptions as reader guidance, not a second contract. Put normative semantics in
  [`docs/CONTRACTS.md`](../docs/CONTRACTS.md) or the governing ADR and reference them.

JSON Schema is intentionally only one validation layer. It cannot detect duplicate JSON keys after a
permissive parse, distinguish an integral real such as `1.0` from an integer in every implementation,
measure a UTF-8 byte limit through `maxLength`, prove a calendar date exists, or enforce the generic
unbound-type, `dataschema`, subject, and topic bindings. Do not loosen a schema to imitate those rules and
do not claim that schema validity alone is wire acceptance. Put validator-only cases in language-owned
unit tests, not in a golden-negative set whose owning schema cannot express the refusal. A composed event
schema may own the binding negatives it explicitly expresses.

## 4. Preserve identity, references, and versions

Every schema identity is its repository-relative path appended to the reserved
`https://aerial-rescue.invalid/` base. The host must never be fetched. A move or rename changes identity;
treat it as a contract and versioning change rather than file organization.

- `$schema` names the supported Draft 2020-12 metaschema exactly.
- `$id` corresponds exactly to the schema's repository path, including its version directory.
- `$ref` is either a same-document `#/$defs/...` reference, a bare absolute registered schema `$id`, or
  that absolute `$id` with a `#/$defs/...` fragment. Do not add other fragments, relative file
  references, or remote fallback behavior.
- Every referenced schema is present in the manifest-built in-memory registry. Validation remains
  deterministic and network-free.
- The topic namespace version, schema directory version, event type, envelope `dataschema`, Python
  binding, composed event schema, fixtures, and generated consumers advance together.

Breaking changes require a new major topic and schema version. Do not edit `v1` in place until the
governing decision establishes that the change is compatible. A field that appears optional in JSON
Schema may still be breaking for closed validators, generated types, refusal ordering, persisted events,
or replay, so prove compatibility across every consumer rather than assuming it.

## 5. Keep the manifest and fixtures atomic

[`contract-manifest.toml`](contract-manifest.toml) is the only artifact inventory. Preserve integer
`format = 1` until a new verification-policy decision changes it; a boolean is not an integer version.
Each contract table names one schema, a nonempty `valid` list, and a nonempty `invalid` list. Every
discovered `*.schema.json` under `schemas/` and every `*.json` file under `fixtures/golden/` is registered
exactly once and loads as a JSON object. Paths are repository-relative files; traversal, missing paths,
directories, duplicate ownership, and symlink escape all fail closed.

For every new or changed schema:

1. Start from a smallest representative accepted fixture.
2. Add boundary-positive fixtures needed to prove inclusive and exclusive edges.
3. Create each negative fixture from an accepted baseline by changing exactly one member.
4. Confirm each negative produces exactly one schema error for its owning schema.
5. Register every fixture once with the correct polarity in the manifest.
6. Subdivide fixture directories before they exceed the repository fan-out policy.

No gate proves the one-member delta automatically, so inspect that comparison manually. Payload fixtures,
including payload-schema negatives, remain inside the canonical profile so they isolate the payload rule
under test. Do not embed an expected error marker in a closed fixture; the marker would create another
failure. Fixture names and directories explain intent, while executable oracles prove it. Keep fixtures
anonymous, deterministic, minimal, and free of secrets or tenant-specific values. Golden fixtures are
marked byte-preserved in `.gitattributes`; do not normalize or reformat unrelated fixture bytes.

Topic case files have additional semantic obligations. Accepted cases record the parsed family,
parameters, and derived event type; refused cases record the exact typed refusal name. Both case files are
schema-valid fixtures—the refused verdict describes parser behavior, not manifest polarity. Coordinate
their enum values and coverage with `packages/contracts/topics.py`. The Python oracle, and the TypeScript
oracle when the dashboard exists, prove parser behavior and refusal parity.

The fast manifest gate proves inventory, metaschema validity, offline reference resolution, and fixture
polarity. The root contract oracle additionally proves Python/schema agreement, one-reason negatives,
topic refusal coverage, identifier mapping, allowed-keyword discipline, and equality with Python patterns
and bounds. Neither gate replaces the other.

## 6. Coordinate every contract consumer

A schema change normally reaches all of these owners in one atomic change:

- [`docs/CONTRACTS.md`](../docs/CONTRACTS.md), the governing ADR, and any affected operating parameter;
- Python constants, bindings, parsers, projectors, reducers, exports, and tests under
  [`packages/contracts/`](../packages/contracts/);
- accepted and one-reason-negative artifacts under [`fixtures/golden/`](../fixtures/golden/);
- the manifest registration in this directory;
- schema-identity and golden-fixture oracles under [`tests/contract/`](../tests/contract/), including any
  explicit inventory expectation such as `EXPECTED_SCHEMA_COUNT`;
- generated TypeScript contract types, their freshness check, runtime schema registry, validator-only
  rules, shared-fixture tests, projection/reducer parity, and replay digest when the dashboard exists; and
- every broker, domain, service, recorder, replay, API, and persisted-data consumer reached by the shape.

Adding an application event requires its payload schema, composed event schema, Python binding,
projection row, reduced-state rule, fixtures, manifest entries, and current Python tests together. Add the
TypeScript parity tests in the same change when the dashboard implementation exists. A recognized event
without the ADR-0067 projection and state decision is refused as unprojected; do not invent an exemption
in a schema.

Change [`tools/contract_gate.py`](../tools/contract_gate.py), its conformance tests, hook registration, or
manifest format only when the verification policy itself changes. That is a build/verification decision,
not an ordinary schema edit, and requires the ADR coordination specified by the root instructions.

## 7. Workspace hygiene and required verification

- Use the repository-root `.venv`, `pyproject.toml`, and `uv.lock`; do not create an environment or lock
  under `schemas/`. Run from the repository root: `tools.contract_gate` uses the current directory as its
  root and can falsely report an empty inactive inventory when invoked from inside `schemas/`.
- Preserve two-space JSON indentation, a final newline, and the surrounding file's ordering conventions.
  Do not hand-edit generated TypeScript output to conceal schema drift.
- Keep `CLAUDE.md` as a relative symlink whose literal target is `AGENTS.md`; do not duplicate this text.
- Do not track validator caches, generated reports, or temporary transformed schemas.
- Pass every new untracked guide, schema, and fixture explicitly to file-based Markdown, JSON, and
  metaschema hooks. The contract-artifact gate scans the filesystem and therefore sees untracked
  contract artifacts. Expect a staged schema or manifest change to widen affected-test selection to the
  full deterministic suite; do not bypass that fail-safe behavior.

From the repository root, run the focused schema and contract checks:

```sh
pre-commit run check-json --all-files --hook-stage pre-commit
pre-commit run check-metaschema --all-files --hook-stage pre-commit
pre-commit run contract-artifacts --all-files --hook-stage pre-commit
pre-commit run test-aaa --all-files --hook-stage pre-commit
uv run --frozen pytest -q tests/contract
uv run --frozen pytest -q packages/contracts/tests
```

Run the generated-type freshness, TypeScript contract, and dashboard tests when those consumers exist,
plus every affected service and integration suite. If the manifest format, artifact-gate policy, or
`tools/contract_gate.py` changes, also run:

```sh
uv run --frozen pytest -q tools/quality_gate_tests/contracts/test_contract_artifact_gate.py
```

Finish with:

```sh
pre-commit run mypy-full --all-files --hook-stage pre-push
pre-commit run dashboard-typecheck-full --all-files --hook-stage pre-push
pre-commit run --all-files --hook-stage pre-commit
pre-commit run --all-files --hook-stage pre-push
git diff --check
```

Inspect the complete schema/fixture/manifest diff. A missing consumer, generator, offline registry, or
cross-language runtime is an unverified compatibility obligation, not a pass.
