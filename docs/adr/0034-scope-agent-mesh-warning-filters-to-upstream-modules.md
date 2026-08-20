# ADR-0034: Scope Agent Mesh warning filters to upstream modules

- **Status:** Accepted
- **Date:** 2026-08-20
- **Deciders:** Alex Anglin
- **Supersedes:** [ADR-0030](0030-contain-upstream-warnings-in-the-agent-mesh-domain.md)

## Context

[ADR-0030](0030-contain-upstream-warnings-in-the-agent-mesh-domain.md) accepted four
warning exemptions because the isolated Agent Mesh project contained no owned production
source. The offline semantic configuration gate introduces owned Python under
`agent-mesh/tools/`, so that premise no longer holds. This record is the revisit that
ADR-0030 and `TECH_DEBT.md` section 2 required the moment owned Python landed in this
domain. It arrived under `agent-mesh/tools/` rather than the `agent-mesh/plugins/` path
both named, which is why nothing tripped automatically and why the condition is now stated
as any owned module rather than one directory. In particular, the category-only
`PydanticDeprecatedSince20` exemption could silence the same warning if owned validation
code introduced it.

The measured warnings still originate in the pinned upstream packages. Removing all four
filters would make the compatibility verdict depend on bytecode-cache warmth, optional
host media tools, and known upstream deprecations rather than on repository behavior.

## Decision

Keep `error` as the default warning policy and retain the four measured exemptions, but
add an originating-module expression to every one:

- invalid-escape `SyntaxWarning` and `datetime.utcnow` `DeprecationWarning` only from the
  `solace` package. The `DeprecationWarning` is raised at import time from a dotted module,
  so its expression is `solace\..*`. The `SyntaxWarning` is raised by the compiler, which
  attributes it to the source path with `.py` removed rather than to a module name, so its
  expression matches that path inside the installed package directory:
  `.*[/\\]site-packages[/\\]solace[/\\].*`. Measured on a cold bytecode cache, a dotted
  expression leaves that warning unmatched, `error` turns it into a `SyntaxError` at import,
  and 59 of the 82 Agent Mesh test and subtest results fail; the path expression passes all
  of them, and the `site-packages` anchor keeps it from matching any path in this repository;
- `PydanticDeprecatedSince20` only from `solace_agent_mesh.*`;
- the missing-ffmpeg `RuntimeWarning` only from `pydub.*`.

No warning exemption may have an empty module expression in the Agent Mesh project.
Warnings attributed to owned `tools.*`, future `plugins.*`, or tests therefore continue to
fail collection or execution.

The filters remain compatibility accommodations, not security acceptance. They change
nothing about the dependency audit: the eleven advisories in this domain stay governed by
[ADR-0031](0031-reject-the-google-adk-version-override.md) and the expiring waivers
[ADR-0026](0026-expiring-dependency-waivers.md) requires, and narrowing a warning filter
does not make Phase 0 complete.

## Consequences

- The exact pinned upstream imports remain deterministic across supported hosts.
- Owned validator warnings cannot be hidden by an upstream exemption of the same category
  or message.
- A future upstream module rename will fail closed until the warning source and filter are
  measured again.
- The `SyntaxWarning` expression is bound to the installed path layout rather than to a
  module name. A distribution that relocated its package directory fails closed the same
  way a module rename does.
- The four upstream defects and their removal conditions remain visible; this decision
  narrows their containment rather than resolving them.

## Alternatives considered

- **Retain ADR-0030 unchanged.** Rejected because its no-owned-source premise is false.
- **Disable warnings-as-errors for this project.** Rejected because unrelated warnings
  would stop carrying a blocking signal.
- **Remove the upstream filters immediately.** Rejected because it restores the
  cache- and host-dependent verdict measured by ADR-0030 without changing the pinned
  runtime.
