# ADR-0045: Enforce a fail-closed compose policy gate at both blocking stages

- **Status:** Accepted
- **Date:** 2026-08-20
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

[ADR-0044](0044-docker-compose-runtime-with-official-agent-mesh-image.md) fixes what
`deploy/compose.yaml` and its Dockerfiles must look like: every pulled image pinned by tag and index
digest, every published port bound to `127.0.0.1`, secrets as files rather than literals, a
healthcheck on every service, developer mode switched off in the Agent Mesh container, a platform
override on the Event Management Agent alone, and a closed set of profiles. The hooks already in the
commit path check shape, not policy. `check-compose-spec` validates the document against the Compose
schema, `hadolint` lints Dockerfile style, and `yamllint` checks formatting; none of them can tell
`0.0.0.0:8080:8080` from `127.0.0.1:8080:8080`, a floating tag from a digest, or a literal password
from `${SOLACE_BROKER_PASSWORD}`.

[ADR-0011](0011-no-exception-lint-typecheck-and-complexity-budgets.md) and
[ADR-0019](0019-fail-closed-quality-gates.md) settle how such a property is held: as a gate that can
fail, that fails closed when it cannot run, and that continuous integration re-runs unchanged.
[ADR-0025](0025-narrow-ruff-subprocess-waivers.md) confines `subprocess` to four reviewed owners, and
`.pre-commit-config.yaml` records twice that Docker must not enter the commit path.
[ADR-0033](0033-bound-directory-fan-out.md) groups hook scripts by concern. Two pieces already exist to
build on: `scripts/hooks/check-env-template.sh` defines the credential-name pattern the repository
uses to recognise a secret, and the configuration validator of
[ADR-0032](0032-agent-mesh-semantic-configuration-validator.md) already requires every environment
reference to be declared in `.env.example`.

## Decision

**A pure compose policy gate, `tools/compose_policy_gate.py`, runs at both blocking stages and in
continuous integration.** It parses every compose file and Dockerfile under `deploy/` with PyYAML and a
small instruction parser, reads the names declared in `.env.example`, and prints sorted, unique
diagnostics prefixed `COMPOSE:`. Any finding is exit status 1, and so is being given no compose file:
the gate cannot admit an empty stack.

The rules, each a small function:

- **Images.** Every `image` is `name:tag@sha256:<64 hex digits>`; `latest` is refused even with a
  digest; a service declares `image` or `build`; a `build` must name a Dockerfile that is under review
  and every reviewed Dockerfile must be built by a service.
- **Ports.** Every published port is `127.0.0.1:<host>:<container>` with single integer ports, or the
  long form with `host_ip: 127.0.0.1`; `network_mode: host` is refused.
- **Secrets.** An environment key matching the credential-name pattern — byte-identical to the shell
  hook's, which a test asserts — holds `${NAME}` indirection or a path under `/run/secrets/`, never a
  literal; no URL carries userinfo; every `${NAME}` in a compose file or a substituted Dockerfile
  instruction is declared in `.env.example`; every top-level secret is a file under `./secrets/` or an
  environment source.
- **Shape.** Every service has `healthcheck.test`; `platform` appears only on the Event Management
  Agent and only as `linux/amd64`; profiles come from the closed set `mesh`, `services`,
  `event-portal`; `extends`, inline Dockerfiles, and top-level `include` are refused.
- **Roles.** The `broker` service declares `shm_size`, soft and hard `nofile` limits, and
  `tls_servercertificate_filepath`, publishes container port 55443, and publishes neither 55555 nor
  8080; the `agent-mesh` service sets `SOLACE_DEV_MODE` to `false` explicitly, because the image's
  default is `True`, and sets `SESSION_SECRET_KEY` by indirection, because the image's default is a
  placeholder; both services must exist.
- **Dockerfiles.** Every `FROM` is digest-pinned or names an earlier stage, carries no `--platform`,
  and is not `latest`; every `pip install` passes `--require-hashes`.

The hook script `scripts/hooks/deploy/check-compose-policy.sh` enumerates `deploy/` through
`git ls-files --cached --others --exclude-standard`, the same tracked-or-unignored scope the AAA checker
and the fan-out gate use, and selects the basenames `check-compose-spec` and hadolint would lint. It is
inert while that listing holds no compose file or Dockerfile; from the first one it fails closed on a
missing `.env.example`, `pyproject.toml`, `uv.lock`, `uv`, or gate module, and it never runs Docker.
It is registered as `compose-policy` at `pre-commit` and `pre-push`, always run, with no filename
arguments. `pyyaml` and its type stubs join the root development group so the gate type-checks under
strict mypy with no override. The gate's own tests cover every rule in both directions, the script's
activation and fail-closed contract, and the committed stack itself, so a stack that drifts from the
policy fails the suite as well as the hook.

## Consequences

- The policy in ADR-0044 is executable rather than reviewed, and the committed stack is proven
  conformant on every commit and every push.
- The profile set and the platform allowlist have one home — the gate's constants — and a test pins
  each, so changing either is a visible change.
- **Another gate sits in the commit path.** It parses three small files and costs milliseconds, but it
  is one more thing that can fail closed on a missing tool.
- PyYAML becomes a declared dependency of the repository tooling; it was already locked transitively.
- The rules encode ADR-0044's specifics. A compose change that is legitimately outside them needs the
  gate changed alongside, with a record if the policy itself moves.
- The gate proves that text conforms, not that a stack runs: it cannot know whether a healthcheck
  command exists in an image or whether a digest resolves. The first live run is separate evidence.
- The interpolation scan is conservative by design: a nested default such as `${A:-${B}}` is checked
  for `A` only, and the docstring says so.

## Alternatives considered

- **Rely on `check-compose-spec`, `hadolint`, and `yamllint`.** Rejected: they check shape and style,
  and every property this record cares about is invisible to them.
- **Run `docker compose config` at commit time and inspect the result.** Rejected: it puts Docker in
  the commit path, which the hook configuration forbids, and the verdict would depend on the local
  daemon's version and state.
- **Review.** Rejected for the reason ADR-0011 gives: a property that is only reviewed is a property
  that is not enforced.
- **A reviewed TOML registry for the allowlists, like the dependency waivers.** Rejected: the platform
  allowlist and the profile set are policy, not per-case reviewed exceptions; constants with pinning
  tests are smaller and equally auditable.
- **Place the script in the `repo/` hook group.** Rejected: that group holds gates with no component
  to be inert for, and this one has one.
