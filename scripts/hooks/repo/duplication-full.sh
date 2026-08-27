#!/usr/bin/env sh
# Cross-language duplication gate over authored code and tests.
#
# Generated dashboard contract types are excluded (docs/adr/0110). One module per schema is
# fixed by ADR-0058, so their shared shapes are not a defect an author can refactor, and the
# dashboard-contracts-check hook rewrites and byte-compares that directory, so nothing
# hand-written can hide inside it.
set -eu

cd "$(git rev-parse --show-toplevel)"
script_directory=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=scripts/hooks/quality-components.sh
. "$script_directory/../quality-components.sh"

root_active=$(quality_root_python_manifest_state)
agent_active=$(quality_agent_python_manifest_state)
dashboard_active=$(quality_dashboard_manifest_state)

if [ "$root_active" = false ] && [ "$agent_active" = false ] && [ "$dashboard_active" = false ]; then
	exit 0
fi
command -v jscpd >/dev/null 2>&1 || {
	printf 'MISSING: jscpd is not installed, so duplication analysis cannot run\n' >&2
	exit 1
}

set --
for path in \
	tools \
	packages \
	services \
	tests \
	scripts \
	agent-mesh/aerial_rescue_event_mesh_gateway \
	agent-mesh/aerial_rescue_runtime_compat \
	agent-mesh/plugins \
	agent-mesh/tools \
	apps/dashboard; do
	[ -d "$path" ] && set -- "$@" "$path"
done
[ "$#" -gt 0 ] || exit 0

jscpd \
	--threshold 3 \
	--min-lines 8 \
	--min-tokens 50 \
	--mode strict \
	--format python,javascript,jsx,typescript,tsx,bash \
	--ignore 'apps/dashboard/src/contracts/generated/**' \
	--reporters console \
	"$@"
