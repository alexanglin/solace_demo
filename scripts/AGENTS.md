# Repository Script Instructions

## 1. Scope and authority

These instructions apply to every file under `scripts/`. Read the repository-root
[`AGENTS.md`](../AGENTS.md) first. Its safety, TDD, documentation, and version-control rules still
apply. This directory owns execution and orchestration; typed parsing and policy verdicts belong under
[`tools/`](../tools/), whose local instructions also apply when a script launches a project-owned gate.

Use the canonical source for the fact being changed instead of copying its current value here:

| Concern | Canonical source |
| --- | --- |
| Hook stages and continuous-integration authority | [ADR-0012](../docs/adr/0012-git-hooks-with-ci-as-authority.md) |
| Fail-closed activation and evidence | [ADR-0019](../docs/adr/0019-fail-closed-quality-gates.md) |
| Deep quality gates and dependency waivers | [ADR-0023](../docs/adr/0023-executable-deep-quality-gates.md), [ADR-0026](../docs/adr/0026-expiring-dependency-waivers.md) |
| Subprocess ownership and shell/Python boundary | [ADR-0025](../docs/adr/0025-narrow-ruff-subprocess-waivers.md) |
| Hook decomposition and fixed paths | [ADR-0033](../docs/adr/0033-bound-directory-fan-out.md) |
| Diagram generation and integrity | [ADR-0022](../docs/adr/0022-recursive-diagram-integrity.md) |
| Agent Mesh configuration and execution context | [ADR-0029](../docs/adr/0029-verify-the-agent-mesh-domain-with-its-own-toolchain.md), [ADR-0032](../docs/adr/0032-agent-mesh-semantic-configuration-validator.md), [ADR-0062](../docs/adr/0062-type-check-the-agent-mesh-domain-from-its-own-directory.md) |
| Dynamic Python discovery and dashboard policy | [ADR-0056](../docs/adr/0056-raise-mypy-to-every-lever-the-tree-satisfies.md), [ADR-0057](../docs/adr/0057-typescript-strictness-baseline-before-the-dashboard.md) |
| Compose, certificates, image scanning, and SBOMs | [ADR-0045](../docs/adr/0045-fail-closed-compose-policy-gate.md), [ADR-0046](../docs/adr/0046-generated-local-certificate-authority.md), [ADR-0048](../docs/adr/0048-scan-images-and-deploy-configuration-with-trivy.md), [ADR-0055](../docs/adr/0055-block-on-the-image-pin-not-on-advisories-inside-it.md), [ADR-0162](../docs/adr/0162-generate-and-validate-per-image-cyclonedx-sboms.md) |
| Broker roles, grants, and lifecycle-source identity | [ADR-0061](../docs/adr/0061-least-privilege-broker-principals-and-topic-authorization.md), [ADR-0111](../docs/adr/0111-broker-dashboard-lifecycle-sources.md) |
| Terminal-safe execution and job budgets | [ADR-0059](../docs/adr/0059-keep-the-verification-authority-able-to-report.md), [`operating-parameters.md`](../docs/operating-parameters.md) |
| Test structure, classes, and toolchain | [`TESTING.md`](../docs/TESTING.md) |

Read the accepted ADR and component-local `AGENTS.md` governing the concern before changing its
wrapper. A script must preserve both the repository-wide execution rules below and the policy it makes
executable.

## 2. Directory ownership

| Path | Responsibility |
| --- | --- |
| `hooks/quality-components.sh` | Side-effect-free, sourced component activation and root Python discovery |
| `hooks/agent-mesh/` | Agent Mesh configuration and whole-tree type-check wrappers |
| `hooks/dashboard/` | TypeScript policy plus package-owned lint, type-check, coverage adjudication, deterministic integration, Playwright acceptance, and build wrappers |
| `hooks/deploy/` | Compose-policy and Trivy-configuration wrapper orchestration |
| `hooks/deps/` | Lock synchronization and consistency plus audit and waiver-gate orchestration |
| `hooks/docs/` | Documentation facts and links plus recursive diagram freshness |
| `hooks/python/` | Root and Agent Mesh Ruff, mypy, pytest/coverage, Bandit, complexity, and mutation orchestration |
| `hooks/repo/` | Commit ranges and messages, secret-file refusal, directory fan-out, and duplication orchestration |
| `ci/` | Closed continuous-integration orchestration and ADR-0186's one-shot broker-restart controller |
| `hooks/agent-mesh-test-full.sh`, `hooks/check-env-template.sh`, `hooks/check-docs-strict.sh` | Fixed ADR-named Agent Mesh test, environment-template, and strict-docs entry points |
| `security/` | Image-inventory pin checks, per-image scanning, and validated SBOM generation |
| `broker-secrets.sh`, `diagrams.sh`, `fix.sh` | Explicit operator mutations for local credentials, diagram artifacts, and formatting fixes |

