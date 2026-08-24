# ADR-0098: Start the Agent Mesh with the default profile, behind an ordered startup

- **Status:** Accepted
- **Date:** 2026-08-24
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

The Solace Agent Mesh is the demonstration's subject. It is also the one component a developer or a
presenter has to remember to ask for: `agent-mesh` carries `profiles: ["mesh"]`, so `just up` and
`docker compose up` start the broker and Postgres and nothing else. Every recorded mesh run typed
`--profile mesh` by hand; no recipe in the `justfile` starts the mesh at all.

That was deliberate and time-bounded, not permanent.
[ADR-0044](0044-docker-compose-runtime-with-official-agent-mesh-image.md) records the condition
verbatim: "`mesh` stays a profile until the first file lands under `agent-mesh/configs/`". The compose
file's own header repeats it — "inert until `agent-mesh/configs/` holds the first configuration, which
is when it moves to the default profile". Five configurations have since landed: the Orchestrator, the
MissionCoordinator agent, the MissionResponse workflow, the HTTP/SSE Web UI, and the Event Mesh
Gateway. All five have been proven live against the broker
([`mesh-first-run.md`](../../release-evidence/phase-0/mesh-first-run.md),
[`event-mesh-gateway-first-run.md`](../../release-evidence/phase-0/event-mesh-gateway-first-run.md),
[`event-mesh-tool-first-run.md`](../../release-evidence/phase-0/event-mesh-tool-first-run.md)). The
stated condition is met, so the profile has outlived its reason.

Moving the service is one deleted line. What makes this a decision rather than an edit is everything
that line was load-bearing for.

**The authorization matrix is applied by a separate manual step.** `just provision` writes the nine
least-privilege identities and their ACL profiles over SEMP and disables the factory `default` client
username, which ships enabled on an allow-everything profile
([ADR-0061](0061-least-privilege-broker-principals-and-topic-authorization.md)). Until it runs, any
identity may publish any topic. A mesh that starts with the default profile on a freshly created broker
therefore comes up **healthy, on factory authority** — the exact posture ADR-0061 removed, and a
failure that looks like success. Against a stale or namespace-less matrix the opposite happens: the
container stays unhealthy with `SOLCLIENT_SUBCODE_SUBSCRIPTION_ACL_DENIED` visible only in its log, and
`restart: unless-stopped` retries rather than exits.

**`--namespace` is optional to the parser and mandatory in effect.** Omitting it writes 41 topic
exceptions instead of 47, and the three Agent Mesh roles get no A2A grant at all.

**`SESSION_SECRET_KEY` ships as the literal string `<required>`.** The upstream check is
presence-only, so the placeholder is accepted and the Web UI signs sessions with it. While the mesh was
opt-in this cost nothing. On by default, it is the quietest cost of every fresh clone.

**Nothing in the readiness path touches Ollama.** The management server's `/readyz` is broker-connected
and database-connected and every flow thread alive; no app declares a database and no custom check is
configured. With the daemon stopped the container reports healthy and the first prompt fails — a green
stack that cannot think.

**The runtime carries an accepted, unfixable advisory.** `google-adk` 1.18.0 / PYSEC-2026-344 is
unauthenticated remote code execution, waived in `dependency-waivers.toml` on the compensating control
that every Agent Mesh surface is bound to loopback ([ADR-0031](0031-reject-the-google-adk-version-override.md),
[TECH_DEBT.md](../../TECH_DEBT.md)). Default-on moves that runtime from opt-in to running on every
start. It does not breach the control — the Web UI stays published on `127.0.0.1:8000` — but the
exposure changes from occasional to continuous and must be recorded as such.

## Decision

**`agent-mesh` joins the default profile.** Its `profiles: ["mesh"]` key is deleted. `mesh` remains a
known profile name in the compose policy gate's closed set; no service declares it. The service keeps
its existing `depends_on: broker: {condition: service_healthy}` and gains no dependency on Postgres,
because no Agent Mesh app declares a database.

**`just up` becomes a single ordered entry point** rather than one compose invocation, in four phases:

1. Bring `broker` and `postgres` up and wait for both to be healthy.
2. Apply the authorization matrix with the namespace stated literally:
   `python -m aerial_rescue_broker --namespace aerial-rescue-mesh`. The literal is held equal to
   `.env.example`'s `NAMESPACE` by a gate test.
3. Run the Ollama preflight. It refuses to continue when the daemon is unreachable, when the identifier
   in `agent-mesh/model-lock.toml` is absent from `GET /api/tags`, or when the manifest digest
   disagrees with the lock.
4. Bring the remaining services up and wait.

Phase 2 runs before the mesh connects for **security**, not liveness: it is what removes factory
authority. Phase 3 refuses **the mesh** while leaving broker and Postgres running, and exits non-zero
so nothing mistakes it for success.

