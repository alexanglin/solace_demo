#!/usr/bin/env sh
# TypeScript policy gate: hold apps/dashboard to the baseline docs/adr/0057 fixes, through
# the pure gate that record names.
#
# Inert while apps/dashboard holds no manifest and no TypeScript source; from the first one
# it fails closed on a missing manifest, uv, or gate module. The enumeration lives here
# because docs/adr/0025 confines subprocess to four reviewed Python owners, and the gate
# only parses files: Node never enters the commit path, and no package is ever resolved.
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
	*/tsconfig.json | */tsconfig.*.json) set -- "$@" --tsconfig "$file" ;;
	*.ts | *.tsx) set -- "$@" --source "$file" ;;
	esac
done <<LISTING
$(git ls-files --cached --others --exclude-standard -- apps/dashboard)
LISTING

[ -f apps/dashboard/package.json ] || {
	printf 'MISSING: apps/dashboard/package.json is required by owned dashboard source\n' >&2
	exit 1
}
[ -f pyproject.toml ] || {
	printf 'MISSING: pyproject.toml is required by the TypeScript policy gate\n' >&2
	exit 1
}
[ -f uv.lock ] || {
	printf 'MISSING: uv.lock is required by the TypeScript policy gate\n' >&2
	exit 1
}
command -v uv >/dev/null 2>&1 || {
	printf 'MISSING: uv is not installed, so the TypeScript policy gate cannot run\n' >&2
	exit 1
}
[ -f tools/typescript_policy_gate.py ] || {
	printf 'MISSING: tools/typescript_policy_gate.py is required by the TypeScript policy gate\n' >&2
	exit 1
}

exec uv run --frozen python -m tools.typescript_policy_gate \
	--package-json apps/dashboard/package.json "$@"
