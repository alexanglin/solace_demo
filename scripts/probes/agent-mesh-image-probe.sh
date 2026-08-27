#!/usr/bin/env sh
# Run the pinned-plugin probe inside the built Agent Mesh image, on the image's own
# interpreter rather than agent-mesh/.venv (docs/ARCHITECTURE.md, TECH_DEBT.md section 6).
#
# A shell script rather than a pytest case for two reasons. The image's /opt/venv carries no
# pytest, and docs/adr/0025 fixes the four files permitted to own a Ruff-reviewed subprocess
# call, so a Python runner would have to become a fifth. Docker-invoking work already lives in
# scripts/ for the same reason (scripts/security/scan-images.sh).
#
# It never gates: it needs a Docker daemon and a built image, which no hook stage may require.
set -eu

cd "$(git rev-parse --show-toplevel)"

IMAGE=aerial-rescue/agent-mesh:1.28.7
PROBE=agent-mesh/tools/image_probe.py

[ -f "$PROBE" ] || {
	printf 'MISSING: %s is required by the image probe\n' "$PROBE" >&2
	exit 1
}
command -v docker >/dev/null 2>&1 || {
	printf 'MISSING: docker is not installed, so the image probe cannot run\n' >&2
	exit 1
}
docker image inspect "$IMAGE" >/dev/null 2>&1 || {
	printf 'MISSING: %s is not built; run "just up" first\n' "$IMAGE" >&2
	exit 1
}

printf 'Probing %s\n' "$IMAGE"

# --network none because the probe imports and introspects and must never reach the broker,
# Ollama, or an index; --entrypoint because the image's own CMD starts the runtime.
exec docker run \
	--rm \
	--network none \
	--read-only \
	--tmpfs /tmp \
	--security-opt no-new-privileges \
	--entrypoint /opt/venv/bin/python \
	--volume "$PWD/agent-mesh/tools:/probe:ro" \
	"$IMAGE" \
	/probe/image_probe.py
