# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
entries are derived from [Conventional Commits](https://www.conventionalcommits.org/).
See [CONTRIBUTING.md](CONTRIBUTING.md) for the commit convention.

## [Unreleased]

### Added

- The approval time-to-live is 60 seconds.
  [ADR-0042](docs/adr/0042-approval-time-to-live.md) moves to `Accepted` and
  `docs/operating-parameters.md` gains an approval-timing section, closing the last row that
  [ADR-0006](docs/adr/0006-proposal-bound-single-use-approvals.md) had left open. No code changes:
  `packages/domain` still takes the window as an injected parameter with no default, so the
  composition root supplies `timedelta(seconds=60)`. The number is derived from the committed
  service-level targets rather than measured — a 30 s restart recovery, a 10 s backlog drain, and a
  2 s command path give a 42 s worst case with 18 s of margin — so moving it is a superseding record.

- **The broker enforces who may publish what.** Before this change, an identity that did not exist,
  with a password that was never issued, could connect to the container and publish a guaranteed
  message to `aerial-rescue/v1/{missionId}/drone/{droneId}/command/escalate-rescue` — the topic
  [ADR-0005](docs/adr/0005-deterministic-command-gateway.md) reserves to the deterministic command
  gateway. Unknown usernames resolved to the enabled factory `default` client username, whose ACL
  profile permitted every topic in both directions.

  [ADR-0061](docs/adr/0061-least-privilege-broker-principals-and-topic-authorization.md) closes it
  with nine authorization roles rather than one identity per process, so three edge agents get
  distinct usernames for observability and identical authority through one ACL profile.
  `packages/domain/principals.py` carries two tables total over the roles and a separate A2A grant, at
  tier 1: 100% statements and branches, 226 of 226 mutants killed. `command-gateway` is the only role
  that may publish a drone command; `event-mesh-tool` may publish exactly the one family the offline
  configuration validator already holds it to, so that boundary now survives a configuration that
  never met the validator; `recorder` and `discovery` hold no publish grant at all; and the scenario
  service gets no identity, because it has no documented broker role and deny-by-default extends to
  issuing them.

  `packages/broker` projects the tables onto the broker. `subscriptions.py` renders one bounded
  pattern per topic family, using only the single-level `*` and never `>`, and every pattern is put
  to a topic of each of the other ten families — with variable levels filled by values that shadow
  literal levels — and must refuse it. `provisioning.py` converges rather than assuming an empty
  broker, and `semp.py` carries SEMP over a bounded `http.client` connection that redacts every
  secret member and withholds the broker's free-text error whenever the request carried one.

  Proven against the running container: nine ACL profiles at `disallow` for publish, subscribe, and
  share name, nine client usernames each on its own profile, the factory `default` disabled, and 41
  topic exceptions. Catalogue cases B17, B18, and B19 move from `to build` to passing, with the
  command gateway publishing the same topic as the positive control, because a broker refusing
  everybody would satisfy every denial test. The full before-and-after is in
  [`release-evidence/phase-0/broker-authorization.md`](release-evidence/phase-0/broker-authorization.md).

  `scripts/broker-secrets.sh` writes one credential per role and no longer treats its output as one
  all-or-nothing set, so adding a role fills its own gap instead of rotating the certificate authority
  the running broker is presenting. `deploy/compose.yaml` gives each service its own identity;
  `.env.example` declares all nine, usernames as real values because they are role names rather than
  secrets. Four gate tests hold the four homes of the role set equal to the `Principal` enum.

  Not settled: no durable queue exists, so guaranteed delivery has no endpoint; the A2A grant is
  withheld until `NAMESPACE` is fixed; no test asserts that a *subscription* outside a role's grants
  is refused; and the showcase service has not been given the same definitions.

- **The compose stack has been started.** The default profile's first live run is recorded in
  `release-evidence/phase-0/first-live-run.md`: broker and Postgres both reach `healthy` and
  `up --wait` returns 0 in 40.75s including both image pulls, all three published ports are bound to
  `127.0.0.1` on the running containers, and the broker holds 1.543 GiB against the workstation's
  7.652 GiB allocation. `tests/phase0/test_first_live_stack.py` completes a full TLS handshake against
  55443 and 1943 with chain verification against the generated authority and hostname checking left
  on, and reads back all three subject alternative names; all three probes failed with
  `ConnectionRefusedError` before the stack was started. They carry the `docker` and `broker` markers,
  so no blocking suite runs them.
- Two accepted-debt rows cleared with that run. The broker image carries `/usr/bin/curl` 7.76.1, so the
  healthcheck's assumption is measured rather than argued and the documented `/dev/tcp` fallback is
  unnecessary; and `scripts/broker-secrets.sh` produced a working authority under macOS LibreSSL 3.3.6,
  while the nine tests that drive it now pass on the Linux runner against OpenSSL — which they could
  not do before, because the job they run in had never completed.
- The measured resource numbers land in `docs/operating-parameters.md`: the Docker Desktop allocation,
  the default profile's cost at rest, and the observed time to healthy. Full-stack memory and the
  fleet's connection count stay provisional, because the two components that make that figure
  interesting have not run.

- Both `[tool.mypy]` tables enable every strictness lever mypy 1.19.0 offers and the tree already
  satisfies: `disallow_any_explicit`, `strict_equality_for_none`, `local_partial_types`, and all
  thirteen error codes that are off by default, among them `exhaustive-match`, `unused-awaitable`,
  `possibly-undefined`, and `ignore-without-code`. Measured at zero errors on both trees before
  being enabled. `tools/quality_gate_tests/contracts/test_type_check_contract.py` asserts a floor,
  so deleting `strict = true` from both tables fails rather than satisfying a pure drift rule; holds
  the two tables equal outside `python_version`, `exclude`, and the override lists; computes the
  expected error-code list from `mypy.errorcodes`, so a mypy upgrade that adds an optional code
  fails until it is decided on; and makes ADR-0029's interpreter routing executable
  ([ADR-0056](docs/adr/0056-raise-mypy-to-every-lever-the-tree-satisfies.md)).
- The dashboard's TypeScript baseline is fixed before the first dashboard file exists, and
  `tools/typescript_policy_gate.py` refuses a configuration that does not carry it: the compiler
  options `strict` omits, `skipLibCheck: false`, a relative-only `extends`, the six required package
  scripts, `--max-warnings 0`, the four coverage thresholds, and exact dependency versions. New
  pre-push gates `dashboard-typecheck-full` and `dashboard-quality-full` run the whole project and
  the whole tree, the counterparts of `mypy-full` and `python-quality-full`. All of them are inert
  until `apps/dashboard` holds a manifest or TypeScript source, and fail closed afterwards
  ([ADR-0057](docs/adr/0057-typescript-strictness-baseline-before-the-dashboard.md),
  [ADR-0058](docs/adr/0058-validate-dashboard-inputs-against-the-committed-schemas.md)).
- `just check-types` and `just check-dashboard`. Every other deep gate had a recipe; type checking
  did not.

- Trivy 0.74.0 scans the stack under the waiver registry. `tools/dependency_waiver_gate.py` takes
  `--source trivy` and two new domains, `deploy-config` and `image:<repository>`: a HIGH or CRITICAL
  finding with a fixed version (or, for a misconfiguration, in `FAIL` status) blocks unless an expiring
  waiver covers it, and every other finding prints as an `INFO:` line. `trivy config` runs over
  `deploy/` at pre-push through the fail-closed `trivy-config-full` hook, armed by the same rule as the
  compose policy gate; `tools/image_inventory.py` lists every pulled and built image from the compose
  file and the Dockerfiles, and `scripts/security/scan-images.sh` runs `trivy image` over each of
  them in continuous integration. `just check-deploy-config` and `just scan-images` drive both
  ([ADR-0048](docs/adr/0048-scan-images-and-deploy-configuration-with-trivy.md)).
- zizmor 1.29.0 audits `.github/workflows/` and `.github/dependabot.yml` offline at the commit stage,
  and any finding fails ([ADR-0049](docs/adr/0049-audit-workflows-with-zizmor-at-the-commit-stage.md)).
- `.github/workflows/security.yml` runs daily at 06:17 UTC, on dispatch, on every push to `main`, and
  on pull requests touching the audited inputs: the locked-dependency audit and the `deploy/`
  misconfiguration audit, a Trivy scan of all seven stack images after a compose build, and CodeQL for
  Python with build mode `none` — the last never on a pull request, where the token is read-only
  ([ADR-0050](docs/adr/0050-scan-python-with-codeql-in-continuous-integration-only.md),
  [ADR-0051](docs/adr/0051-rescan-daily-and-let-dependabot-raise-pinned-updates.md)).
- `.github/dependabot.yml` watches both uv locks, the workflows, the two Dockerfiles, and the compose
  file daily, at most five open pull requests each, with Conventional Commit prefixes the commit-message
  hook accepts and the seven-day cooldown zizmor's `dependabot-cooldown` audit requires
  ([ADR-0052](docs/adr/0052-hold-dependabot-to-a-seven-day-cooldown.md)).
- Docker is the runtime. `deploy/compose.yaml` defines every component except Ollama: the PubSub+
  Standard 10.26.0 broker container and PostgreSQL 18 in the default profile, Agent Mesh 1.28.7 built
  on its official image with the two Event Mesh wheels installed by hash under the `mesh` profile, the
  six application services under an inert `services` profile until they gain entrypoints, and the
  Event Management Agent under the non-gating `event-portal` profile. Every pulled image is pinned by
  tag and index digest, every published port binds to `127.0.0.1`, the broker's management ports are
  never published, secrets are files under the ignored `deploy/secrets/`, and every service declares a
  healthcheck. Nothing has been started yet; the first live run is the next increment
  ([ADR-0044](docs/adr/0044-docker-compose-runtime-with-official-agent-mesh-image.md)).
- The broker substrate decision the plan had owed since its first revision: the container is the
  broker for development, integration, continuous integration, acceptance, and release, and the
  Developer-class Solace Cloud service becomes a non-gating showcase profile selected by environment
  alone, committed to three console surfaces — Broker Manager and Cluster Manager, Event Portal
  Designer and Catalog, and Event Portal runtime discovery of the local container. The two open
  questions about the trial service and the post-trial substrate are settled ([ADR-0043](docs/adr/0043-docker-broker-with-solace-cloud-showcase.md)).
- A fail-closed compose policy gate, `tools/compose_policy_gate.py`, at both blocking stages and in
  continuous integration. It parses the compose file and the Dockerfiles without running Docker and
  refuses an unpinned or floating image, a port not bound to loopback, a literal secret or URL
  userinfo, an undeclared environment reference, a missing healthcheck, a platform override outside
  the one-entry allowlist, an unknown profile, a broker without its shared memory, file limits,
  certificate path, or TLS port, an Agent Mesh service left at the image's developer-mode or
  session-secret defaults, and a Dockerfile whose `FROM` lacks a digest or whose `pip install` lacks
  `--require-hashes`. The committed stack is proven conformant by a test on every run ([ADR-0045](docs/adr/0045-fail-closed-compose-policy-gate.md)).
- `scripts/broker-secrets.sh`, which generates a per-checkout EC P-256 certificate authority, the
  broker's server certificate with subject alternative names for `localhost`, `broker`, and
  `127.0.0.1`, and the stack's passwords, every private file 0600 and none of it ever tracked, so the
  `tcps` rule applies to the container unchanged. `just secrets`, `just up`, `just down`, `just logs`,
  `just ps`, `just showcase`, and `just check-compose` drive the stack and its gate ([ADR-0046](docs/adr/0046-generated-local-certificate-authority.md)).

- The first tested code in `packages/domain`, the Tier 1 domain core, at 100% statement and branch
  coverage with 202/202 mutants killed and no reviewed survivors. `connectivity.py` counts consecutive
  heartbeat intervals into `CONNECTED`, `DEGRADED`, and `OFFLINE`, with the three counts injected and a
  miss that never improves the state (ADR-0039). `idempotency.py` judges one producer's sequence against
  its own high-water mark and denies a repeated approval consumption instead of replaying it.
  `approvals.py` encodes the ADR-0006 protocol with `EXECUTED` reachable only through a consumption that
  recomputes the proposal digest through the contracts package and reads a wall clock and a monotonic
  clock together (ADR-0040). `authority.py` closes the `commandType` set to `assign-sector` and
  `escalate-rescue` in a deny-by-default table that authorizes an escalation only from a consumed
  approval (ADR-0041). Catalogue cases B08, B09, B10, B12, B15, B16, B23, and B25 have their domain
  halves as named tests. The approval time to live is injected with no default; ADR-0042 proposes
  60 seconds and awaits acceptance.

- A typed builder and parser for the eleven application topic families, `packages/contracts`
  `topics.py`. Every variable level obeys one of four allowlisted rules, so a Solace wildcard, a
  reserved prefix, an empty level, or a separator inside a level is unrepresentable rather than
  defended against; the CloudEvents type is derived from the topic and recovered from it; parsing
  refuses in a fixed order with typed refusals naming the parameter at fault. Agent names admit upper
  case and underscores and refuse hyphens because that is the character class Agent Mesh 1.28.7
  publishes under (ADR-0036).
- The CloudEvents 1.0 envelope profile, `envelope.py`, validated at the trust boundary as a pure
  function: a closed member set with sequence, correlation, causation, and W3C trace context as
  extension attributes, `data` inside the integer canonical profile for every event type, a binding
  table from type to payload schema that fails closed, an egress document form that is the exact
  inverse of parsing, decoding through the canonical decoder so a repeated key is refused, and an
  arriving-topic binding check for the broker adapter (ADR-0037).
- The v1 JSON Schemas, golden fixtures, and `schemas/contract-manifest.toml`, which arm the
  contract-artifact gate for the first time: the canonical profile with every shared definition, the
  envelope, the drone telemetry payload and its composed event, and the topic golden cases. Every
  schema `$id` is a path under the reserved host `https://aerial-rescue.invalid/`, every negative
  fixture fails for exactly one reason, and a root contract suite proves the schema verdict equals the
  Python verdict on every fixture and that the schema patterns are the Python constants (ADR-0038).

- The Agent Mesh semantic-configuration gate ADR-0032 specified: `agent-mesh/tools/agent_mesh_config_validator.py`,
  run by `scripts/hooks/agent-mesh/check-agent-mesh-configs.sh` -- a sixth concern subdirectory
  under ADR-0033, with its gate tests under `tools/quality_gate_tests/hooks/` -- at both blocking
  stages and in CI. It runs on the 3.13 interpreter and delegates include expansion, parsing,
  multi-file merge, and app-configuration models to the exact pinned Solace AI Connector, Agent
  Mesh, and Event Mesh plugin wheels, binding each symbol to its installed distribution record, then
  adds the owned rules: repository-contained includes, environment indirection for every credential
  with every reference declared in `.env.example`, no `model_provider`, no floating model
  identifier, `tcps` or WSS-on-443 broker URLs without userinfo, the gateway settlement and routing
  policy, and an Event Mesh Tool that publishes only to
  `aerial-rescue/v1/{{ missionId }}/gateway/request/{{ operation }}`. It starts no Agent Mesh
  process, broker client, application, or model; it is inert until the first file lands under
  `agent-mesh/configs/` and then fails closed on a missing manifest, lock, `uv`, or parser. Until the
  local-model lock representation is decided, every `ollama` identifier fails `MODEL_LOCK_REQUIRED`
  ([ADR-0035](docs/adr/0035-refuse-unprovable-agent-mesh-configuration.md)). An editable
  validation-flow diagram and its generated PNG document the evidence boundary. A green result is
  configuration evidence only; live PubSub+ and Ollama messaging remains the next Phase 0 evidence.

- The Agent Mesh Bandit, cognitive-complexity, duplication, and source-presence checks now cover
  `agent-mesh/tools/` as well as `agent-mesh/plugins/`, and the Agent Mesh test stage holds the
  validator at 100% statement and branch coverage, so the first owned Python in that domain meets
  every gate the plugins will.

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

- The durable store moves to `postgres:18.6-trixie`, the newest major, and the named volume mounts at
  `/var/lib/postgresql` rather than the 17-era `/var/lib/postgresql/data`. PostgreSQL 18 sets
  `PGDATA=/var/lib/postgresql/18/docker` and declares `/var/lib/postgresql` as its volume, so keeping
  the old mount would have put the running cluster in the container's writable layer — a durable store
  that loses its database on every recreation, which no gate here could have detected because the
  compose file would still have named a volume. Verified live: healthy in 20.77s, `PostgreSQL 18.6 …
  aarch64-unknown-linux-gnu`, `data_directory` at `/var/lib/postgresql/18/docker`, and
  `18/docker/PG_VERSION` present inside the named volume. **An existing version 17 cluster will not
  start under this image**; discard it with `docker compose down` and
  `docker volume rm aerial-rescue-mesh_postgres-data`
  ([ADR-0060](docs/adr/0060-postgresql-18-and-its-data-directory-layout.md)).

- Every job in `.github/workflows/` is bounded by a budget derived from its measured cost: at most 20
  minutes, down from 60 on `pre-push hooks` and `image scan` and 30 on `codeql`. Measured 2026-08-20
  whole-tree — the complete pre-push stage 2m01s, the image scan 2m58s, CodeQL 1m13s, the commit stage
  1m15s — so the slowest job keeps better than four times its cost. The budget is a detection
  threshold: a job that reaches it is wedged rather than slow, and the previous hour meant nobody
  could tell those apart. `test_no_continuous_integration_job_may_outlive_its_measured_cost` holds it
  and `docs/operating-parameters.md` records it
  ([ADR-0059](docs/adr/0059-keep-the-verification-authority-able-to-report.md)).
- `test_every_type_check_hook_is_reached_by_a_continuous_integration_job` asserts the wiring that was
  previously only true: every hook whose own entry runs `mypy` or `tsc` must declare a stage that a
  `checks.yml` job executes. Identifying the hooks by entry rather than by id keeps the rule intact
  through a rename, so retargeting a `--hook-stage` argument or deleting the push-stage job now fails
  a test instead of silently ending whole-tree type checking.

- The image scan reports advisories instead of enforcing them, and a new gate enforces the thing the
  project can act on. The first run found 307 blocking findings across the seven images and none was
  actionable: every pinned digest was already the newest its tag carried, the newest tags were already
  pinned, hadolint `DL3005` forbids the `apt-get upgrade` layer that would patch the two derived
  images, and ADR-0007 forbids patching a vendor image. `tools/image_pin_gate.py`, driven by
  `scripts/security/check-image-pins.sh` and `just check-image-pins`, now fails when a pinned digest is
  no longer the newest its tag carries, so the fix arrives by changing the image rather than by signing
  307 waivers on a 30-day cycle. `deploy-config` misconfigurations and the `pip-audit` zero-tolerance
  rule are unchanged ([ADR-0055](docs/adr/0055-block-on-the-image-pin-not-on-advisories-inside-it.md)).
- `main` is protected on GitHub: a pull request with zero required approvals, the three `checks.yml`
  jobs required, linear history, no force pushes or deletions, and enforcement for administrators
  ([ADR-0054](docs/adr/0054-enforce-the-verification-authority-with-branch-protection.md)).

- The coverage gate reports a scaffolded workspace member as `SCAFFOLD` instead of failing it, and
  the mutation gate lists, preflights, and evaluates only the active tier-one members while naming
  the scaffolded ones. `tools/member_scaffold.py` is the one predicate both gates call: a manifest,
  no `tests/`, and nothing under `src/` but `py.typed` markers and docstring-only modules. The first
  executable statement, test file, or non-Python source file makes the member active again, and a
  scaffold without a declared tier still fails. This supersedes the clause of ADR-0019 that kept the
  pre-push stage red on `main` by design
  ([ADR-0053](docs/adr/0053-report-scaffolded-workspace-members-instead-of-failing-them.md)).
- The pip-audit loader in `tools/dependency_waiver_gate.py` now requires the report's `dependencies`
  array and refuses a Trivy report, just as the Trivy loader refuses a pip-audit one. A report with the
  wrong shape used to read as clean.
- The three checkouts in `checks.yml` no longer persist the workflow token into the working tree;
  the push stage reads local refs only. `compose_policy_gate` exposes its Dockerfile instruction
  parser as `dockerfile_instructions` for the image inventory.
- Solace Cloud moved from the live broker to a showcase profile, and every document that named it as
  the broker now names the container: the plan's decision table gains its missing records, Phase 0's
  next step becomes the stack's first live run, Phase 5 exercises the mission against the container
  before the showcase, the architecture gains a deployment-layout section and a publication column on
  the reserved-port table, the testing document's broker-integration class runs on the container, and
  the operating parameters gain a local-stack table that fills the open image-digest row. ADR-0004's
  rejected container alternative is superseded for the runtime only; its two verification environments
  stand ([ADR-0043](docs/adr/0043-docker-broker-with-solace-cloud-showcase.md), [ADR-0044](docs/adr/0044-docker-compose-runtime-with-official-agent-mesh-image.md)).
- `.env.example` is rewritten from the container's point of view with only names the pinned runtime
  and the official images read, plus a commented showcase block for the ignored `.env.showcase`.
- `pyyaml` 6.0.3 and its type stubs join the root development group so the compose policy gate can
  parse YAML under strict mypy with no override.

- Superseded ADR-0030's category- and message-scoped Agent Mesh warning exemptions with ADR-0034's
  source-scoped ones, because the validator is the first owned Python in that domain and ADR-0030
  rested on there being none. The four exemptions cover what they did and now name where the warning
  may come from: `solace_agent_mesh.*` for `PydanticDeprecatedSince20`, `solace.*` for
  `datetime.utcnow`, `pydub.*` for the missing-ffmpeg warning, and a path expression inside the
  installed `solace` package directory for the invalid-escape `SyntaxWarning`, which the compiler
  attributes to a source path rather than to a module. Measured on a cold bytecode cache, a dotted
  expression for that last warning fails 59 of the 82 Agent Mesh test and subtest results with
  `SyntaxError`; the path expression passes all of them. The same warning raised from
  `agent-mesh/tools/` or `agent-mesh/tests/` is an error. Nothing about the dependency audit changes:
  the eleven waivers ADR-0031 recorded stand and expire 2026-09-18.

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

- The `pre-push hooks` job had never once completed. Eight runs, every one stalled immediately after
  `gitleaks (full history)` and killed at the 60-minute cap with orphan `git` and `pager` processes in
  the cleanup log — so whole-tree type checking, the full test suite and its coverage gates, mutation
  scoring, the lockfile checks, Bandit, the dependency audit and the deploy-configuration scan had
  never reported a verdict, red or green. pre-commit runs hooks under a pseudo-terminal to keep their
  colour, git therefore sent `diff --check` through `core.pager`, and on a runner whose `TERM` is
  degraded `less` blocked on `Press RETURN to continue`. `scripts/hooks/repo/check-commit-range.sh`
  now passes `--no-pager`. The complete pre-push stage takes 2m01s
  ([ADR-0059](docs/adr/0059-keep-the-verification-authority-able-to-report.md)).
- The quality-gate harness could not have caught it: `run_script` runs every hook through pipes, so
  git took its pipe path in every test and its terminal path only where nobody was looking.
  `run_script_on_terminal` runs a hook on a pseudo-terminal with a fixed degraded `TERM` and kills the
  session rather than the script, so a surviving pager cannot outlive the test that caught it.
  `test_no_hook_script_lets_git_start_a_pager` holds the class: no project-owned hook script may run a
  pageable git subcommand with the terminal inherited and no pager suppressed.
- Two documents carried an unfinished sentence — `TECH_DEBT.md` and `CONTRIBUTING.md` both stopped at
  "a CodeQL alert page nobody". No gate could see it: markdownlint checks structure, `typos` checks
  tokens, and `docs-strict` checks banned phrases, none of which can tell that a sentence does not end.
- The whole-program Python gates built their argument list from the literal roots
  `tools packages services tests migrations`, so a new top-level directory holding Python was
  checked file by file at the commit stage and not at all by the pre-push run -- the run whose own
  header records that per-file checking gives a different answer than checking the project. The
  roots are now derived from git's own listing.
- The commit-stage `tsc` hook carries `pass_filenames: false`, which makes its `files:` pattern a
  trigger rather than a scope, and the trigger matched only `.ts` and `.tsx`. A change to
  `tsconfig.json`, to `package.json`, or a bumped type-declaration package would have run no type
  check while changing the verdict for every file.
- `security.yml`'s daily audit job set up no Node and no pnpm, while `dependency-audit.sh` audits the
  dashboard through pnpm once a manifest exists. The job would have failed closed on `MISSING: pnpm`
  the day `apps/dashboard/package.json` landed. Its pull-request path filter also omitted
  `apps/dashboard/**`, so a dashboard lockfile change would never have triggered the audit that
  covers it.

- `test_a_missing_openssl_fails_closed` established its precondition with `PATH=/bin`, which hides
  `openssl` on macOS but not on Debian, where `/bin` is a symlink to `/usr/bin`. The test asserted a
  real fail-closed path on the workstation and, on the Linux runner, ran the script with `openssl`
  available and failed on its own assertion. It now points `PATH` at an empty directory. This was the
  first defect continuous integration found that no local run could have.

- The import-contract gate and the domain Ruff banned-api list disagreed in both directions: `httpx`
  was banned only by Ruff, and `litellm` and `solace_agent_mesh` only by the gate. Both now forbid the
  same eight roots, and a gate test holds the two lists equal.

- `SAFETY.md` wrote the approval protocol as an arrow that read as though four states reach `EXECUTED`
  and listed a narrower binding than ADR-0006 requires; it now states the seven legal transitions and
  the full binding. The operator identity is carried as `operatorIdentity` on the wire, because a
  snake-case key is unrepresentable under the canonical key rule; the Python field keeps its name. The
  state-machine roster in `ARCHITECTURE.md` and `IMPLEMENTATION_PLAN.md` now matches ADR-0017, and the
  connectivity row for the recovery count names both impaired states.

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

- `asteval` is overridden from the `1.0.6` that Agent Mesh 1.28.7 pins to `1.0.9`, closing
  CVE-2026-55244 / GHSA-9w56-46f6-3qhx, a sandbox escape in the default `Interpreter` that Agent
  Mesh feeds math embeds taken from model output. The single-package override is the case ADR-0031's
  rule admits; a black-box probe in `agent-mesh/tests/` proves the overridden wheel against the pinned
  runtime on every push and fails the day upstream raises its own pin. The eleven reviewed waivers
  stand; nothing was added to the registry. The official Agent Mesh container image still carries
  1.0.6 until upstream moves ([ADR-0047](docs/adr/0047-override-the-asteval-pin-to-close-cve-2026-55244.md)).

- Every broker connection in the stack validates a per-checkout certificate authority and the broker's
  own certificate; no key, certificate, or password is tracked, and the one plaintext path — the Event
  Management Agent's SEMP connection inside the compose network — is named and never published
  ([ADR-0046](docs/adr/0046-generated-local-certificate-authority.md)).

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
