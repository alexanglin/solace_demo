# ADR-0053: Report scaffolded workspace members instead of failing them

- **Status:** Accepted
- **Date:** 2026-08-20
- **Deciders:** Alex Anglin
- **Supersedes:** two clauses of [ADR-0019](0019-fail-closed-quality-gates.md) — "a member with no
  measurable source fails" in its decision, and the consequence that "the concurrently introduced empty
  package scaffolds cannot satisfy the full coverage gate until real, tested behavior exists. This is
  intentional." Every other part of ADR-0019 stands.

## Context

The workspace declares eleven members. Two, `packages/contracts` and `packages/domain`, hold tested
code and pass the coverage and mutation gates. The other nine are scaffolds: a `pyproject.toml` that
declares a risk tier, one `__init__.py` holding nothing but a docstring, and a `py.typed` marker. They
exist so the workspace resolves, the wheel layout is fixed, and the tier of each future component is
declared before its first line is written ([ADR-0017](0017-mutation-tool-score-and-risk-tiers.md)).

ADR-0019 made every such member fail the coverage gate ("no measurable source; an active member cannot
pass vacuously") and, for the tier-one `services/command_gateway`, the mutation gate's preflight
("co-located tests/ is required"). `TECH_DEBT.md` has carried the result since 2026-08-19 as "gates
that are red by design": the pre-push stage does not pass on `main`. The first attempt to push `main`
with the hooks enabled confirmed it. Thirty of the thirty-two pre-push hooks passed over the whole
ninety-four-commit range; the two that failed, failed only on the nine scaffolds and on the root
tooling member's coverage. A pre-push stage that can never pass on `main` is a stage everybody pushes
around, which is the routine bypass [ADR-0012](0012-git-hooks-with-ci-as-authority.md) forbids and the
false-green condition ADR-0019 was written to end, arriving from the other direction.

The two honest ways out are to write the nine components, which is the remainder of the delivery plan
and not a gate repair, or to make the gates distinguish *nothing to measure yet* from *unmeasured*. The
distinction is exact. A member is measured the moment it contains one executable statement, and a
docstring is not a statement: `coverage.py` reports zero lines for it, `mutmut` generates zero mutants
from it, and no test could exercise it. Reporting such a member as a scaffold hides nothing, because
there is nothing to hide; failing it proves nothing, because there is nothing to prove.

## Decision

**A scaffolded workspace member is reported as `SCAFFOLD` by the coverage gate and skipped by the
mutation gate, and it stops being a scaffold at its first executable statement or test file.**

A member is scaffolded when all of the following hold, judged by `tools/member_scaffold.py`, which
is the single implementation both gates call:

- it has a `pyproject.toml`, so it is a declared member and not an absent component;
- it has no `tests/` directory;
- every file under `src/`, ignoring `__pycache__` directories, is either a `py.typed` marker or a
  Python module whose parsed body is empty or a single string constant — the docstring.

Anything else makes the member active, and the unchanged rules of ADR-0019 apply: a module with one
statement is measured against its tier, a `tests/` directory with no source fails as "no measurable
source", a non-Python file under `src/` fails the same way, and a tier-one member with a function and
no tests fails the mutation preflight. The scaffold state can only be entered by having nothing.

The coverage gate prints `SCAFFOLD <member> tier=<n> manifest and docstring-only package markers,
no tests; not measured` on standard output and does not count it as a failure. The mutation gate's
`--list-tier-one` omits scaffolded tier-one members and names each omission on standard error;
`--evaluate` prints the same `SCAFFOLD` line for them and judges the rest. If every declared tier-one
member were a scaffold, the list would be empty and the pre-push script would fail with `MISSING: no
tier-one workspace member was discovered`, which is the correct verdict for a project whose safety
core does not exist.

## Consequences

- The pre-push stage can be green on `main` while the scaffolds exist, so `--no-verify` stops being
  the way changes reach the remote. `TECH_DEBT.md` section 4 becomes a record of what the root tooling
  member still owes, not a standing exception for the whole stage.
- `SCAFFOLD` is a third outcome beside `PASS` and `FAIL`, visible in every run. A reader of the hook
  output sees which components have not started, which is more information than a uniform red wall.
- ADR-0019's fail-closed property is preserved at the first line of code. The first statement in any
  scaffold turns its line to `FAIL` until that statement is tested to its tier.
- The predicate parses every `src/` module on every run; for nine one-line files the cost is not
  measurable.

## Alternatives considered

- **Write the nine components now.** Rejected: that is the delivery plan, sequenced by
  [IMPLEMENTATION_PLAN.md](../IMPLEMENTATION_PLAN.md), not a gate repair, and writing placeholder
  statements to satisfy a percentage is the fake evidence ADR-0019 exists to prevent.
- **Remove the scaffolds from the workspace until each has code.** Rejected: the declared tier of
  each future component is a decision ADR-0017 wants made before the code exists, and the compose
  stack's `services` profile already names the six services.
- **Leave the stage red and push with `--no-verify`.** Rejected: it is the bypass ADR-0012 forbids,
  and a stage that is always red cannot signal anything.
- **Treat a scaffold as `PASS`.** Rejected: a pass claims evidence, and a scaffold has none. The
  distinct outcome is the point.
