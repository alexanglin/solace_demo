#!/usr/bin/env sh
# Production probe gate: resolve the references the dashboard's production and soak harness
# embeds as string literals, through the pure gate docs/adr/0204 names.
#
# Inert while apps/dashboard holds no manifest and no TypeScript source; from the first one
# it fails closed on a missing Compose file, uv, or gate module. The enumeration lives here
# because docs/adr/0025 confines subprocess to four reviewed Python owners, and the gate only
# reads files: nothing it resolves is imported, and no container is started.
#
# The workspace source roots are passed in full rather than narrowed to the changed files.
# The change that breaks a probe reference is usually a deletion on the Python side, and that
# commit touches no harness file, so a narrowed run would miss exactly the case that matters.
set -eu

cd "$(git rev-parse --show-toplevel)"
script_directory=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source-path=SCRIPTDIR
. "$script_directory/../quality-components.sh"

quality_dashboard_active || exit 0

set --
while IFS= read -r file; do
	[ -n "$file" ] || continue
	case "$file" in
	*.ts | *.tsx) set -- "$@" --support "$file" ;;
	esac
done <<LISTING
$(git ls-files --cached --others --exclude-standard -- apps/dashboard/tests/production apps/dashboard/tests/soak)
LISTING

for source_root in packages/*/src services/*/src; do
	[ -d "$source_root" ] || continue
	set -- "$@" --source-root "$source_root"
done

[ -f deploy/compose.yaml ] || {
	printf 'MISSING: deploy/compose.yaml is required by the production probe gate\n' >&2
	exit 1
}
[ -f pyproject.toml ] || {
	printf 'MISSING: pyproject.toml is required by the production probe gate\n' >&2
	exit 1
}
[ -f uv.lock ] || {
	printf 'MISSING: uv.lock is required by the production probe gate\n' >&2
	exit 1
}
command -v uv >/dev/null 2>&1 || {
	printf 'MISSING: uv is not installed, so the production probe gate cannot run\n' >&2
	exit 1
}
[ -f tools/production_probe_gate.py ] || {
	printf 'MISSING: tools/production_probe_gate.py is required by the production probe gate\n' >&2
	exit 1
}

exec uv run --frozen python -m tools.production_probe_gate \
	--compose deploy/compose.yaml "$@"
