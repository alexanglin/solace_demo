# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
entries are derived from [Conventional Commits](https://www.conventionalcommits.org/).
See [CONTRIBUTING.md](CONTRIBUTING.md) for the commit convention.

## [Unreleased]

### Added

- A fail-closed directory fan-out gate, so structure is enforced rather than reviewed. Every other
  maintainability property here already had a number and a gate; how many files one directory holds did
  not. The limit is 20 immediate children, chosen because the tree had a wide empty band between the
  largest conforming directory at 7 and the four outliers at 22 and above, so it separates them without
  arguing about borderline cases. Counting is deliberately not recursive: a recursive count fails a
  parent *because* its children were split up, which is the opposite of the intent
  ([ADR-0033](docs/adr/0033-bound-directory-fan-out.md)).

  Exemptions live in `directory-fanout.toml` and are enforced in both directions, as the dependency
  waivers are: a directory over the limit with no entry fails, and an entry naming a directory that is
  no longer over the limit fails as a dead exemption. Unlike a dependency waiver they carry no expiry,
  because a structural exemption has nothing to wait for and a recurring re-review that can only reach
  the same conclusion is paperwork rather than a control. Two are granted -- the repository root, whose
  manifests are located by tools that look only there, and `docs/adr/`, where every document links
  records relatively and an accepted record is never renamed.

  The enumeration lives in the hook script rather than the gate. ADR-0025 confines `subprocess` to four
  reviewed Python owners, and counting directory entries is not a reason to reopen that decision, so the
  gate is a pure function of the listing and the registry.

- Phase 0 ran for the first time and settled three of the open questions the register deferred to it.
  `solace-pubsubplus` 1.11.0 does function on Python 3.14.7 rather than merely install: the bundled
  native library loads, session creation marshals its callback structures, and the API version,
  application identifier and a message payload read back, none of it needing a broker. ADR-0004's
  split-runtime decision survives its kill criterion.

- Agent Mesh 1.28.7, `sam-event-mesh-gateway` 1.1.0 and `sam-event-mesh-tool` 0.1.1 are pinned on
  Python 3.13.15 and proven to work together. Nothing upstream attests the combination -- the gateway
  declares no dependency on Agent Mesh and the tool declares none at all -- so the probes assert the
  runtime symbols each plugin imports rather than resolution alone. The tool ships no entry point and
  is imported by module path, as agent configuration wires it.

- A test stage for the Agent Mesh domain, which previously had none. It was linted, type-checked,
  security-scanned, audited and lock-verified, but no hook ran its tests at any stage, so a
  compatibility probe there would have been committed and never executed. The hook enters the project
  directory rather than passing `--project`, because pytest is rooted at the working directory and
  `--project` does not change it.

- `TECH_DEBT.md`, `README.md`, `NOTICE`, `.env.example`, and Phase 0 acceptance evidence under
  `release-evidence/`. The technical-debt register exists because a machine-readable waiver registry
  cannot tell a reader which of eleven advisories is unauthenticated remote code execution and which
  is a packaging glob bug.

- The first production code in the repository: `packages/contracts` now canonicalizes, parses, and
  digests digest-covered payloads, at 100% statement and branch coverage with a 100% mutation score and
  no reviewed survivors. Two mutants were equivalent rather than untested and were removed at the source:
  a codec name normalizes so `"utf-8"` and `"UTF-8"` cannot be told apart, and a surrogate bound written
  as a character literal is the same character in either hexadecimal case.

- ADR-0027 and the canonical serialization contract, discharging the open question ADR-0006 left and
  unblocking every digest in the system. Digest-covered payloads use an integer-only JSON profile in
  which no floating-point value is representable: coordinates are integer microdegrees and the evidence
  score is integer hundredths, so bypass case B14 becomes impossible by construction rather than defended
  against. RFC 8785 was rejected because its ECMAScript number formatting is defined over IEEE-754
  doubles, which makes formatting deterministic while leaving distinct coordinates free to alias.

- ADR-0026, `dependency-waivers.toml`, and `tools/dependency_waiver_gate.py`, making the
  time-bounded waiver `AGENTS.md` already required actually executable. The dependency audit now
  adjudicates pip-audit's JSON report rather than trusting its exit status, and enforces the
  contract in both directions: no advisory may go unwaived, and no waiver may outlive the advisory
  it was written for. Without this, pinning Agent Mesh 1.28.7 would have failed the audit
  permanently, leaving `--no-verify` as the only way to commit.

- ADR-0023 and blocking pre-push/CI gates for cognitive complexity, multi-language duplication, and
  independent Tier 1 mutation runs. Mutation results are scored per module; the survivor registry is
  exact, expiring, and cannot remove survivors from the score denominator.
- Check-only hooks for the commit, commit-message, push, checkout, merge, and pre-merge stages; GitHub
  Actions re-runs the same fail-closed entry points, with shared fixtures covering hook activation,
  revision ranges, environment hygiene, diagram integrity, and dependency synchronization.
- Offline, fail-closed gates for contract-artifact ownership, per-member statement and branch coverage,
  and domain import boundaries; active members fail on missing manifests, fixtures, schemas, tiers, or
  measurable source.
- A fail-closed, whole-tree Arrange-Act-Assert checker for Python, JavaScript, TypeScript, Vitest, and
  Playwright tests, with conformance coverage for nested Python assertions, dynamic registrations,
  syntax-based imports, and bare `expect(...)` assertions.
- A Python 3.14.7 uv application workspace with five typed library packages, six typed service packages,
  explicit per-member risk tiers, and one lock resolved for macOS arm64 and Linux aarch64; Agent Mesh
  remains isolated behind its Python 3.13.15 interpreter pin.
- ADR-0024 defining the exact single-operator local API boundary: loopback-only binding, Host validation
  on every request, browser-Origin validation for mutations, and a fresh per-runtime bearer for the three
  state-changing endpoints. Canonical digest serialization remains blocked on its own future ADR.
- ADR-0022 defining recursive integrity requirements for editable diagram sources, generated PNG
  signatures, and hashes of both artifacts.
- ADR-0021 defining the offline contract-artifact manifest and ownership requirements.
- ADR-0020 pinning uv 0.12.5 across local development and CI.
- ADR-0019 recording the fail-closed activation contract and exact verification toolchain.
- ADR-0018 defining mandatory, syntax-aware Arrange-Act-Assert structure for every project-owned
  executable test.
- ADR-0017 naming `mutmut` 3.7.0, a 90% killed-mutant score, and a risk tier for
  every package — discharging the two deferrals ADR-0015 left open.
- `docs/LIMITATIONS.md`, stating what the system does and does not model for a
  reader from the search-and-rescue domain.
- `docs/security/threat-model.md` and
  `docs/security/approval-bypass-catalogue.md`, the latter enumerating 35 bypass
  attempts so the "zero authorized actions" target quantifies over a defined set.

- Architecture decision records under `docs/adr/`, covering the self-hosted
  open-source Agent Mesh baseline, paid orchestration under an enforced budget, Postgres as the
  durable mission store, split Python runtimes, the deterministic command
  gateway, proposal-bound approvals, replay isolation, and the quality regime.
- `CONTRIBUTING.md` describing the branching model, commit convention, and what
  runs at each stage.
- An editable Graphviz architecture overview with its generated PNG and integrity sidecar.

### Changed

- Decomposed the two directories the fan-out gate was written for, rather than waiving them. A gate whose
  first act is to waive the only violations it found has not been enforced. `tools/quality_gate_tests/`
  became four concern subpackages -- `hooks/`, `coverage/`, `contracts/`, `analysis/` -- and
  `scripts/hooks/` became five: `python/`, `dashboard/`, `deps/`, `docs/`, `repo/`. No assertion changed
  in either move; the suite runs the same 239 tests before and after.

  Two files deliberately stayed where they were. `test_diagram_integrity.py` and the three hook scripts
  named by accepted records -- `agent-mesh-test-full.sh` (ADR-0029), `check-env-template.sh` (ADR-0032)
  and `check-docs-strict.sh` (ADR-0017) -- keep their paths, because an ADR is immutable and moving them
  would leave four accepted records stating paths that no longer resolve. `test_diagram_integrity.py` is
  additionally one of the four exact paths ADR-0025's `S603` allowlist names, so moving it would have
  required reopening that decision to relocate a file.

  The shared test fixture now resolves a hook script by basename wherever it sits, so a script's group
  can change without rewriting its forty call sites.

- Synchronized the whole uv workspace from the post-checkout and post-merge hook. It ran a bare
  `uv sync --frozen`, and because `uv sync` is exact by default that pruned every workspace member's
  editable install on each checkout, merge, and pull. A member test could then no longer import its own
  package, so `pytest-unit-fast` failed until someone re-ran the sync by hand. CI never saw this: it
  syncs with `--all-packages` explicitly and runs no post-checkout hook.

- Excluded mutmut's generated `mutants/` tree from type checking and test collection. Ruff honours
  `.gitignore` and mypy and pytest do not, so after any mutation run mypy reported the member's package
  as a duplicate module and pytest collected a second copy of every tier-one test. The failure was
  order-dependent: `mypy-full` and `pytest-full` run before `mutation-full`, so the first pre-push pass
  succeeded and every later one failed.

- Repaired the quality-gate test fixtures, which inherited `GIT_DIR` and `GIT_INDEX_FILE` from the
  process running them. Inside a git hook that aimed every fixture command at the repository
  running the hook, so `pytest-unit-fast` failed whenever a Python file was staged while passing
  when the suite was run by hand.

- Replaced global and broad-test Ruff `S603`/`S607` ignores with ADR-0025's exact four-file `S603`
  allowlist, removed every `S607` waiver, and made required Git execution absolute and fail closed.
- Kept deterministic evidence scoring and fleet state machines in Tier 1 domain code; the evidence and
  fleet services remain Tier 2 coordination and adapter boundaries.
- Standardized the decision metric as an evidence score across the architecture, testing, limitations,
  and security documentation.
- Split the normative documentation set so every fact has one home, per ADR-0016
  (now Accepted): `docs/ARCHITECTURE.md`, `docs/CONTRACTS.md`, `docs/SAFETY.md`,
  `docs/TESTING.md`, and `docs/operating-parameters.md`.
  `docs/IMPLEMENTATION_PLAN.md` drops from 510 to ~300 lines and keeps sequenced
  delivery only; `AGENTS.md` drops from 249 to ~180 lines and keeps process rules
  only. Zero substantive lines are now duplicated across the set.
- Added a document precedence rule and a "Decided by" column linking each
  confirmed decision to its ADR, with `—` where a decision still owes one. Neither
  governing document previously referenced `docs/adr/` at all.
- Reconciled the plan with its own decision log: the durable store is Postgres per
  ADR-0003 (the plan still specified the superseded SQLite store), approvals are
  exempt from idempotent replay per ADR-0006, replay isolation is structural per
  ADR-0009, imagery is artifact-only per ADR-0013, and the replay-determinism
  oracle compares reduced dashboard state rather than raw event streams.
- Restated the coverage gate per language. The former flat "95% across statements,
  branches, functions, and lines" was not computable: `coverage.py` has no
  function-coverage metric and statements and lines are the same measurement.
- Accepted ADR-0015 and ADR-0016, both of which were already load-bearing in
  tooling while still marked Proposed.
- Recorded ADR-0002's decision that paid Anthropic or OpenAI models are permitted for
  the Agent Mesh `general` and `planning` roles under an enforced USD $50 cap with
  a persisted spend ledger and pre-call enforcement. The three edge agents stay on
  local Ollama, and local-only operation remains a supported, tested configuration
  so no release gate depends on a paid API.

### Fixed

- The continuous-integration credential guard asserted that `SOLACE_URL` and `SOLACE_PASSWORD` were
  unset. Neither name is what the runtime reads: the pinned Event Mesh templates use
  `SOLACE_BROKER_URL` and `SOLACE_BROKER_PASSWORD`, so a real broker credential configured under the
  name the templates consume would have passed the guard unnoticed.

- Two stages type-checked the Agent Mesh tree under different settings. mypy reads configuration from
  the working directory and never searches parents, and explicitly named files bypass its exclude
  list, so the pre-commit hook running from the repository root applied the root table's Python 3.14
  while the pre-push script applied whatever the project declared. The configuration file is now named
  explicitly.

- A comment claimed `pytest-related.sh` selects `-m unit`, so an unmarked suite would be silently
  deselected. It does not; both blocking suites select by resource, not by test class.

### Security

- Eleven advisories across five packages are recorded as expiring, reviewed waivers, and the audit
  gate passes honestly rather than by bypass. Every affected package is pinned exactly by Agent Mesh
  1.28.7 and 1.28.7 is the latest upstream release, so no safe upgrade exists for any of them.

- `google-adk` 1.18.0 carries unauthenticated remote code execution with no satisfiable fix: the
  override the register required be attempted resolves to nothing, because 1.28.1 needs `google-genai`
  and `fastapi` versions above Agent Mesh's exact pins. What bounds the risk is the absence of a
  network path -- loopback-only binding, no public ingress, and a command gateway outside model
  control -- rather than the absence of the vulnerability. The advisory is reported as
  `PYSEC-2026-344`; the register named a CVE alias, which would have failed the waiver gate in both
  directions at once.
