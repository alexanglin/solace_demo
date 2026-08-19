#!/usr/bin/env sh
# Whole-tree Python formatting and linting for every active Python environment.
set -eu

cd "$(git rev-parse --show-toplevel)"
script_directory=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source-path=SCRIPTDIR
. "$script_directory/quality-components.sh"

root_active=false
agent_active=false
quality_root_python_active && root_active=true
quality_agent_python_active && agent_active=true

if [ "$root_active" = false ] && [ "$agent_active" = false ]; then
	exit 0
fi

if [ "$root_active" = true ] && [ ! -f pyproject.toml ]; then
	printf 'MISSING: pyproject.toml is required by owned root Python source\n' >&2
	exit 1
fi
if [ "$agent_active" = true ] && [ ! -f agent-mesh/pyproject.toml ]; then
	printf 'MISSING: agent-mesh/pyproject.toml is required by owned Agent Mesh source\n' >&2
	exit 1
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
	for path in tools packages services tests migrations; do
		if [ -d "$path" ] && find "$path" -type f \( -name '*.py' -o -name '*.pyi' \) \
			-print -quit | grep -q .; then
			set -- "$@" "$path"
		fi
	done
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