The startup is not expressed as a compose provisioner service. That would require a healthcheck the
gate cannot waive, a reviewed Dockerfile, and a SEMP administrative credential inside the compose
network — a security-widening change for the narrow benefit of covering bare `docker compose up`, which
is not a supported entry point and never was.

**`SESSION_SECRET_KEY` is generated, never hand-set.** `scripts/broker-secrets.sh` emits it beside the
twelve existing passwords, into `deploy/secrets/.env.roles`, which `just up` passes as the second
`--env-file` and which therefore overrides `.env`. The fresh-clone path reduces to
`cp .env.example .env`, `just secrets`, `just up`.

**Readiness stays broker-scoped.** The container healthcheck is unchanged and the Ollama check stays on
the host. Coupling container health to a host daemon would fail `just up` for six minutes on an Ollama
hiccup and would contradict the root instruction that loss of Ollama "must not disable telemetry,
operator visibility, replay, or the approval boundary".

**`just showcase` names `broker postgres` explicitly**, keeping its scope exactly what it is today
rather than silently acquiring the mesh against the Solace Cloud service.

## Consequences

The demonstration's subject is running whenever the stack is running, which is the point.

`just up` stops being a thin alias for one compose command. It builds
`aerial-rescue/agent-mesh:1.28.7` on first run from a 3.92 GB base — no default-profile service does
that today — so the runbook must say to build ahead of a demonstration rather than in front of one.

The default stack's memory floor rises from 1.58 GiB to the figure recorded for broker, Postgres, and
the mesh together. `docs/operating-parameters.md` carries the measurement; the old default-profile row
ceases to have a subject.

An unhealthy mesh now blocks the front-door command. The healthcheck allows a 60-second start period
and twenty 15-second retries, so a mesh that never becomes healthy holds `--wait` for up to six
minutes. That is a worse failure than today's, and it is accepted deliberately: the alternative is a
stack that reports success while the component being demonstrated is absent.

Ollama becomes a hard prerequisite of `just up` rather than a prerequisite of asking an agent a
question. A developer who only wants the broker must now say so.

The accepted `google-adk` advisory moves from opt-in to every start. The compensating control is
unchanged and still holds — every Agent Mesh surface remains bound to loopback — but the reassessment
trigger recorded in `TECH_DEBT.md` ("reassess immediately if any Agent Mesh surface is ever exposed
beyond loopback") now applies to a continuously running process.

Bare `docker compose up` degrades from "starts the broker" to "starts a mesh that hangs unhealthy for
six minutes". It reads only `.env`, where the six mesh role credentials do not live, so the reference
expands to empty and the broker refuses the connection as the shutdown factory `default` username,
retrying without an error. This is documented, not defended: `just up` is the supported entry point.

`docs/ARCHITECTURE.md`'s composite readiness definition is stricter than the `/readyz` probe that now
decides whether the default stack is up. That gap is named here and left open rather than closed
quietly.

Two claims this record relies on were code-path inferences. The accompanying live run
([`default-profile-with-agent-mesh.md`](../../release-evidence/phase-0/default-profile-with-agent-mesh.md))
measures one: the mesh reached healthy 12 seconds into the final phase, far inside the six-minute
worst case. The other — that the container reports healthy with Ollama stopped — is **still
unmeasured**, and the evidence record says so. The preflight makes it less pressing, because `just up`
now refuses before reaching the mesh, but it does not answer it.

## Alternatives considered

**Leave the profile and add a `just demo` recipe that passes `--profile mesh`.** Rejected. It keeps the
default stack a stack without its subject, and it preserves the failure this record removes: a
presenter who types `just up` gets a green stack with no mesh and no indication anything is missing.

**Delete the profile and change nothing else.** Rejected as unsafe. On a freshly created broker it
starts the mesh on factory authority, because the authorization matrix is a separate manual step; the
result is healthy, silent, and wrong.

**Add a provisioner service to the compose file with `depends_on` ordering.** Rejected. The compose
policy gate requires a healthcheck on every service with no exception and its Dockerfile in the
reviewed set, and it would place a SEMP administrative credential inside the compose network. The
ordering belongs in the entry point, not the topology.

**Make the container healthcheck depend on Ollama.** Rejected. `custom_ready_check` is imported by
dotted path from inside the image, so it cannot be supplied by the read-only configuration mount
without changing the derived image and the scanned image set. It would also make an Ollama hiccup fail
the whole stack, contradicting the fail-safe rule that Ollama loss must not disable telemetry,
operator visibility, replay, or the approval boundary.

**Warn about a missing Ollama instead of refusing.** Rejected. `/readyz` never touches Ollama, so the
warning would be followed by a green stack whose agents fail at the first prompt — the failure moves
from before the demonstration to during it.

**Keep `SESSION_SECRET_KEY` manual and have the preflight refuse the placeholder.** Rejected as a
weaker form of the same fix: it leaves a required manual step in the fresh-clone path and leaves a
placeholder secret valid for anyone who sets it to something else by hand. Generating it removes the
step and the placeholder together.
