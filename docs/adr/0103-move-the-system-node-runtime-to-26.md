# ADR-0103: Move the system Node runtime to 26.7.0 and keep the provisioned hooks on 24 LTS

- **Status:** Accepted
- **Date:** 2026-08-24
- **Deciders:** Alex Anglin
- **Supersedes:** the Node.js runtime pin of [ADR-0099](0099-pin-the-dashboard-runtime-and-stack.md),
  including its consequence that "a newer system Node does not change the supported version recorded
  here", and the Node version named in [ADR-0019](0019-fail-closed-quality-gates.md)

## Context

[ADR-0099](0099-pin-the-dashboard-runtime-and-stack.md) selected Node.js `24.19.0` — then the active
long-term-support release — and made the fail-closed dashboard wrappers compare the running `node
--version` against `engines.node` exactly. That gate did its job: the pre-push Playwright hook refused
to run on the reference workstation as soon as Homebrew moved its `node` formula forward, rather than
verifying the dashboard on a runtime nobody had chosen.

Two facts decided which way to resolve the refusal. Node's published release schedule ends v25 on
2026-06-01, so the Current line that happened to be installed is already out of support and receives no
further security releases. Nothing in this repository would have said so: pip-audit, Trivy, and
Dependabot observe dependencies and images, not the host runtime. v26 started on 2026-05-05, becomes
long-term support on 2026-10-28, and is maintained until 2029-04-30.

The Node hooks that pre-commit provisions are a separate question. nodeenv installs their runtime
inside the hook environment, so it is hermetic and unrelated to the host, and `.pre-commit-config.yaml`
already records why `markdownlint-cli2` carries its own pin: its dependency tree refuses the Node 25
Current line.

## Decision

The system Node runtime is `26.7.0`. `apps/dashboard/package.json` `engines.node`, both
`actions/setup-node` steps in `.github/workflows/checks.yml`, the `actions/setup-node` step in
`.github/workflows/security.yml`, and the setup sequence in `CONTRIBUTING.md` name that exact version,
and the dashboard wrappers keep comparing the active runtime against the manifest rather than trusting
it.

`@types/node` moves to `26.2.0`, which holds ADR-0099's rule that the Node declarations stay on the
selected runtime major. `26.3.0` was published earlier the same day; the exact pin takes the preceding
release so that a lockfile refresh is not gated on a package a few hours old. pnpm stays at `11.23.0`,
and every other pin in ADR-0099 is unchanged.

The two Node hooks that pre-commit provisions — `markdownlint-cli2` and the `jscpd` duplication gate —
stay at `language_version: "24.19.0"`. Their runtime is hermetic, so it does not have to match the
host, and moving it would mean revalidating third-party tooling for no gain.

## Consequences

- The supported runtime is a Current release for the next two months. Until 2026-10-28 the project runs
  on a line with no long-term-support guarantee, so a v26 defect has to be met by moving forward rather
  than by waiting for a backport.
- Two Node versions are in play at once, and that is now deliberate rather than accidental: `26.7.0`
  for everything this repository owns, `24.19.0` inside two provisioned hook environments. A
  contributor reading `.pre-commit-config.yaml` will see a version that is not the one they installed.
- No gate here observes the host runtime, so this record and the exact pins that follow from it are the
  only thing keeping it supported. Drifting onto an end-of-life line again produces no warning; it
  produces silence.
- A runtime major reaches the lockfile through `@types/node`, so the dashboard type check, unit suite,
  browser acceptance, and production build all have to be re-run before the move can be claimed.

## Alternatives considered

- **Install Node 24.19.0 on the workstation and change nothing.** Rejected: it is the honest reading of
  ADR-0099 and it keeps the active long-term-support line, but it was declined in favour of tracking
  the newest runtime.
- **Pin the installed 25.2.1.** Rejected once its support window was checked: v25 ended on 2026-06-01,
  so the pin would have committed the project to a runtime with no security releases and no gate that
  would ever report it.
- **Accept a range such as `>=26` rather than an exact version.** Rejected by ADR-0099's exact-pin
  rule, which exists so that a lock refresh cannot change the runtime without a decision.
- **Move the provisioned hooks to `26.7.0` as well.** Rejected: the recorded `markdownlint-cli2`
  constraint rules out a Current line, and a hermetic hook environment gains nothing from matching the
  host.
