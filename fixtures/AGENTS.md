# Shared Fixture Instructions

## 1. Scope and authority

These instructions apply to every file under `fixtures/`. Read the repository-root
[`AGENTS.md`](../AGENTS.md) first. Its TDD, safety, privacy, documentation, and version-control rules
still apply. Golden-contract work also requires the local guides for
[`schemas/`](../schemas/AGENTS.md), [`packages/contracts/`](../packages/contracts/AGENTS.md), and
[`tests/`](../tests/AGENTS.md).

Fixtures are committed executable inputs shared by independent consumers. They are not illustrative
examples, captured runtime data, or a second source of contract values. Read the owner of each fact before
editing it:

| Concern | Authority or reference |
| --- | --- |
| Wire shapes, canonical profile, topic semantics, and validator-only rules | [`docs/CONTRACTS.md`](../docs/CONTRACTS.md) |
| Process, red-green-refactor, and test-change approval | [Root `AGENTS.md` section 5](../AGENTS.md#5-test-driven-development) |
| Fixture versus executable-test structure and contract-test classes | [`docs/TESTING.md`](../docs/TESTING.md) |
| Bounds and the instrument that measures each one | [`docs/operating-parameters.md`](../docs/operating-parameters.md) |
| Schema, manifest, and fixture coordination | [`schemas/AGENTS.md`](../schemas/AGENTS.md) |
| Python contract behavior and compatibility surface | [`packages/contracts/AGENTS.md`](../packages/contracts/AGENTS.md) |
| Cross-component oracles and their claim limits | [`tests/AGENTS.md`](../tests/AGENTS.md) |
| Offline one-owner manifest | [ADR-0021](../docs/adr/0021-contract-artifact-manifest.md) |
| Integer-only canonical serialization | [ADR-0027](../docs/adr/0027-integer-only-canonical-serialization.md) |
| Immediate-directory fan-out | [ADR-0033](../docs/adr/0033-bound-directory-fan-out.md) |
| Topic grammar and refusal order | [ADR-0036](../docs/adr/0036-ascii-topic-grammar-bound-to-event-type.md) |
| Closed CloudEvents envelope | [ADR-0037](../docs/adr/0037-cloudevents-envelope-profile.md) |
| Fixture layout and one-reason negatives | [ADR-0038](../docs/adr/0038-reserved-host-schema-identity-and-one-reason-fixtures.md) |
| Shared TypeScript validation obligations | [ADR-0058](../docs/adr/0058-validate-dashboard-inputs-against-the-committed-schemas.md) |
| Dashboard projection and reduced-state fixtures | [ADR-0067](../docs/adr/0067-normalized-dashboard-events-and-reduced-state.md) |

An Accepted architecture decision record (ADR) governs if a fixture, manifest entry, schema,
implementation, test, or document disagrees. Changing accepted shape, polarity, refusal behavior,
canonicalization, or verification policy is not a fixture-only cleanup; perform the decision and
cross-tree work required by the root guide.

This root directory is for shared, independently consumed evidence. Keep package-private support data
beside its owning tests. Do not place contract JSON outside `fixtures/golden/` to evade manifest discovery.
A new top-level fixture class such as replay, scenario, or evaluation data needs a named owner, privacy
rules, executable consumer, and verification path before its first artifact lands.

## 2. Current layout and ownership

The current version-one fixture families have distinct jobs:

| Path | Responsibility |
| --- | --- |
| `golden/v1/canonical/` | Values at and outside the integer-only canonical profile |
| `golden/v1/envelope/` | Accepted envelopes and one-rule missing, context, or extension refusals |
| `golden/v1/payload/<event>/` | Accepted payload boundaries and payload-schema refusals |
| `golden/v1/event/<event>/` | Composed envelope, type, dataschema, and payload binding cases |
| `golden/v1/topics/` | Schema-valid accepted/refused parser cases plus schema-invalid case documents |

[`schemas/contract-manifest.toml`](../schemas/contract-manifest.toml) is the only ownership registry.
Every `fixtures/golden/**/*.json` artifact must:

- be a JSON object;
- appear exactly once in one manifest entry's `valid` or `invalid` list;
- have one registered owning schema;
- use a repository-relative regular-file path that remains inside the repository; and
- contribute to an owner that retains at least one schema-valid and one schema-invalid fixture.

The directory path makes the intended schema visible to a reviewer, but the manifest is authoritative.
Never infer ownership from a filename, register one physical file under two schemas, or symlink one case
into several owners. When the same document must be exercised by two schemas, keep deliberate physical
copies under the two schema-specific paths and register each copy once.

The accepted envelope document currently has three byte-identical copies:

- `golden/v1/envelope/valid/baseline.json`;
- `golden/v1/event/drone-telemetry/baseline.json`; and
- `../packages/contracts/tests/envelope_baseline.json`.

The first two are intentionally separate manifest-owned artifacts; the third is member-local support for
the Python unit and property suites. No gate currently proves those copies stay equal. Inspect all three
when changing that representative document, and never replace the copies with symlinks or a fixture path
that crosses ownership boundaries.

## 3. Author accepted and rejected cases deliberately

Golden JSON is a test support artifact, so it does not contain AAA comments. Every project-owned test that
consumes it still follows the exact AAA grammar in `docs/TESTING.md`. For new contract behavior, add and
register the smallest fixture that expresses the expected verdict, run the owning oracle to observe the
intended red result, and then change the implementation or schema. Never flip manifest polarity, broaden
a valid example, or weaken an invalid example merely to make new code pass.

For accepted fixtures:

- start with a minimal representative document that contains no irrelevant members;
- add separate boundary-positive cases for inclusive minima, maxima, and other meaningful edges;
- use values fixed in source rather than current clocks, random identifiers, host state, or generated
  runtime output; and
- preserve the surrounding member order for reviewability; raw fixture member order is not contract
  semantics.

For rejected fixtures:

1. Choose a specific accepted baseline for the same owning schema.
2. Change exactly one member, including one nested member when that is the intended boundary.
3. Name the file for that single mutation or missing member.
4. Confirm the owning schema reports exactly one validation error.
5. Compare baseline and negative manually; the automated error count cannot prove a one-member delta.
6. Register the path once under `invalid` without embedding expected-error metadata in the document.

Keep every payload-schema negative inside the canonical profile so it isolates the payload rule. Keep a
composed-event negative structurally valid against the envelope schema except for the binding or payload
constraint its owning event schema is intended to exercise. An invalid fixture that fails a prerequisite
first is false evidence for the later rule named by its file.

Only schema-expressible refusals belong in a schema-negative golden set. Duplicate JSON keys, an invalid
calendar date that matches the structural instant pattern, an integral real such as `1.0`, UTF-8 byte
length that differs from code-point length, and bindings unavailable to the owning schema require raw or
language-owned unit tests instead. A composed event schema may own a type, dataschema, or payload binding
negative that it explicitly expresses; do not register the same case as an envelope-schema negative.

Never add duplicate keys to a golden JSON object. The manifest and root-oracle loaders use a normal JSON
object parser, which would collapse duplicate-key evidence before validation. Raw repeated-key behavior
belongs in decoder tests that retain the original bytes.

Closed payloads cannot carry an `expectedError`, comment, provenance, or test-only member. The path and
filename explain why a case exists; the manifest records schema polarity; the executable oracle records
the expected semantic result. If a future fixture format needs expectation fields, define and register an
owning schema for that format rather than adding markers to an application payload.

## 4. Keep topic schema polarity separate from parser verdict

Topic case documents have two layers of meaning:

- `topics/accepted.json` and `topics/refused.json` are both manifest-`valid` documents for
  `topic-cases.schema.json`;
- each case's `verdict` and, for refusals, typed `refusal` value are semantic parser expectations; and
- `topics/case-without-topic.json` is manifest-`invalid` because the case document violates its schema.

Never move `refused.json` to the manifest's `invalid` list. Schema validity proves the case-file shape;
the root topic oracle proves the recorded parse, format, and event-type outcomes plus coverage of the
text-reachable refusal set. Member-local topic tests exercise the broader refusal precedence. A parser
refusal name is compatibility surface, not descriptive prose.

An accepted case records the family, mission identifier, parameters, round-tripped topic, and derived
event type expected from `packages/contracts/topics.py`. A refused case isolates the earliest applicable
refusal. Coordinate a topic-family, grammar, decision value, parameter, refusal, or case-format change
with the topic schema, Python implementation and tests, `docs/CONTRACTS.md`, the root oracle, and the
independent TypeScript implementation when it exists.

Constructor-only or otherwise text-unreachable refusals remain language-owned unit tests. Do not add a
contrived golden string merely to make an enum appear in the shared case file.

## 5. Preserve bytes, layout, and reviewability

[`../.gitattributes`](../.gitattributes) marks `fixtures/golden/**` with `-text` because exact bytes
matter. Git does not normalize their line endings. Preserve the current two-space JSON indentation, LF
line endings, final newline, member order, raw UTF-8 spelling, and untouched bytes outside the intended
case. Do not run a bulk JSON formatter, write through `jq`, reorder object members, escape Unicode, or
normalize neighboring fixtures as incidental cleanup.

`linguist-generated=true` only controls repository presentation; it does not mean the fixtures are
mechanically generated or safe to replace wholesale. Review them as hand-authored executable data. Use a
forced text diff when normal Git output collapses a fixture change, and compare each negative directly
with its accepted baseline. For a new untracked fixture, inspect it against `/dev/null` as well as against
that baseline.

The present oracles parse fixture values. They establish canonicalizer acceptance or refusal where
tested, but they do not compare committed expected canonical bytes or expected digests. Byte preservation
supports reproducibility and future cross-language evidence; it does not turn the current suite into
byte-for-byte canonicalization evidence.

The repository permits at most twenty files whose immediate parent is one directory. Subdivide by a
schema-relevant concern before adding the file that would cross the limit; do not seek an exemption for a
growing fixture family. Keep filenames lowercase, hyphenated, and specific to the one boundary they
exercise. A directory split changes manifest paths, so move paths and registrations atomically.

## 6. Public-data and security hygiene

This repository is public. Every fixture must be deterministic, anonymous, synthetic, and safe to expose
forever. Never copy data from a live run, broker export, provider response, tenant, operator session, or
real search-and-rescue incident.

Do not commit:

- passwords, API keys, tokens, private keys, authorization headers, cookies, or expanded environment
  values;
- tenant identifiers, tenant URLs, private broker addresses, internal hostnames, or real account names;
- real-person names, contact details, identifying telemetry, exact incident coordinates, biometrics, or
  unreviewed imagery;
- raw prompts, completions, model traces, provider metadata, or tool payloads captured from a run; or
- timestamps, identifiers, or correlation values copied from operational logs.

Use clearly synthetic identifiers, fixed timestamps, reserved hosts, and fabricated coordinates. A
future schema may require a minimal synthetic structured model result, but its content must be authored
for the test and must not be copied from a provider. A secret scanner is a backstop, not proof that a
fixture contains no tenant value, personal datum, or plausible credential.

The typo checker excludes `fixtures/golden/` because deliberate boundary strings produce false positives.
Manually inspect filenames, ordinary prose-like strings, field names, and semantic enum values for errors;
a spellcheck pass elsewhere does not cover them. Do not “correct” intentionally malformed boundary data
without first proving which contract rule it exercises.

## 7. Coordinate consumers and calibrate claims

A fixture change may require a focused subset of these owners, determined by the behavior it changes:

- its schema and the one-owner manifest entry under `schemas/`;
- Python contract constants, bindings, validators, canonicalizer, topic parser, projections, and local
  tests under `packages/contracts/`;
- schema-identity and shared-fixture oracles under `tests/contract/`;
- `docs/CONTRACTS.md`, an operating parameter, and the governing or superseding ADR;
- generated TypeScript types, runtime schema registry, validator-only tests, topic refusal parity,
  projection/reducer parity, and replay digest when those consumers exist; and
- every broker, domain, service, recorder, replay, API, or persisted-data consumer reached by the shape.

Adding an application event is not complete with one payload example. Its binding, payload schema,
composed event schema, projection row, reduced-state rule, accepted and rejected fixtures, manifest
entries, and current language tests move together as required by the governing ADRs.

Keep claims within the current instruments:

- the manifest gate proves complete one-owner inventory, JSON-object loading, offline schema references,
  metaschema validity, and registered schema polarity;
- the root fixture oracle additionally proves one schema error per registered negative and current
  schema/Python/canonicalizer/topic agreement; and
- manual review proves the one-member baseline delta and that the synthetic data is safe and meaningful.

Neither instrument proves independent contract correctness, exact canonical output bytes or digests,
validator-only behavior, unimplemented event families, an unexercised TypeScript implementation, broker
delivery, runtime acceptance, replay determinism, or live operational behavior. A golden fixture is not a
recorded run or release-evidence artifact.

## 8. Workspace hygiene and required verification

Use the repository-root `.venv`, `pyproject.toml`, and `uv.lock`; do not create an environment under
`fixtures/`. Run every command from the repository root because the contract gate discovers artifacts
relative to the current directory. Keep `CLAUDE.md` as a relative symlink whose literal target is
`AGENTS.md`.

For a fresh checkout, create the root environment with:

```sh
uv sync --all-packages --frozen
```

For a fixture change, run the JSON, inventory, fan-out, and executable contract checks:

Before the aggregate hooks, stage every intended new fixture so `--all-files` enumeration and the
staged-diff `gitleaks` hook include it. If a new fixture must remain untracked during the red phase, pass
its path explicitly to `check-json` and `detect-private-key`; the stock `gitleaks` hook cannot inspect it
until it is staged.

```sh
pre-commit run check-json --all-files --hook-stage pre-commit
pre-commit run contract-artifacts --all-files --hook-stage pre-commit
pre-commit run directory-fanout --all-files --hook-stage pre-commit
pre-commit run test-aaa --all-files --hook-stage pre-commit
uv run --frozen pytest -q tests/contract
uv run --frozen pytest -q packages/contracts/tests
```

Run `check-metaschema` and schema-identity tests when a schema changes. Run generated-type freshness,
TypeScript contract tests, projection/reducer parity, and every affected consumer when those artifacts
exist. `just check-contracts` runs the manifest gate only; it does not establish one-error negatives,
Python parity, topic semantic coverage, or manual one-member deltas.

A changed fixture is a non-Python dependency the import graph cannot narrow, so the commit-stage selector
deliberately widens to the full deterministic root suite. Do not bypass that run or substitute the focused
oracles for the pre-push coverage authority.

For a guide-only change, pass the untracked files explicitly to file-based hooks:

```sh
pre-commit run markdownlint-cli2 --files fixtures/AGENTS.md fixtures/CLAUDE.md \
  --hook-stage pre-commit
pre-commit run typos --files fixtures/AGENTS.md fixtures/CLAUDE.md \
  --hook-stage pre-commit
pre-commit run docs-facts-and-links --files fixtures/AGENTS.md fixtures/CLAUDE.md \
  --hook-stage pre-commit
pre-commit run docs-strict --files fixtures/AGENTS.md fixtures/CLAUDE.md \
  --hook-stage pre-commit
pre-commit run check-symlinks --files fixtures/AGENTS.md fixtures/CLAUDE.md \
  --hook-stage pre-commit
pre-commit run destroyed-symlinks --files fixtures/AGENTS.md fixtures/CLAUDE.md \
  --hook-stage pre-commit
pre-commit run detect-private-key --files fixtures/AGENTS.md fixtures/CLAUDE.md \
  --hook-stage pre-commit
```

Finish with:

```sh
pre-commit run --all-files --hook-stage pre-commit
pre-commit run --all-files --hook-stage pre-push
git diff --check
```

Inspect the complete forced-text fixture diff, every manifest path and polarity, the symlink target, and
all affected consumers. Confirm that no unrelated byte changed and no secret, tenant value, real-person
data, generated cache, or live artifact is tracked. Report any absent cross-language consumer or unrun
environment-dependent check as an open verification obligation, not as a pass.
