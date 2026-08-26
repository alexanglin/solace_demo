# ADR-0087: Put the migration tree inside the member that owns the schema, and cover its revisions offline

- **Status:** Accepted
- **Date:** 2026-08-23
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

[ADR-0003](0003-postgres-durable-mission-store.md) selects Alembic. `packages/store/AGENTS.md`
requires the question to be settled before anything is written: "resolve migration location, local
guidance, scaffold activation, coverage ownership, and runtime-image inclusion through the required
repository-layout and verification decision **before the first revision**; test the gate change
itself." It also records that "the repository-root `migrations/` path shown in the implementation-plan
blueprint does not exist", and that "this guide would not govern it" if it did.

Four measured facts about this repository's gates decide the location.

- `tools/coverage_gate.py` attributes a file to a member by a single path prefix:
  `prefixes = ("tools/",) if member == "." else (f"{member}/src/",)`. Only `<member>/src/` counts.
- `scripts/hooks/python/pytest-full.sh` builds `--cov=` from `tools` plus each member's `src`
  directory, so a path outside `src/` is not merely unattributed, it is uninstrumented.
- `tools/member_scaffold.py` walks `src/` alone when deciding whether a member is a scaffold.
- `deploy/application/Dockerfile` copies `pyproject.toml`, `uv.lock`, `packages`, `services`, and
  `tools`. There is no `COPY migrations`. `packages/store/pyproject.toml` additionally builds its wheel
  from `packages = ["src/aerial_rescue_store"]`.

[ADR-0017](0017-mutation-tool-score-and-risk-tiers.md) already places the durable store **and its
migrations** in Tier 2, "measured independently per uv workspace member" -- so a location that no
member owns would make an accepted decision unenforceable by construction.

One more fact, established by running it rather than reading about it: **Alembic's offline mode
executes a revision's `upgrade` and `downgrade` bodies with no database at all.** Driving
`command.upgrade(config, "head", sql=True)` against a one-revision tree emits the data-definition
statements the revision would issue, and `command.downgrade(config, "0001:base", sql=True)` emits its
reverse. That measurement is what makes the coverage question answerable without an escape hatch.

## Decision

**The Alembic tree lives at `packages/store/src/aerial_rescue_store/migrations/`**, with `versions/`
beneath it and its configuration resolved from package resources. The repository-root `migrations/`
directory in the implementation-plan blueprint is deleted from the blueprint rather than created.

Inside `src/` means the revisions are instrumented and attributed to this member at Tier 2, they ship
in the wheel as well as the copied source tree, and the member's own guide governs them. It also means
the member activates honestly at the first revision, because `env.py` is a module with a body.

**The coverage those revisions owe is paid offline, and no `omit` is added.** A member-local test drives
Alembic's offline mode and asserts the emitted data definition, which executes both function bodies
without a connection. That matters beyond the number:
[ADR-0086](0086-prove-the-store-on-a-database-the-run-creates-and-drops.md) puts every live probe
outside the blocking suite, so an `omit` would have been the only other way to keep the gate green --
and [ADR-0011](0011-no-exception-lint-typecheck-and-complexity-budgets.md) leaves this tree with no
escape hatches, a property worth more than the convenience of dropping a directory from measurement.
A live probe still runs `upgrade head` and `downgrade base` against a real cluster, because the offline
form proves the statements are emitted and not that PostgreSQL accepts them.

**`env.py` is hand-written, not the generated template.** The stock file does not survive this
repository's type checking: `fileConfig(config.config_file_name)` passes `str | None` into a `str`
parameter, and ADR-0011 forbids the inline suppression that would silence it. There is no
`# type: ignore` anywhere in this tree and this is not the place to add the first one.

**`script.py.mako` is customised** so a generated revision is already conformant: a docstring on the
module and on both functions, `-> None` annotations, and no commented-out operation calls. The
`file_template` produces a name that is a valid Python identifier and passes the `N999` module-name
rule, which the default hexadecimal-prefixed form does not when a revision hash begins with a digit.

**`versions/` is sharded by release series before it needs to be**, because
[ADR-0033](0033-bound-directory-fan-out.md) caps a directory at twenty immediate children and grants an
exemption only where the fan-out cannot be removed. A `versions/` directory can always be decomposed --
Alembic supports several `version_locations` and recursive discovery -- so no exemption would be
granted, and the twenty-first revision must not be the commit that discovers this.

Three shell gates enumerate a repository-root `migrations` directory that has never existed:
`scripts/hooks/quality-components.sh`, `scripts/hooks/python/cognitive-complexity-full.sh`, and
`scripts/hooks/repo/duplication-full.sh`. They are corrected in the same change, so no gate implies a
location this record rejects. The `packages` root each already enumerates covers the chosen path.

## Consequences

- Every revision is measured at Tier 2 like any other module in the member, which is what ADR-0017
  already said and what no other location delivers.
- **A revision now has to be executable offline to be coverable.** A revision doing something Alembic
  cannot render as data-definition statements -- a data backfill driven by a query, say -- earns no
  coverage from the offline test and needs its own live evidence. That is a real constraint on how
  migrations may be written, and it is the price of not adding an `omit`.
- Writing `env.py` by hand means Alembic's own template improvements do not arrive for free, and an
  upgrade that changes the expected `env.py` contract has to be applied deliberately.
- Sharding `versions/` before it is necessary adds a directory level nobody needs yet, and a
  contributor generating a revision has to place it in the current series rather than accepting the
  default path.
- The wheel now carries a template file and a tree of revisions. Hatchling includes them because they
  sit under the package directory, and the image gets them through `COPY packages` as well, so the two
  paths agree.
- The implementation plan's repository blueprint loses a line it has carried since it was written.

## Alternatives considered

- **A repository-root `migrations/`, as the blueprint sketches.** Rejected on four measured grounds:
  unattributed by `coverage_gate.py`, uninstrumented by `pytest-full.sh`, invisible to the scaffold
  predicate so a schema could land while the member still reported `SCAFFOLD`, and not copied by the
  application image. The member guide adds a fifth: no local guidance would govern it.
- **`packages/store/migrations/`, a sibling of `src/`.** Rejected. It gains the guide's governance and
  none of the measurement -- outside the coverage prefix, outside the scaffold predicate -- and it is
  excluded from the wheel by this member's hatchling target, so an installed package could not find its
  own schema history.
- **Inside `src/`, with the revisions added to the coverage `omit` list.** Rejected once the offline
  measurement showed it was unnecessary. An `omit` would also have been the first coverage escape hatch
  in a tree that has none, and it would have silently exempted every future revision rather than the
  ones that genuinely cannot be rendered offline.
- **Alembic's generated `env.py` and revision template verbatim.** Rejected: neither survives strict
  type checking, the docstring rules, or the annotation rules, and the only way to keep them would be
  the suppression ADR-0011 forbids.
- **`metadata.create_all` instead of migrations.** Rejected outright; `packages/store/AGENTS.md` says
  never to let ORM metadata create production tables implicitly, and it would leave no upgrade path.
- **Autogenerate as the authority.** Rejected as a matter of degree rather than kind: autogenerate may
  draft a revision, but the committed file is what runs, and it is reviewed and edited like any source.
- **A flat `versions/` with an ADR-0033 exemption once it fills.** Rejected: the exemption rule is for
  fan-out that cannot be removed, and this can, so the request would be refused on its own terms.
- **Data-definition files under `deploy/` applied by a container entrypoint.** Rejected: ADR-0003 names
  Alembic, `deploy/` owns container lifecycle rather than application schema, and it would put schema
  authority in the one directory `packages/store/AGENTS.md` says must not hold it.
