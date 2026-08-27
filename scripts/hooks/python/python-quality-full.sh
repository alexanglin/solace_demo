#!/usr/bin/env sh
# Whole-tree Python formatting and linting for every active Python environment.
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
	printf 'MISSING: uv is not installed, so whole-tree Python quality checks cannot run\n' >&2
	exit 1
}

status=0

if [ "$root_active" = true ]; then
	[ -f uv.lock ] || {
		printf 'MISSING: uv.lock is required for root Python quality checks\n' >&2
		exit 1
	}
	set --
	while IFS= read -r path; do
		[ -n "$path" ] || continue
		set -- "$@" "$path"
	done <<LISTING
$(quality_root_python_paths)
LISTING
	if [ "$#" -gt 0 ]; then
		uv run --frozen ruff format --check "$@" || status=1
		uv run --frozen ruff check "$@" || status=1
	fi
fi

if [ "$agent_active" = true ]; then
	[ -f agent-mesh/uv.lock ] || {
		printf 'MISSING: agent-mesh/uv.lock is required for Agent Mesh Python quality checks\n' >&2
		exit 1
	}
	(
		cd agent-mesh
		uv run --frozen ruff format --check .
		uv run --frozen ruff check .
	) || status=1
fi

exit "$status"
