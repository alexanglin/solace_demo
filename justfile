# Aerial Rescue Mesh -- canonical command entrypoints.
#
# Hooks and CI call the scripts under scripts/ directly, NOT these recipes, so
# neither depends on `just` being installed. These are the human-facing names.

# The executable members only. A declared-only member has no process, consumer, or credential,
# so a command queue for one is an endpoint that implies an execution path the product denies
# (docs/adr/0118-provision-command-queues-only-for-executable-members.md).
reference_drone_arguments := "--drone drone-sim-01 --drone drone-sim-02 --drone drone-sim-03 --drone drone-sim-04 --drone drone-sim-05 --drone drone-sim-06 --drone drone-sim-07 --drone drone-sim-08 --drone drone-sim-09 --drone drone-sim-10 --drone drone-sim-11 --drone drone-sim-12 --drone drone-sim-13 --drone drone-sim-14 --drone drone-sim-15 --drone drone-sim-16 --drone drone-sim-17 --drone drone-sim-18 --drone drone-sim-19 --drone drone-sim-20"
mission_control_services := "broker-event-monitor migration fleet-simulator scenario-service recorder replay-validator dashboard-api caddy"
mission_control_long_running_services := "broker-event-monitor fleet-simulator scenario-service recorder dashboard-api caddy"

# Show available recipes.
default:
    @just --list

# One-time developer setup.
install:
    pre-commit install --install-hooks
    @echo "Hooks installed for: pre-commit, commit-msg, pre-push, post-checkout, post-merge, pre-merge-commit"

# Everything CI runs, in the same order.
check: check-commit check-push

# Fast tier -- what runs on every commit.
check-commit:
    pre-commit run --all-files --hook-stage pre-commit

# Thorough tier -- what runs before every push.
check-push:
    pre-commit run --all-files --hook-stage pre-push

# Type-check both Python environments and the dashboard, whole-program, as CI does.
check-types:
    pre-commit run --all-files --hook-stage pre-push mypy-full
    pre-commit run --all-files --hook-stage pre-push dashboard-typecheck-full

# Hold dashboard policy, types, lint, format, coverage, and integration to ADR-0057 and ADR-0105.
check-dashboard:
    pre-commit run --all-files --hook-stage pre-commit typescript-policy
    pre-commit run --all-files --hook-stage pre-push dashboard-contracts-current-all
    pre-commit run --all-files --hook-stage pre-push dashboard-typecheck-full
    pre-commit run --all-files --hook-stage pre-push dashboard-quality-full
    pre-commit run --all-files --hook-stage pre-push dashboard-test-full
    pre-commit run --all-files --hook-stage pre-push dashboard-integration-full

# Run the complete dashboard browser acceptance gate with its runtime, cache, and artifact checks.
check-dashboard-browser:
    pre-commit run --all-files --hook-stage pre-push dashboard-playwright-full

# Verify the mandatory AAA structure of every project-owned executable test.
check-aaa:
    pre-commit run --all-files --hook-stage pre-commit test-aaa

# Validate the complete schema and golden-fixture inventory without network access.
check-contracts:
    pre-commit run --all-files --hook-stage pre-commit contract-artifacts

# Enforce Ruff complexity, cognitive complexity, and multi-language duplication.
check-complexity:
    pre-commit run --all-files --hook-stage pre-push python-quality-full
    pre-commit run --all-files --hook-stage pre-push cognitive-complexity-full
    pre-commit run --all-files --hook-stage pre-push duplication-full

# Run independent mutation tests and score every tier-one module.
check-mutation:
    pre-commit run --all-files --hook-stage pre-push mutation-full

# Hold the deploy/ compose stack and its Dockerfiles to the compose policy gate.
check-compose:
    pre-commit run --all-files --hook-stage pre-commit compose-policy

# Audit the deploy/ Dockerfiles for misconfiguration with Trivy, adjudicated by the waiver gate.
check-deploy-config:
    pre-commit run --all-files --hook-stage pre-push trivy-config-full

# Refuse a pinned image digest that upstream has already moved past. Needs Docker.
check-image-pins:
    scripts/security/check-image-pins.sh

# Run the pinned-plugin probe inside the built Agent Mesh image, on the image's own
# interpreter rather than agent-mesh/.venv. Needs Docker and a built image.
probe-image:
    scripts/probes/agent-mesh-image-probe.sh

