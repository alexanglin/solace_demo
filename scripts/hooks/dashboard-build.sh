#!/usr/bin/env sh
# Production dashboard build; inactive until the dashboard manifest exists.
set -eu

cd "$(git rev-parse --show-toplevel)"
script_directory=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source-path=SCRIPTDIR
. "$script_directory/quality-components.sh"

quality_dashboard_active || exit 0
[ -f apps/dashboard/package.json ] || {
	printf 'MISSING: apps/dashboard/package.json is required by owned dashboard source\n' >&2
	exit 1
}
[ -f apps/dashboard/pnpm-lock.yaml ] || {
	printf 'MISSING: apps/dashboard/pnpm-lock.yaml is required for the dashboard build\n' >&2
	exit 1
}
command -v pnpm >/dev/null 2>&1 || {
	printf 'MISSING: pnpm is not installed, so the dashboard build cannot run\n' >&2
	exit 1
}

exec pnpm --dir apps/dashboard run build
