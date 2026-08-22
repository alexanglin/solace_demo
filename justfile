# Aerial Rescue Mesh -- canonical command entrypoints.
#
# Hooks and CI call the scripts under scripts/ directly, NOT these recipes, so
# neither depends on `just` being installed. These are the human-facing names.

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

# Hold the dashboard's TypeScript configuration, lint, and formatting to docs/adr/0057.
check-dashboard:
    pre-commit run --all-files --hook-stage pre-commit typescript-policy
    pre-commit run --all-files --hook-stage pre-push dashboard-typecheck-full
    pre-commit run --all-files --hook-stage pre-push dashboard-quality-full

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
    docker compose --env-file .env.example -f deploy/compose.yaml --profile mesh --profile services build agent-mesh dashboard-api
    scripts/security/scan-images.sh

# Generate the per-checkout certificate authority, broker certificate, and stack passwords.
secrets:
    scripts/broker-secrets.sh

# Replace the generated authority, certificate, and passwords.
rotate-secrets:
    scripts/broker-secrets.sh --rotate

# Start the broker and Postgres and wait for both to be healthy. Add a profile explicitly:
# `just up --profile mesh`, `--profile services`, or `--profile event-portal`.
up *ARGS:
    docker compose --env-file .env --env-file deploy/secrets/.env.roles -f deploy/compose.yaml {{ARGS}} up --detach --wait

# Apply the broker authorization matrix over SEMP. Needs a running broker and the
# credentials `just secrets` writes (docs/adr/0061). Safe to re-run; it converges.
provision *ARGS:
    uv run --frozen python -m aerial_rescue_broker {{ARGS}}

# Stop the stack; volumes are kept.
down:
    docker compose --env-file .env --env-file deploy/secrets/.env.roles -f deploy/compose.yaml down

# Follow the stack's logs.
logs:
    docker compose --env-file .env --env-file deploy/secrets/.env.roles -f deploy/compose.yaml logs --follow --tail 200

# Show the stack's services and health.
ps:
    docker compose --env-file .env --env-file deploy/secrets/.env.roles -f deploy/compose.yaml ps

# Point the same stack at the Solace Cloud showcase service (docs/adr/0043). `.env.showcase`
# is an ignored operator-created copy of .env.example carrying the service's values.
showcase *ARGS:
    docker compose --env-file .env.showcase -f deploy/compose.yaml {{ARGS}} up --detach --wait

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
