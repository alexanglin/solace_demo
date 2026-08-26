# Deployment Instructions

## 1. Scope and authority

These instructions apply to every file under `deploy/`. Read the repository-root
[`AGENTS.md`](../AGENTS.md) first. Its safety, testing, documentation, and version-control rules still
apply. Files in this directory are the supported runtime and its security boundary, not example
configuration.

Use the accepted decisions and canonical documents below instead of copying their current pins or
numeric values into this file:

| Concern | Canonical source |
| --- | --- |
| Broker substrate and non-gating Cloud showcase | [ADR-0043](../docs/adr/0043-docker-broker-with-solace-cloud-showcase.md) |
| Runtime, images, services, profiles, and ports | [ADR-0044](../docs/adr/0044-docker-compose-runtime-with-official-agent-mesh-image.md) |
| Executable Compose and Dockerfile policy | [ADR-0045](../docs/adr/0045-fail-closed-compose-policy-gate.md) |
| Per-checkout certificate authority and consumed secret inventory | [ADR-0046](../docs/adr/0046-generated-local-certificate-authority.md), [ADR-0129](../docs/adr/0129-generate-only-consumed-local-secrets.md) |
| Deploy scanning and actionable image-pin policy | [ADR-0048](../docs/adr/0048-scan-images-and-deploy-configuration-with-trivy.md), [ADR-0055](../docs/adr/0055-block-on-the-image-pin-not-on-advisories-inside-it.md) |
| PostgreSQL major version and durable volume layout | [ADR-0060](../docs/adr/0060-postgresql-18-and-its-data-directory-layout.md) |
| Broker identities, grants, lifecycle sources, projections, and A2A namespace | [ADR-0061](../docs/adr/0061-least-privilege-broker-principals-and-topic-authorization.md), [ADR-0064](../docs/adr/0064-fix-the-agent-mesh-a2a-namespace.md), [ADR-0111](../docs/adr/0111-broker-dashboard-lifecycle-sources.md), [ADR-0120](../docs/adr/0120-run-only-the-recorder-endpoints-the-dashboard-consumes.md) |
| Web UI exposure boundary | [ADR-0065](../docs/adr/0065-validate-the-web-ui-gateway-and-keep-the-platform-service-out.md) |
| Dashboard relay, shared-project mission-control extension, and host-publisher bridges | [ADR-0096](../docs/adr/0096-relay-the-dashboard-over-caddy-and-a-unix-socket.md), [ADR-0117](../docs/adr/0117-select-the-exact-mission-control-service-closure.md), [ADR-0131](../docs/adr/0131-isolate-loopback-publishers-and-forward-startup-flags.md), [ADR-0139](../docs/adr/0139-reuse-the-aerial-rescue-mesh-runtime-for-the-dashboard.md) |
| Current runtime measurements and limits | [`operating-parameters.md`](../docs/operating-parameters.md) |
| Supported commands, recovery, and current profile status | [`CONTRIBUTING.md`](../CONTRIBUTING.md), [`ARCHITECTURE.md`](../docs/ARCHITECTURE.md) |

An image version, profile, exposed surface, credential flow, health policy, database layout, or other
runtime boundary change requires the record and coordinated updates required by the root instructions.

## 2. Directory ownership

| Path | Responsibility |
| --- | --- |
| `compose.yaml` | The single local-runtime definition for every component except host Ollama |
| `agent-mesh/Dockerfile` | The derived official Agent Mesh runtime image |
| `agent-mesh/plugin-requirements.txt` | Hash-locked runtime plugins mirrored from `agent-mesh/uv.lock` |
| `application/Dockerfile` | The locked root-workspace application image |
| `application/uv-requirements.txt` | Hash-locked installer used while building that image |
| `caddy/Caddyfile` | Header-preserving, unbuffered relay from loopback port 8080 to the private dashboard Unix socket |
| ignored `certs/` | Public per-checkout trust material only |
| ignored `secrets/` | Private keys, passwords, role environment, and other generated credentials |

Do not add a parallel Compose file or runtime path to bypass this ownership. A new Compose or
Dockerfile shape must enter the existing inventory, policy gates, scanner, and documentation.

## 3. Runtime and security invariants

