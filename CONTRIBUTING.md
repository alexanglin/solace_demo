# Contributing

This repository is a reference implementation, so the process is part of the artifact. The executable
rules below are enforced mechanically; review remains responsible for semantic properties a static gate
cannot prove.

Read [`AGENTS.md`](AGENTS.md) for the full engineering rules and [`docs/adr/`](docs/adr/) for the decisions behind them.

## Setup

```sh
pre-commit install --install-hooks
```

That installs hooks for six git stages: `pre-commit`, `commit-msg`, `pre-push`, `post-checkout`, `post-merge`, and `pre-merge-commit`. A bare `pre-commit install` would only wire up `pre-commit` and your commit-message and push checks would silently never run.

Prerequisites: `pre-commit` 4.5, `uv` 0.12.5, Python 3.14.7, Graphviz
(`brew install graphviz`), `shellcheck`, and `trivy` 0.74.0 (`brew install trivy`), which the pre-push
misconfiguration audit of `deploy/` fails closed without. Running the stack in `deploy/` additionally
needs Docker Desktop with Compose v2 and `openssl`; no hook needs Docker. Agent Mesh work additionally requires Python 3.13.15. When
`apps/dashboard/package.json` exists, install Node 24.19.0 and use Corepack to activate the
`packageManager`-pinned `pnpm`; the dashboard hooks are `language: system`, so the system Node and pnpm do
matter. Only isolated third-party Node hooks are provisioned by pre-commit itself.

## Branching

`main` is protected in two places. The `no-commit-to-branch` hook stops a commit on `main` locally,
and GitHub refuses a direct push to `main`, a force push, and a deletion
([ADR-0054](docs/adr/0054-enforce-the-verification-authority-with-branch-protection.md)). Work on a
short-lived branch and open a pull request:

```sh
git switch -c feat/sector-assignment
git push -u origin feat/sector-assignment
gh pr create --fill
```

