# ADR-0109: Enable the Pydantic mypy plugin with typed constructors

- **Status:** Accepted
- **Date:** 2026-08-24
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

ADR-0108 activates strict Pydantic models in three Python 3.14 services. The root mypy policy enables
`disallow_any_explicit`, while Pydantic's normal dataclass transform synthesizes model constructors that
accept `Any`. Whole-program strict mypy therefore reports an explicit-`Any` error at every owned model
class even though the declared fields themselves are fully typed.

Inline ignores, per-module relaxations, or disabling the repository-wide `Any` rule would weaken owned
code to accommodate framework-generated signatures. Pydantic 2.13.4 ships an official mypy plugin that
can synthesize field-typed constructors and enforce the model configuration selected by ADR-0108.

## Decision

Enable `pydantic.mypy` in the root `[tool.mypy]` configuration. Configure the pinned plugin with:

- `init_typed = true`, so generated model constructors use declared field types rather than `Any`;
- `init_forbid_extra = true`, so constructor signatures agree with the closed-model boundary; and
- `warn_required_dynamic_aliases = true`, so a required field cannot become statically unknowable through
  an unchecked dynamic alias.

Keep every existing strict mypy switch, including `disallow_any_explicit`. Do not add a Pydantic plugin
to the separate Agent Mesh Python 3.13 environment, which owns its own pinned dependency graph and warning
policy. A repository conformance test holds the plugin name and its three strict options so a future
dependency or configuration change cannot silently restore untyped constructors.

## Consequences

- Service model constructors become statically typed without weakening the global `Any` policy.
- Mypy now executes pinned third-party plugin code while checking the root workspace; the lock and plugin
  configuration become one verification unit.
- Dynamic aliases that obscure required fields fail type checking, so models use explicit or predictable
  aliases.
- Agent Mesh remains isolated from the root plugin and continues to test its upstream Pydantic usage under
  its own interpreter and configuration.

## Alternatives considered

- **Disable `disallow_any_explicit`.** Rejected because it weakens every root package for one framework's
  generated method.
- **Add per-file mypy exceptions or inline ignores.** Rejected because the model boundary would be the
  only trust boundary exempted from the strictness it is intended to provide.
- **Write hand-typed constructors on every model.** Rejected because it duplicates fields, creates a
  second alias/default representation, and is exactly the synthesis the official plugin owns.
- **Apply the plugin to Agent Mesh too.** Rejected because that environment is an upstream black-box
  runtime with independent pins and configuration.
