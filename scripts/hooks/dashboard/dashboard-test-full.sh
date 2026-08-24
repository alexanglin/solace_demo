#!/usr/bin/env sh
# Full dashboard unit/component suite with all four coverage dimensions enforced.
set -eu

cd "$(git rev-parse --show-toplevel)"
script_directory=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source-path=SCRIPTDIR
. "$script_directory/../quality-components.sh"

quality_dashboard_require 'dashboard tests' || exit 0

command -v uv >/dev/null 2>&1 || {
	printf 'MISSING: uv is not installed, so dashboard coverage cannot be adjudicated\n' >&2
	exit 1
}

# ADR-0103 requires the untrusted report path itself to remain inside the
# dashboard boundary. Use an absolute path because pnpm changes into the package.
dashboard_root=$(CDPATH='' cd -- apps/dashboard && pwd)
evidence_parent="$dashboard_root/coverage"
evidence_parent_created=0
if [ -L "$evidence_parent" ]; then
	printf 'INVALID: dashboard coverage parent may not be a symbolic link\n' >&2
	exit 1
fi
if [ ! -d "$evidence_parent" ]; then
	mkdir "$evidence_parent"
	evidence_parent_created=1
fi
evidence_directory=''
cleanup() {
	case "$evidence_directory" in
	"$evidence_parent"/gate.*) rm -rf "$evidence_directory" ;;
	esac
	if [ "$evidence_parent_created" -eq 1 ]; then
		rmdir "$evidence_parent" 2>/dev/null || :
	fi
}
trap cleanup EXIT HUP INT TERM
evidence_directory=$(mktemp -d "$evidence_parent/gate.XXXXXX")
case "$evidence_directory" in
"$evidence_parent"/gate.*) ;;
*)
	printf 'INVALID: mktemp returned an unexpected dashboard coverage path\n' >&2
	evidence_directory=''
	exit 1
	;;
esac

coverage_status=0
pnpm --dir apps/dashboard run test:coverage \
	--coverage.reportsDirectory="$evidence_directory" || coverage_status=$?
if [ "$coverage_status" -ne 0 ]; then
	exit "$coverage_status"
fi

coverage_report="$evidence_directory/coverage-summary.json"
[ -f "$coverage_report" ] || {
	printf 'MISSING: dashboard coverage summary was not generated\n' >&2
	exit 1
}

source_inventory="$evidence_directory/source-inventory.bin"
git ls-files -z --cached --others --exclude-standard -- \
	'apps/dashboard/src/*.ts' \
	'apps/dashboard/src/*.tsx' \
	'apps/dashboard/src/*.js' \
	'apps/dashboard/src/*.jsx' \
	'apps/dashboard/src/*.mts' \
	'apps/dashboard/src/*.cts' \
	'apps/dashboard/src/*.mjs' \
	'apps/dashboard/src/*.cjs' >"$source_inventory"

set -- run --frozen python -m tools.typescript_coverage_gate \
	--report "$coverage_report" --dashboard-root apps/dashboard \
	--source-inventory "$source_inventory"

uv "$@"
