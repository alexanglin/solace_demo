#!/usr/bin/env sh
# Cross-language duplication gate over project-owned code and tests.
set -eu

cd "$(git rev-parse --show-toplevel)"
script_directory=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=scripts/hooks/quality-components.sh
. "$script_directory/../quality-components.sh"

root_active=false
agent_active=false
dashboard_active=false
quality_root_python_active && root_active=true
quality_agent_python_active && agent_active=true
quality_dashboard_active && dashboard_active=true

if [ "$root_active" = false ] && [ "$agent_active" = false ] && [ "$dashboard_active" = false ]; then
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
if [ "$dashboard_active" = true ] && [ ! -f apps/dashboard/package.json ]; then
	printf 'MISSING: apps/dashboard/package.json is required by owned dashboard source\n' >&2
	exit 1
fi
command -v jscpd >/dev/null 2>&1 || {
	printf 'MISSING: jscpd is not installed, so duplication analysis cannot run\n' >&2
	exit 1
}

set --
for path in tools packages services tests migrations scripts agent-mesh/plugins agent-mesh/tools apps/dashboard; do
	[ -d "$path" ] && set -- "$@" "$path"
done
[ "$#" -gt 0 ] || exit 0

jscpd \
	--threshold 3 \
	--min-lines 8 \
	--min-tokens 50 \
	--mode strict \
	--format python,javascript,jsx,typescript,tsx,bash \
	--reporters console \
	"$@"
