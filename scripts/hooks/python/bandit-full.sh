#!/usr/bin/env sh
# Static security analysis over every project-owned Python source tree.
set -eu

cd "$(git rev-parse --show-toplevel)"
script_directory=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source-path=SCRIPTDIR
. "$script_directory/../quality-components.sh"

root_active=$(quality_root_python_manifest_state)
agent_active=$(quality_agent_python_manifest_state)

if [ "$root_active" = false ] && [ "$agent_active" = false ]; then
	exit 0
fi

command -v uv >/dev/null 2>&1 || {
	printf 'MISSING: uv is not installed, so Bandit cannot run\n' >&2
	exit 1
}

status=0

if [ "$root_active" = true ]; then
	[ -f uv.lock ] || {
		printf 'MISSING: uv.lock is required for Bandit\n' >&2
		exit 1
	}
	set --
	for path in tools packages services; do
		[ -d "$path" ] && set -- "$@" "$path"
	done
	if [ "$#" -gt 0 ]; then
		uv run --frozen bandit --quiet --recursive --ignore-nosec \
			--severity-level medium --confidence-level medium \
			--exclude '*/tests/*,*/test_*.py' "$@" || status=1
	fi
fi

if [ "$agent_active" = true ]; then
	set --
	for path in \
		agent-mesh/aerial_rescue_event_mesh_gateway \
		agent-mesh/aerial_rescue_runtime_compat \
		agent-mesh/plugins \
		agent-mesh/tools; do
		[ -d "$path" ] && set -- "$@" "$path"
	done
	if [ "$#" -gt 0 ]; then
		[ -f agent-mesh/uv.lock ] || {
			printf 'MISSING: agent-mesh/uv.lock is required for Agent Mesh Bandit checks\n' >&2
			exit 1
		}
		uv run --project agent-mesh --frozen bandit --quiet --recursive --ignore-nosec \
			--severity-level medium --confidence-level medium \
			--exclude '*/tests/*,*/test_*.py' "$@" || status=1
	fi
fi

exit "$status"