Keep new hooks in the concern-named subdirectory and keep hook basenames unique; the hermetic test
harness resolves scripts by basename. Do not move a path named by an Accepted ADR without a
superseding decision.

## 3. Shell construction and process safety

- Write portable POSIX `sh` with `#!/usr/bin/env sh`; do not add Bash-only arrays, conditionals, or
  process substitution. Quote expansions and pass file lists as arguments rather than reparsing shell
  text.
- Gate scripts normally use `set -eu` and change to the checkout resolved by
  `git rev-parse --show-toplevel`. Preserve deliberate subshells and working-directory changes.
- `quality-components.sh` is sourced library code: it must not change shell options, working directory,
  positional arguments, or external state. `sync-deps.sh` is the deliberate nonblocking exception: a
  checkout or merge must not be stranded, so synchronization failures warn and return success.
- Validation hooks inspect and report; they never repair tracked source. The nonblocking lifecycle sync
  hook may update ignored dependency environments after checkout or merge. Other mutation belongs only
  in an explicit operator command such as `fix.sh`, `diagrams.sh`, or authorized secret creation or
  rotation. Do not copy `fix.sh`'s continue-after-failure behavior into a gate.
- Shell owns repository enumeration and external execution. Pass explicit listings or report files to
  pure Python gates for validation and adjudication; do not duplicate policy in shell or expand the
  Python subprocess-owner allowlist to avoid writing a wrapper.
- Preserve each wrapper's documented activation and enumeration predicate. Where an ADR requires Git's
  tracked-or-unignored scope, do not replace it with raw filesystem discovery; fixed source-root checks
  intentionally have different semantics. Keep dynamic root Python path discovery centralized in
  `quality-components.sh` rather than hard-coding package or service directories.
- Use deterministic ordering and stable, redacted diagnostics. Preserve documented output streams and
  exit statuses because callers and conformance tests treat them as command behavior.
- Create temporary files or directories with `mktemp`, install a trap before work begins, and remove
  every artifact on success, failure, and signal. Never place credentials or unredacted reports in a
  predictable path.
- When independent locks, images, reports, or policy legs can all be checked, accumulate their statuses
  and report every failure. Do not let `set -e` stop after the first independent result.
- Never run pageable Git with an inherited terminal. Use the load-bearing non-pager form and add a
  pseudo-terminal regression test for behavior that differs from pipes; bounded tests must terminate
  the whole child session so a pager or descendant cannot survive.
- Commit-range wrappers accept complete base/head pairs, reject incomplete pairs, validate objects, and
  distinguish deleted refs. Inspect committed objects for the requested range rather than the current
  working tree, and preserve the exact commit-message range semantics in their conformance tests.

## 4. Activation and toolchain boundaries

- Component-wide gates may pass inert only while both their manifest and owned source are absent. Once
  either activation input exists, a missing manifest, lock, executable, package script, validator, test
  suite, or generated report is a blocking failure. Specialized config, deployment, file-passing, and
  image gates arm on their own ADR-defined subjects; do not generalize one predicate to every wrapper.
- Treat scanner and audit reports as untrusted evidence. A tool's exit status alone is not a verdict;
  missing, empty, malformed, incomplete, or inconsistent output fails before the typed gate adjudicates
  findings.
- Keep the root and `agent-mesh/` Python projects on their separate frozen locks and environments.
  Agent Mesh pytest and mypy wrappers must change into `agent-mesh/` before `uv run --frozen`; selecting
  a project does not change pytest discovery, mypy configuration, or Python import resolution.
- Dashboard execution wrappers call committed lint, type-check, test, integration, and build scripts through
  `pnpm --dir apps/dashboard`. The TypeScript-policy wrapper enumerates inputs and launches its pure
  Python gate instead. The coverage wrapper writes its report to a temporary directory, enumerates
  tracked or unignored source, and passes both to the pure TypeScript coverage gate; it never trusts the
  runner's displayed percentage alone. The Playwright wrapper additionally refuses a runtime that differs from the
  manifest, verifies discovery against the manifest-owned test inventory, requires the package-pinned
  Chromium revision to be cached before it starts, and scans retained reports for the synthetic bearer
  sentinel even after a browser failure. It never downloads a browser from a local hook. Do not replace
  a package command with a convenient direct tool invocation that bypasses project references or policy.
