#!/usr/bin/env sh
# Whole-tree cognitive-complexity gate for every owned Python source and test.
set -eu

cd "$(git rev-parse --show-toplevel)"
script_directory=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=scripts/hooks/quality-components.sh
. "$script_directory/../quality-components.sh"

root_active=false
agent_active=false
quality_root_python_active && root_active=true
quality_agent_python_active && agent_active=true

if [ "$root_active" = false ] && [ "$agent_active" = false ]; then
	exit 0
fi
if [ ! -f pyproject.toml ]; then
	printf 'MISSING: pyproject.toml is required by owned Python source\n' >&2
	exit 1
fi
if [ "$agent_active" = true ] && [ ! -f agent-mesh/pyproject.toml ]; then
	printf 'MISSING: agent-mesh/pyproject.toml is required by owned Agent Mesh source\n' >&2
	exit 1
fi
command -v uv >/dev/null 2>&1 || {
	printf 'MISSING: uv is not installed, so cognitive complexity cannot run\n' >&2
	exit 1
}
[ -f uv.lock ] || {
	printf 'MISSING: uv.lock is required for cognitive complexity\n' >&2
	exit 1
}

set --
for path in \
	tools \
	packages \
	services \
	tests \
	agent-mesh/aerial_rescue_event_mesh_gateway \
	agent-mesh/aerial_rescue_runtime_compat \
	agent-mesh/plugins \
	agent-mesh/tools; do
	if [ -d "$path" ] && find "$path" -type f \( -name '*.py' -o -name '*.pyi' \) \
		-print -quit | grep -q .; then
		set -- "$@" "$path"
	fi
done
[ "$#" -gt 0 ] || exit 0

uv run --frozen complexipy "$@" \
	--max-complexity-allowed 15 \
	--check-script \
	--no-ignore \
	--failed \
	--plain