This mirrors the [`AGENTS.md` version-control rules](AGENTS.md#9-version-control). If you try to commit on
`main`, the hook stops you — that is not a misconfiguration.

Three checks must pass before the merge button unlocks: **commit-stage hooks**, **pre-push hooks**, and
**no credentials in CI**, all from `checks.yml`. They are the same hook configuration you ran locally,
re-run on a Linux runner, which is why they occasionally catch what a workstation cannot. No approval is
required while the project has one maintainer, history stays linear, and the protection applies to
administrators too — so there is no flag that lands unverified work on `main`.

## Commit messages

[Conventional Commits](https://www.conventionalcommits.org/), enforced at `commit-msg`:

```text
feat(broker): add guaranteed-delivery consumer with explicit ack
fix(approval): reject an approval whose proposal digest no longer matches
docs(adr): record Postgres as the durable mission store
```

Permitted types: `feat`, `fix`, `docs`, `chore`, `test`, `refactor`, `perf`, `build`, `ci`, `revert`. This is not decoration — it is what lets `CHANGELOG.md` be generated rather than hand-maintained, as the [`AGENTS.md` version-control rules](AGENTS.md#9-version-control) require.

## What runs, and when

| Stage | What | Budget |
| --- | --- | --- |
| `pre-commit` | AAA conformance, format, lint, type check, contract artifacts, compose policy over `deploy/`, Agent Mesh configuration semantics once a file exists under `agent-mesh/configs/`, hygiene, secret scan, workflow audit, related tests | **≤ 60 s** |
| `commit-msg` | Conventional Commits | instant |
| `pre-push` | Full-tree AAA conformance, Python format/lint/type/test/coverage, Agent Mesh compatibility suite and configuration semantics on its own 3.13 interpreter, cognitive complexity, multi-language duplication, Tier 1 mutation, domain layering, Bandit, locked-dependency audit, `deploy/` misconfiguration audit, dashboard test/build, pushed-range commit/whitespace validation, full-history secret scan | minutes |
| `post-checkout`, `post-merge` | Resync dependencies if a lockfile changed | seconds |

Initial baseline `pre-commit` measurement on the reference MacBook, taken with a documentation-only tree:
**~2.7 s**. Re-measure when the suite grows; if it approaches the budget, move work to `pre-push` rather
than letting people start using `--no-verify`.

Run them yourself at any time:

```sh
just check-commit      # the fast tier
just check-push        # the thorough tier
just check             # both, exactly as CI runs them
just check-aaa         # the mandatory whole-tree AAA gate and its self-tests
just check-contracts   # schema inventory and positive/negative golden fixtures
just check-complexity  # Ruff, cognitive complexity, and multi-language duplication
just check-mutation    # independent Tier 1 mutation runs and per-module scoring
just check-compose     # the deploy/ stack against the compose policy gate
just check-deploy-config  # trivy config over deploy/, adjudicated under the waiver registry
just scan-images       # build the derived images, then trivy image over all seven (needs Docker)
```

`just` is a convenience wrapper. The hooks and CI invoke the scripts under [`scripts/`](scripts/) directly, so nothing breaks if you do not have `just` installed.

Continuous integration runs two workflows. `checks.yml` re-runs the hook stages on every push and pull
request. `security.yml` runs the locked-dependency audit, the `deploy/` misconfiguration audit, a Trivy
scan of every pulled and built stack image, and CodeQL for Python — daily at 06:17 UTC, on dispatch, on
every push to `main`, and on a pull request that touches the audited inputs
([ADR-0048](docs/adr/0048-scan-images-and-deploy-configuration-with-trivy.md),
[ADR-0050](docs/adr/0050-scan-python-with-codeql-in-continuous-integration-only.md),
[ADR-0051](docs/adr/0051-rescan-daily-and-let-dependabot-raise-pinned-updates.md)). Dependabot raises
daily pinned-update pull requests for both uv locks, the workflows, the two Dockerfiles, and the compose
file, each held back by a seven-day cooldown
([ADR-0052](docs/adr/0052-hold-dependabot-to-a-seven-day-cooldown.md)); every one of them runs both
hook stages like any other pull request.

## Test structure

Every project-owned executable test must pass the mandatory Arrange-Act-Assert gate defined in
[`docs/TESTING.md`](docs/TESTING.md#mandatory-arrange-act-assert-structure) and decided by
[ADR-0018](docs/adr/0018-enforced-arrange-act-assert.md). The gate scans the complete owned source tree at
both `pre-commit` and `pre-push`, and CI runs the same hook. There is no suppression or per-test waiver;
extend the checker and its conformance suite before introducing another test language or registration
dialect.

## Hooks never rewrite tracked source

Every hook is **check-only with respect to tracked source and tests**. A hook tells you what is wrong; it
does not rewrite what you staged. The mutation hook creates ignored `mutants/` result caches inside Tier 1
members; those caches are execution evidence, never staged content. The commit therefore contains exactly
what you reviewed.

To apply fixes, ask for them explicitly:

```sh
just fix
```

Then review the result before staging it.

## Bypassing

`git commit --no-verify` exists for genuine emergencies. It skips the local hooks and nothing else — CI re-runs the identical configuration via `pre-commit run --all-files --hook-stage <stage>`, so unverified work cannot merge. Hooks are fast feedback; CI is the authority.

## Diagrams

Architecture diagrams are Graphviz sources under [`docs/architecture/`](docs/architecture/), committed
alongside their generated PNGs as the [`AGENTS.md` documentation rules](AGENTS.md#7-documentation-and-diagrams)
require.

```sh
just diagrams
```

This recursively regenerates every PNG and refreshes the `.dot.sha256` sidecar next to each source. The
sidecar records both source and PNG hashes; the gate also validates the PNG signature. Content hashes are
used rather than modification time because Git does not preserve mtimes. If the source or PNG changes
without one deliberate regeneration, the hook stops you ([ADR-0022](docs/adr/0022-recursive-diagram-integrity.md)).

## Two Python environments

Per [ADR-0004](docs/adr/0004-split-python-runtimes.md), Python lives in two isolated environments:

- Application services on **3.14.x** in the root `uv` workspace
- Agent Mesh and its plugins on **3.13.x** in `agent-mesh/`, because upstream declares `>=3.10.16,<3.14`

Runtime-dependent hooks route files to the correct environment by path. A change under `agent-mesh/` is
executed and type-checked by the 3.13 environment; application code uses 3.14. Repository-level static
parsers may inspect both trees from the root tool environment because they do not import or execute the
Agent Mesh project.

Never combine the two lockfiles or install either globally.

To run the Agent Mesh semantic-configuration gate by hand, enter the Agent Mesh project so that
`uv` resolves the frozen Python 3.13 lock and the validator imports the pinned wheels. With no
arguments it discovers every `*.yaml` and `*.yml` under `configs/` in sorted order; arguments must
be paths inside that directory:

```sh
cd agent-mesh
uv run --frozen python -m tools.agent_mesh_config_validator [CONFIG ...]
```

Every selected file must be valid on its own. When more than one file is selected the gate also
merges them with the pinned Solace AI Connector merge primitive and fails on a conflict the merge
exposes, such as two apps with one name; it is not a partial-overlay linter. The exit status is 0
when every file is valid or no file exists, 1 on any finding, and 2 on a path outside `configs/`.
A finding names the file, the location, and the rule, and never prints the offending value.

## Local stack

Every component except Ollama runs under Docker Compose from `deploy/compose.yaml`
([ADR-0044](docs/adr/0044-docker-compose-runtime-with-official-agent-mesh-image.md)):

```sh
cp .env.example .env   # then set SESSION_SECRET_KEY; the broker credentials follow provisioning
just secrets           # per-checkout certificate authority, broker certificate, and passwords
just up                # broker and Postgres, waiting for both to be healthy
just ps                # service health
just logs              # follow the logs
just down              # stop; volumes are kept
```

`just up --profile mesh`, `--profile services`, and `--profile event-portal` add the other services;
the first is inert until `agent-mesh/configs/` holds a configuration, the second until the services gain
entrypoints. Broker Manager is `https://localhost:1943`, and the browser warns until `deploy/certs/ca.pem`
is trusted. `just showcase` runs the same stack against the Solace Cloud service through an ignored
`.env.showcase` ([ADR-0043](docs/adr/0043-docker-broker-with-solace-cloud-showcase.md)). **Nobody has
started this stack yet**: the first live run is the next increment, and until it is recorded under
`release-evidence/` the healthcheck commands and the image-internal details are design, not evidence.

## Fail-closed gates

Per [ADR-0019](docs/adr/0019-fail-closed-quality-gates.md), a component that has neither a manifest nor
owned source is inactive. Once either exists, a missing manifest, lockfile, executable, configured test
script, or generated report is an error. `SKIP` is not a successful result for an active blocking gate.
The post-checkout and post-merge dependency synchronizer is the sole exception: it warns instead of
blocking the Git operation, and the next commit or push gate remains authoritative.

The Agent Mesh semantic-configuration gate follows the same contract: it is inert while
`agent-mesh/configs/` holds no YAML file, and from the first file it fails on a missing
`agent-mesh/pyproject.toml`, `agent-mesh/uv.lock`, `uv`, or validator module before it reads any
configuration. A green result is offline evidence only; it does not attest PubSub+, Ollama, A2A, or
plugin behaviour.

The coverage and mutation gates distinguish a scaffold from an unmeasured member. A workspace member
whose `src/` holds only docstring-only modules and `py.typed` markers, and which has no `tests/`
directory, is reported as `SCAFFOLD` and neither measured nor counted as a failure; the first
statement or test file makes it active and every threshold applies
([ADR-0053](docs/adr/0053-report-scaffolded-workspace-members-instead-of-failing-them.md)).

The compose policy gate follows the same contract: it is inert while `deploy/` holds no compose file
or Dockerfile in the tracked-or-unignored listing, and from the first one it fails on a missing
`.env.example`, `pyproject.toml`, `uv.lock`, `uv`, or gate module before it reads anything. It parses
files and never runs Docker, so a green result proves the stack's text conforms to
[ADR-0044](docs/adr/0044-docker-compose-runtime-with-official-agent-mesh-image.md), not that it runs
([ADR-0045](docs/adr/0045-fail-closed-compose-policy-gate.md)).

The `deploy/` misconfiguration audit follows the same contract and the same arming rule as the compose
policy gate: inert until a compose file or Dockerfile is listed, then failing on a missing `trivy`,
`pyproject.toml`, `uv.lock`, `uv`, or gate module before it scans anything. Trivy's own exit code is
ignored in favour of its JSON report, which `tools/dependency_waiver_gate.py --source trivy`
adjudicates: a HIGH or CRITICAL check in `FAIL` status blocks unless `dependency-waivers.toml` carries
an unexpired waiver in the `deploy-config` domain, and every other finding prints as an `INFO:` line.
The image scans apply the same rule per image, in the `image:<repository>` domain, and additionally
require a fixed version before a finding blocks
([ADR-0048](docs/adr/0048-scan-images-and-deploy-configuration-with-trivy.md)).

## Suppressions

Per [ADR-0011](docs/adr/0011-no-exception-lint-typecheck-and-complexity-budgets.md), blanket `# type: ignore`, bare `# noqa`, and `eslint-disable` are prohibited. If you genuinely need one, record it as an ADR with the justification and the condition under which it can be removed. Unused ignores are themselves errors, so a waiver cannot silently outlive its cause.

[ADR-0025](docs/adr/0025-narrow-ruff-subprocess-waivers.md) records the existing Ruff convention choices
for `D203`, `D213`, and `PT009`, plus the exact four-file `S603` subprocess allowlist. `S603` is not
ignored globally or through test globs, `S607` has no waiver, and required Git commands resolve to an
absolute executable before launch or fail closed.

## Updating pinned hooks

Every hook revision is pinned to an exact upstream tag.

```sh
just update-hooks   # pre-commit autoupdate
```

Review every change. Renovate's `pre-commit` manager can raise one reviewable PR per hook, and is the recommended automation — note that it is disabled by default and must be enabled explicitly. Dependabot has no `pre-commit` ecosystem support; it does watch the two uv locks, the workflows, the
Dockerfiles, and the compose file through `.github/dependabot.yml`.

## Known gaps

- The image scans run only in continuous integration. `just scan-images` builds the derived images and
  scans all seven on a workstation that has Docker, but no hook runs it, so a new advisory in a base image is first seen
  by the daily `security.yml` run rather than by a push. A red daily run and a CodeQL alert page
  nobody; reading both is on the review date in [`TECH_DEBT.md`](TECH_DEBT.md).
- `docs-strict` — the check that bans unquantified terms — is blocking at the `pre-commit` stage as of
  2026-08-19. Numbers belong in `docs/operating-parameters.md`; a value not yet known is marked
  `(provisional -- confirm in Phase 0)` rather than left vague. The check skips `docs/adr/` (immutable by
  policy) and this file (it documents the words it bans). Run `just lint-docs-strict` to invoke it
  directly.
- The Agent Mesh semantic-configuration gate specified by
  [ADR-0032](docs/adr/0032-agent-mesh-semantic-configuration-validator.md) is implemented at
  `agent-mesh/tools/agent_mesh_config_validator.py` and is inert until the first file lands under
  `agent-mesh/configs/`. Two of its rules are enforced more narrowly than the record states them:
  `model_provider` is rejected at the top level of an agent or workflow `app_config` rather than at
  any depth, and the versioned-namespace rule is applied to the Event Mesh Tool's publish topic but
  not yet to gateway subscription or output topics. Every local-model identifier fails
  `MODEL_LOCK_REQUIRED` until the lock representation is decided
  ([ADR-0035](docs/adr/0035-refuse-unprovable-agent-mesh-configuration.md)). Live PubSub+ and Ollama
  messaging is the next Phase 0 evidence; a green offline result does not attest it.
- `services/command_gateway` contains no mutation-eligible behavior or co-located tests yet, and neither
  do the Tier 2 members. The mutation and coverage entry points are executable and intentionally fail
  closed until those packages gain tested behavior, so the pre-push tier stays red until they do.
  `packages/contracts` and `packages/domain` pass both; the root tooling member sits below its Tier 2
  threshold because the mutation, contract, coverage, and import gates predate the per-member rule.
- The `deploy/` stack is defined and held to its policy gate but has never been started. The broker
  image's `curl`, the Agent Mesh management-server probe, the Event Management Agent's secret mount,
  and the `openssl` flags under LibreSSL are confirmed by the first live run, not by any hook.