# Build the derived images and scan every stack image with Trivy. Needs Docker and trivy.
scan-images:
    docker compose --env-file .env.example -f deploy/compose.yaml --profile services build agent-mesh dashboard-api
    scripts/security/scan-images.sh

# Generate the per-checkout certificate authority, broker certificate, and stack passwords.
secrets:
    scripts/broker-secrets.sh

# Replace the generated authority, certificate, and passwords.
rotate-secrets:
    scripts/broker-secrets.sh --rotate

# Start the whole default stack -- broker, Postgres, and the Agent Mesh -- in the order the
# authorization matrix requires (docs/adr/0094). The broker is provisioned before the mesh
# connects, because until it is, the factory `default` username is still enabled and any
# identity may publish any topic (docs/adr/0061). Compose flags pass through:
# `just up --force-recreate`. Add a profile with `COMPOSE_PROFILES=services just up`.
# The Agent Mesh Platform service migrates its own database at boot but cannot create it, and
# the image creates only POSTGRES_DB on a first initialisation, so that database is created
# here once PostgreSQL is healthy and before the mesh starts (docs/adr/0222).
up *ARGS:
    docker compose --env-file .env --env-file deploy/secrets/.env.roles -f deploy/compose.yaml up --detach --wait broker postgres
    scripts/create-platform-database.sh
    uv run --frozen python -m aerial_rescue_broker --namespace aerial-rescue-mesh {{reference_drone_arguments}}
    scripts/preflight-ollama.sh
    docker compose --env-file .env --env-file deploy/secrets/.env.roles -f deploy/compose.yaml up --detach --wait {{ARGS}}

# Start the dashboard extension inside the existing aerial-rescue-mesh project. Broker and
# PostgreSQL are shared stack services and must already be healthy; --no-deps prevents this
# recipe from creating, starting, or updating either shared service. The accepted optional
# flags are --build, --no-build, and --force-recreate.
mission-control-up *ARGS:
    @set -eu; \
        requested_args={{quote(ARGS)}}; \
        build_requested=false; \
        no_build_requested=false; \
        recreate_arg=; \
        set -f; \
        set -- $requested_args; \
        for arg do \
            case "$arg" in \
                --build) build_requested=true ;; \
                --no-build) no_build_requested=true ;; \
                --force-recreate) recreate_arg=--force-recreate ;; \
                *) echo "unsupported mission-control-up argument: $arg" >&2; exit 2 ;; \
            esac; \
        done; \
        if [ "$build_requested" = true ] && [ "$no_build_requested" = true ]; then \
            echo "mission-control-up cannot combine --build and --no-build" >&2; \
            exit 2; \
        fi; \
        broker_id="$(docker compose --env-file .env --env-file deploy/secrets/.env.roles -f deploy/compose.yaml ps -q broker)"; \
        postgres_id="$(docker compose --env-file .env --env-file deploy/secrets/.env.roles -f deploy/compose.yaml ps -q postgres)"; \
        test -n "$broker_id" || { echo "mission-control requires the shared broker; run just up first" >&2; exit 1; }; \
        test -n "$postgres_id" || { echo "mission-control requires shared postgres; run just up first" >&2; exit 1; }; \
        docker compose --env-file .env --env-file deploy/secrets/.env.roles -f deploy/compose.yaml ps --format json broker | grep -Eq '"Health"[[:space:]]*:[[:space:]]*"healthy"' || { echo "mission-control requires the shared broker to be healthy; run just up first" >&2; exit 1; }; \
        docker compose --env-file .env --env-file deploy/secrets/.env.roles -f deploy/compose.yaml ps --format json postgres | grep -Eq '"Health"[[:space:]]*:[[:space:]]*"healthy"' || { echo "mission-control requires shared postgres to be healthy; run just up first" >&2; exit 1; }; \
        if [ "$build_requested" = true ]; then \
            docker compose --env-file .env --env-file deploy/secrets/.env.roles -f deploy/compose.yaml --profile mission-control build dashboard-api; \
        fi; \
        docker compose --env-file .env --env-file deploy/secrets/.env.roles -f deploy/compose.yaml --profile mission-control up --no-start --no-deps --no-build $recreate_arg {{mission_control_services}}; \
        docker inspect "$broker_id" | grep -q '"aerial-rescue-mesh_event-mesh"' || docker network connect --alias broker aerial-rescue-mesh_event-mesh "$broker_id"; \
        docker inspect "$postgres_id" | grep -q '"aerial-rescue-mesh_store"' || docker network connect --alias postgres aerial-rescue-mesh_store "$postgres_id"; \
        uv run --frozen python -m aerial_rescue_broker --namespace aerial-rescue-mesh --port 1943 {{reference_drone_arguments}}; \
        docker compose --env-file .env --env-file deploy/secrets/.env.roles -f deploy/compose.yaml --profile mission-control up --detach --wait --no-deps --no-build --no-recreate {{mission_control_services}}; \
        test "$broker_id" = "$(docker compose --env-file .env --env-file deploy/secrets/.env.roles -f deploy/compose.yaml ps -q broker)" || { echo "mission-control changed the shared broker container" >&2; exit 1; }; \
        test "$postgres_id" = "$(docker compose --env-file .env --env-file deploy/secrets/.env.roles -f deploy/compose.yaml ps -q postgres)" || { echo "mission-control changed the shared postgres container" >&2; exit 1; }