- Pin every pulled image by an explicit tag and index digest. Pin every external Dockerfile base by
  digest, never use `latest`, and keep platform selection in Compose at the reviewed exception rather
  than in `FROM`.
- Give every built service an `image` name. The policy gate permits an unnamed build, but the image
  inventory cannot hand an unnamed output to Trivy; an unscanned derived image is not acceptable.
- Publish ports on `127.0.0.1` only and never use host networking. Loopback binding is a compensating
  control for the Agent Mesh Web UI and its accepted upstream risk, not developer convenience that may
  be relaxed locally.
- Give broker, PostgreSQL, and Caddy separate single-member host-publisher bridges. Each bridge disables
  IP masquerade and defaults host binding to `127.0.0.1`; no other service may join one or use it as an
  outbound path. Their application edges remain on the dedicated internal networks or, for Caddy, the
  private Unix socket.
- Keep Ollama on the host bridge and never expose it publicly. Keep broker management and plaintext
  messaging ports inside the Compose network unless an accepted decision changes the boundary.
- Supply credentials through declared environment indirection or files under `/run/secrets/`. Never put
  a literal credential, URL userinfo, private key, password, or live tenant value in Compose,
  Dockerfiles, build arguments, fixtures, logs, screenshots, or evidence.
- Never print or persist `docker compose config` rendered with `.env` and
  `deploy/secrets/.env.roles`; interpolation can disclose every role password. Use `.env.example` for
  non-secret structural work and redact any runtime evidence.
- Preserve one least-privilege broker identity per authorized role. Do not restore the factory fallback,
  share a convenience identity, or issue an identity to a component with no recorded broker role.
- Keep a healthcheck on every long-running service and readiness ordering on every dependent service.
  Only the enumerated migration and replay-validator one-shot jobs may omit one; they must use
  `restart: "no"`, and dependants wait for `service_completed_successfully`. A static green
  gate proves file policy only; it does not prove that the probe exists in the image, TLS is served,
  data survives recreation, authorization was provisioned, or a profile starts.
- Preserve non-root execution, numeric project-owned users, `no-new-privileges`, and read-only trust and
  configuration mounts. Install Python artifacts by hash from synchronized locks.
- Treat the profile set and platform exception set as closed policy. Keep runnable, scaffolded,
  degraded, and showcase states truthful against current architecture and live evidence; the presence
  of configuration is not proof that a profile is operational.
- Do not introduce top-level includes, service `extends`, inline Dockerfiles, or another unreviewed path
  around the files the policy gate enumerates.
- Treat the PostgreSQL volume mount as load-bearing. A future major upgrade requires an explicit data
  migration and rollback decision; never infer that an existing volume can be reset because an earlier
  scaffold-era reset was safe.
- Keep native Agent Mesh verification distinct from the container runtime. After changing its base,
  plugins, or installed packages, prove compatibility inside the built image as well as in
  `agent-mesh/.venv`.

## 4. Coordinate cross-tree changes

| Change under `deploy/` | Inspect and update together |
| --- | --- |
| Environment reference or secret | `.env.example`, its runtime consumer, the semantic validator, secret generator, and wiring tests |
| Broker identity or grant | Governing ADR, `packages/domain`, `packages/broker`, `scripts/broker-secrets.sh`, Compose, and live denial tests |
| Agent Mesh plugin or base | `agent-mesh/pyproject.toml`, `agent-mesh/uv.lock`, hashed plugin requirements, image inventory, and in-container compatibility evidence |
| Application dependency or base | Root/member manifests, `uv.lock`, hashed installer requirement when applicable, `.dockerignore`, and image build evidence |
| Image pin | Governing ADR when required, `operating-parameters.md`, image inventory/pin tests, and image scans |
| Service, profile, port, healthcheck, or platform | Architecture/runbook, pure policy gate, its conformance tests, and applicable live evidence |
| PostgreSQL major or mount | ADR, operating parameter, migration and recovery runbook, and live durability evidence |

Do not weaken a policy refusal merely to admit a new shape. Change the governing decision, pure gate,
and positive and negative conformance tests together.

## 5. Generated state and live operations

- `just secrets` fills missing per-checkout material without printing values. Do not read, copy, attach,
  or track generated private material.
