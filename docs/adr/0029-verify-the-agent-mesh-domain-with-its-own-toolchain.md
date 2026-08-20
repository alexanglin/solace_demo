# ADR-0029: Verify the Agent Mesh domain with its own toolchain, at its own stage

- **Status:** Accepted
- **Date:** 2026-08-19
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

[ADR-0004](0004-split-python-runtimes.md) put Agent Mesh and its plugins in a separate `uv` project on Python 3.13.15, isolated from the 3.14.7 application workspace. [ADR-0012](0012-git-hooks-with-ci-as-authority.md) makes staged hooks the fast feedback and CI the authority. Phase 0 creates that second domain for the first time, and creating it exposed three gaps between what the hooks claim to verify and what they actually verify.

**Nothing executes the Agent Mesh project's tests.** The domain is linted by `python-quality-full.sh`, type-checked by `mypy-full.sh` and `mypy-agent-mesh`, scanned by `bandit-full.sh`, audited by `dependency-audit.sh`, and lock-verified by `check-locks.sh`. No hook runs `pytest` against it at any stage. Root `pytest` cannot: `[tool.pytest.ini_options] testpaths` deliberately excludes `agent-mesh/` so the 3.14 interpreter never collects a 3.13 tree, and `pytest-related.sh` carries an explicit `exclude: ^agent-mesh/`. A compatibility probe written under `agent-mesh/tests/` would therefore be committed, reviewed, and never run.

**The two type-checking stages disagree.** `mypy` reads configuration from the current working directory and never searches parent directories. The pre-commit `mypy-agent-mesh` hook runs `uv run --project agent-mesh --frozen mypy` from the repository root, so it reads the root `[tool.mypy]`, which declares `python_version = "3.14"`. `mypy-full.sh` enters `agent-mesh` first, so at pre-push it reads whatever that directory declares. The root table also lists `^agent-mesh/` in `exclude`, but mypy applies `exclude` only when crawling a directory; explicitly named files bypass it, and the pre-commit hook passes filenames. The same tree is therefore checked twice under two configurations, one of them naming the wrong interpreter version.

**Ruff would lint the domain as 3.14.** Ruff resolves configuration hierarchically from the nearest ancestor declaring `[tool.ruff]`. With none in `agent-mesh/`, its files inherit `target-version = "py314"`, and the `UP` rules could rewrite code into syntax the 3.13 interpreter cannot parse.

A fourth constraint shapes the fix. `uv run --project agent-mesh --frozen pytest` does **not** change the working directory. Invoked from the repository root it would read the root pytest configuration and collect `tests`, `tools`, `packages`, and `services` under the 3.13 interpreter — the mirror image of the hazard the root `testpaths` comment already records.

## Decision

**The Agent Mesh domain is verified by its own toolchain configuration, executed from its own directory, at its own stage.**

`agent-mesh/pyproject.toml` declares `[tool.ruff]` (extending the root and overriding `target-version` to `py313`), `[tool.mypy]` (`python_version = "3.13"`, `strict`), and `[tool.pytest.ini_options]` (`testpaths = ["tests"]`). Its dev group repeats the root's exact `pytest`, `mypy`, `ruff`, and `pip-audit` versions, because the runtime-dependent hooks invoke those tools through this project and two domains running different tool versions would disagree about the same source.

Add `scripts/hooks/agent-mesh-test-full.sh`, wired at `pre-push` beside `pytest-full`, following the fail-closed contract of the existing `*-full.sh` scripts: inert while the domain is inactive, and a hard failure — never a skip — once the manifest exists and the lockfile or `uv` is missing. It runs pytest from inside `agent-mesh` in a subshell rather than through `--project`, so the working directory selects the configuration.

Give the pre-commit `mypy-agent-mesh` hook an explicit `--config-file agent-mesh/pyproject.toml`, so both stages check the domain under identical settings regardless of where they are invoked from.

Extend the quality-gate self-tests that enumerate hooks and scripts by hand — the activation, inert-when-absent, and required-hook-id lists — to include the new stage, so the new gate is itself gated.

## Consequences

- A probe under `agent-mesh/tests/` becomes executable evidence rather than a committed artifact nobody runs. Phase 0's plugin-compatibility question cannot be answered without this.
- Both type-checking stages now agree, and the disagreement cannot silently return: the configuration file is named explicitly rather than inferred from a working directory.
- **Pre-push gets slower, and continuous integration gets slower twice over.** The domain resolves to roughly 250 packages once the runtime is pinned, and CI installs it on every pull request in both the commit-stage and push-stage jobs. The `CONTRIBUTING.md` stage budgets should be re-measured rather than assumed to still hold.
- The `cd` subshell and the `--project` invocation now coexist in the codebase for what looks like the same purpose, and the difference is load-bearing but not obvious. It is recorded in the script itself, because a future contributor "simplifying" it to `--project` would silently collect the wrong tree under the wrong interpreter.
- Four hand-maintained lists in `tools/quality_gate_tests/` gain an entry. None of them enumerate `scripts/hooks/` automatically, so a fifth domain would need the same edits again. That duplication is now visible, and worth replacing with discovery if a third domain ever appears.
- Tool versions are pinned in two manifests. They can drift, and only review catches it; no gate compares them today.

## Alternatives considered

- **Run the Agent Mesh tests from the root project with `--project agent-mesh`.** Rejected on mechanism: `uv run --project` does not change the working directory, so pytest would load the root configuration and collect the application tree under the 3.13 interpreter. This is the same failure the root `testpaths` setting exists to prevent, in the opposite direction.
- **Add `agent-mesh/tests` to the root `testpaths`.** Rejected: it would execute 3.13 code on the 3.14 interpreter, which contradicts [ADR-0004](0004-split-python-runtimes.md) and would fail on the upstream package's own interpreter constraint.
- **Leave the domain untested and rely on lint, type checking, and the dependency audit.** Rejected: none of those imports the upstream package, and the Phase 0 question is precisely whether three independently released wheels load together. Static analysis cannot answer it.
- **A single `[tool.mypy]` in the root covering both trees.** Rejected: one table cannot declare two `python_version` values, and the root's exclusion of `agent-mesh/` is what keeps `mypy-full.sh` from checking the tree twice under the wrong interpreter.
- **Extend the existing `pytest-full` hook with an agent-mesh branch.** Rejected: `pytest-full.sh` also computes per-member coverage arguments and runs `tools.coverage_gate`, which are workspace concepts the non-member Agent Mesh project does not have. Overloading it would couple two unrelated verdicts into one exit status.