- Static Compose and Dockerfile hooks must remain Docker-free. Image pin resolution, pulls, builds, and
  scans are explicit CI or operator work and must not be smuggled into a fast configuration hook.
- Secret generation must retain restrictive creation permissions, atomic role-environment replacement,
  fill-missing idempotence, and explicit rotation. Never print a key, password, authorization value, or
  rendered Compose configuration containing real role values. Coordinate role names with the domain
  principal model, broker provisioning, deployment wiring, and their tests.
- Diagram generation walks the documented source tree and renders each existing editable source into
  its PNG and integrity sidecar. Commit the complete source/PNG/sidecar triplet; never hand-edit the PNG
  or hash file, and inspect every changed PNG after generation.
- `justfile` provides human-facing aliases. Local hooks provide fast feedback, while CI invokes the
  identical project-owned entry points and remains the authority.

## 5. Coordinate cross-tree changes

For every changed hook, inspect its entry in [`.pre-commit-config.yaml`](../.pre-commit-config.yaml), its
CI prerequisites under [`.github/workflows/`](../.github/workflows/), its human-facing `justfile` alias,
the corresponding Python gate and conformance tests under [`tools/`](../tools/), and the canonical
documentation. Preserve local/CI entry-point parity and update every source-relative
`quality-components.sh` path together with its ShellCheck source directive.

Also coordinate the concern-specific owners:

- root Python discovery with workspace manifests, source roots, risk tiers, and coverage tests;
- Agent Mesh wrappers with `agent-mesh/{pyproject.toml,uv.lock,AGENTS.md}`;
- dashboard wrappers with `apps/dashboard/package.json`, its lockfile, and package scripts;
- deployment and image scripts with Compose, Dockerfiles, `.env.example`, image inventory, and
  [`deploy/AGENTS.md`](../deploy/AGENTS.md);
- dependency scripts with all governed manifests, locks, `dependency-waivers.toml`, and audit tests;
- diagram scripts with DOT sources, PNGs, sidecars, and visual inspection;
- secret scripts with domain roles, broker provisioning, deployment wiring, and redacted tests.

A change to a hook stage, tool or runtime pin, verification mechanism, safety boundary, or numeric budget
requires the ADR and document updates specified by the root instructions. Do not narrow
`pytest-related.sh` by path guesswork; affected-test selection requires project-owned dependency proof.

## 6. Tests for scripts

Script conformance tests live under `tools/quality_gate_tests/` and inherit
[`tools/AGENTS.md`](../tools/AGENTS.md). Follow TDD and mandatory AAA. Use `QualityGateTestCase`, a
temporary Git repository, deterministic identity, cleared inherited `GIT_*` context, a minimal `PATH`,
and bounded cleanup rather than the contributor's checkout or installed tools.

For a new or changed gate, cover every applicable case:

- fully inactive, first activating input, and ignored-input behavior;
- every missing prerequisite after activation;
- success with exact arguments, working directory, order, stream, and status;
- missing executable plus nonzero, missing, empty, malformed, and incomplete reports;
- all independent branches reporting rather than stopping at the first failure;
- temporary-artifact cleanup and sensitive-value redaction;
- hook registration at every required stage and parity with CI;
- terminal-backed no-pager behavior when output differs on a pseudo-terminal.

When adding or regrouping a hook, update manually maintained structural and full-wrapper tests as well as
path references. A helper may hide fixture construction, never the executable action or behavioral
assertion.

## 7. Required verification

From the repository root, run shell formatting, linting, and the focused wrapper suite:

```sh
pre-commit run shfmt --all-files --hook-stage pre-commit
pre-commit run shellcheck --all-files --hook-stage pre-commit
uv run --frozen pytest -q tools/quality_gate_tests/hooks
```

Run the concern-specific conformance tests and each safe, check-only canonical wrapper affected by the
change. For deployment, secret, scanner, and diagram wrappers, include the applicable suites under
`tools/quality_gate_tests/{analysis,contracts,deploy}/` and
`tools/quality_gate_tests/test_diagram_integrity.py`. Use hermetic tests for mutators and network-,
container-, or credential-dependent scripts unless the human separately authorizes the live operation.
Before staging a new file, pass it explicitly to pre-commit because Git diff and related-file discovery
do not include untracked paths.

Finish with:

```sh
pre-commit run --all-files --hook-stage pre-commit
pre-commit run --all-files --hook-stage pre-push
git diff --check
```

Report every environment-dependent check that could not run. A warning, skip, or missing report is not a
pass unless an Accepted ADR defines that exact nonblocking lifecycle behavior.