- `just rotate-secrets` overwrites credentials and certificates. Run it only with explicit authority,
  then recreate affected containers and reapply broker provisioning so runtime state agrees.
- `just down` preserves volumes. Never use `down -v`, remove a named volume, or reset PostgreSQL without
  explicit human authorization and the migration or recovery decision that makes data loss acceptable.
- Starting profiles, applying broker provisioning, pulling or building images, and running the Cloud
  showcase change external state or use network resources. Keep them within the user's requested scope
  and never place Cloud credentials in continuous integration or tracked files.
- A bind-mounted Agent Mesh configuration does not restart the running process. Recreate the container
  before claiming a configuration or image change was exercised.
- `just mission-control-up` is the supported dashboard extension entry point inside the existing
  `aerial-rescue-mesh` project. It requires the shared broker and PostgreSQL containers to exist and be
  healthy, records their IDs, selects seven dashboard-owned targets with `--no-deps`, and verifies both
  IDs after startup. It never creates, starts, updates, or replaces either shared stateful service.
- The seven selected targets are migration, fleet simulator, scenario service, recorder, replay
  validator, dashboard API, and Caddy. The recipe applies the bounded mission-control broker projection,
  starts fleet command intake in publication-only mode, and shares recorder freshness through a tmpfs
  volume. Required queues and grants are a subset of the shared broker inventory; unrelated runtime
  endpoints are neither an isolation failure nor something dashboard startup may delete.
- `mission-control-up *ARGS` forwards Compose `up` flags only to those seven extension targets.
  `--no-deps` and the post-start identity checks protect the shared broker and PostgreSQL containers.
  Normal `just up` remains their lifecycle owner.
- `mission-control-down` stops only the five long-running dashboard services: fleet simulator,
  scenario service, recorder, dashboard API, and Caddy. It must never issue Compose `down`, stop broker
  or PostgreSQL, remove project networks or volumes, or delete persisted dashboard history. The status
  and log recipes may inspect all seven extension targets, including the two completed one-shot jobs.
- Production and soak controls record the shared broker and PostgreSQL container IDs before startup and
  after cleanup and require equality. Fault injection may target only dashboard-owned services in the
  shared project.
- `just up *ARGS` places arguments after the `up` subcommand, so `up` options such as
  `--force-recreate` and `--build` pass through directly. Select an extra profile with the
  `COMPOSE_PROFILES` environment variable, which Compose reads on its own:

```sh
just up --force-recreate --build
COMPOSE_PROFILES=services just up
just mission-control-up --build
just mission-control-ps
just mission-control-logs
just mission-control-down
```

  `just up` is the supported entry point. A bare `docker compose up` reads only `.env`, where the six
  Agent Mesh role credentials do not live, so the references expand to empty and the broker refuses
  the connection as the shutdown factory `default` username, retrying without an error.

## 6. Required verification

Run static verification from the repository root before any live operation:

```sh
scripts/hooks/deploy/check-compose-policy.sh
scripts/hooks/deploy/trivy-config-full.sh
uv run --frozen pytest -q \
  tools/quality_gate_tests/analysis/test_compose_policy.py \
  tools/quality_gate_tests/analysis/test_dockerfile_policy.py \
  tools/quality_gate_tests/analysis/test_trivy_adjudication.py \
  tools/quality_gate_tests/deploy \
  tools/quality_gate_tests/hooks/test_compose_policy_stage.py \
  tools/quality_gate_tests/hooks/test_trivy_config_stage.py
```

`just check-compose` and `just check-deploy-config` are the human-facing equivalents when `just` is
installed. Also run the Compose schema, YAML, and Hadolint hooks for changed files, then the complete
commit and push stages required by the root instructions.

For an image change, run the Docker- and network-dependent pin check, build, and full image scan through
`just check-image-pins` and `just scan-images`. Image advisories are informational under ADR-0055; do
not call the image clean, suppress the report, or create an advisory waiver. A stale or unresolvable
image pin is the blocking, actionable result.

For an authorized runtime change, follow the local-stack sequence in `CONTRIBUTING.md`, run the
applicable live tests under `tests/phase0/` and `tests/security/`, and record only redacted evidence.
Static conformance, an image build, and a live runtime result are three different claims; report each
one honestly and state every check that could not run.