# Stop only the exact mission-control closure; named data volumes are preserved.
mission-control-down:
    docker compose --env-file .env --env-file deploy/secrets/.env.roles -f deploy/compose.yaml --profile mission-control stop {{mission_control_long_running_services}}

# Follow only the mission-control closure's logs.
mission-control-logs:
    docker compose --env-file .env --env-file deploy/secrets/.env.roles -f deploy/compose.yaml --profile mission-control logs --follow --tail 200 {{mission_control_services}}

# Show only the mission-control closure's state and health.
mission-control-ps:
    docker compose --env-file .env --env-file deploy/secrets/.env.roles -f deploy/compose.yaml --profile mission-control ps {{mission_control_services}}

# Apply the broker authorization matrix over SEMP. Needs a running broker and the
# credentials `just secrets` writes (docs/adr/0061). Safe to re-run; it converges.
provision *ARGS:
    uv run --frozen python -m aerial_rescue_broker {{ARGS}}

# Delete the application queues the desired state no longer names. Separate from `provision`
# because converging never deletes: ADR-0154 makes retirement an operator-invoked readback and
# ADR-0157 permits deletion only through it. Refuses any queue that still holds a message or a
# consumer, and refuses a run that names no fleet, which would make every drone queue stale.
retire-stale-queues *ARGS:
    uv run --frozen python -m aerial_rescue_broker.retire --namespace aerial-rescue-mesh {{reference_drone_arguments}} {{ARGS}}

# Stop the stack; volumes are kept.
down:
    docker compose --env-file .env --env-file deploy/secrets/.env.roles -f deploy/compose.yaml down

# Follow the stack's logs.
logs:
    docker compose --env-file .env --env-file deploy/secrets/.env.roles -f deploy/compose.yaml logs --follow --tail 200

# Follow only new broker event-facility JSON and emit tenant-neutral catalog alerts. The
# monitor intentionally exits nonzero if Docker closes the stream or alert delivery fails.
broker-events:
    #!/usr/bin/env bash
    set -o errexit -o nounset -o pipefail
    docker compose --env-file .env --env-file deploy/secrets/.env.roles -f deploy/compose.yaml logs --follow --tail 0 --no-log-prefix broker | uv run --frozen aerial-rescue-broker-events

# Show the stack's services and health.
ps:
    docker compose --env-file .env --env-file deploy/secrets/.env.roles -f deploy/compose.yaml ps

# Point the same stack at the Solace Cloud showcase service (docs/adr/0043). `.env.showcase`
# is an ignored operator-created copy of .env.example carrying the service's values.
showcase *ARGS:
    docker compose --env-file .env.showcase -f deploy/compose.yaml {{ARGS}} up --detach --wait broker postgres

# Apply every automatic fix. The only thing here that modifies files.
fix:
    scripts/fix.sh

# Regenerate architecture PNGs from their Graphviz sources and refresh hashes.
diagrams:
    scripts/diagrams.sh

# Strict documentation check. Blocking at pre-commit since 2026-08-19.
lint-docs-strict:
    pre-commit run --all-files docs-strict

# Refresh pinned hook revisions. Review every change before committing.
update-hooks:
    pre-commit autoupdate

# Remove hook caches and generated artifacts.
clean:
    pre-commit clean
    rm -rf .mypy_cache .ruff_cache .pytest_cache htmlcov .coverage
