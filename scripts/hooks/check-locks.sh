#!/usr/bin/env sh
# Lockfiles must match their manifests.
#
# This project has THREE dependency domains (see docs/adr/0004-split-python-runtimes.md
# and docs/adr/0010-uv-workspace-and-toolchain.md):
#   1. root uv workspace          -- application services, Python 3.14.x
#   2. agent-mesh/ uv project     -- Agent Mesh + plugins, Python 3.13.x
#   3. apps/dashboard             -- pnpm
#
# Each domain is skipped when its manifest does not exist yet, so this hook is
# inert on a greenfield tree and activates as each domain appears. But once a
# manifest exists, a missing tool is a FAILURE, not a skip: a quality gate must never
# pass because the tool that enforces it is absent from PATH.
set -eu

cd "$(git rev-parse --show-toplevel)"
script_directory=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source-path=SCRIPTDIR
. "$script_directory/quality-components.sh"

status=0

if quality_root_python_active && [ ! -f pyproject.toml ]; then
	printf 'MISSING: pyproject.toml is required by owned root Python source\n' >&2
	status=1
fi
if quality_agent_python_active && [ ! -f agent-mesh/pyproject.toml ]; then
	printf 'MISSING: agent-mesh/pyproject.toml is required by owned Agent Mesh source\n' >&2
	status=1
fi
if quality_dashboard_active && [ ! -f apps/dashboard/package.json ]; then
	printf 'MISSING: apps/dashboard/package.json is required by owned dashboard source\n' >&2
	status=1
fi

check_uv() {
	dir=$1
	label=$2
	[ -f "$dir/pyproject.toml" ] || return 0
	lockfile="$dir/uv.lock"
	if [ "$dir" = "." ]; then
		lockfile="uv.lock"
	fi
	if [ ! -f "$lockfile" ]; then
		printf 'MISSING: %s is required by %s\n' "$lockfile" "$label" >&2
		status=1
	fi
	if ! command -v uv >/dev/null 2>&1; then
		printf 'MISSING: uv is not installed, so %s cannot be verified\n' "$label" >&2
		status=1
		return 0
	fi
	[ -f "$lockfile" ] || return 0
	if ! uv lock --check --project "$dir" >/dev/null 2>&1; then
		printf 'STALE: %s lockfile is out of date -- run: uv lock --project %s\n' "$label" "$dir" >&2
		status=1
	fi
}

check_uv "." "root workspace"
check_uv "agent-mesh" "agent-mesh"

if [ -f "apps/dashboard/package.json" ]; then
	package_manager=$(tr -d '\n\r' <apps/dashboard/package.json |
		sed -nE 's/.*"packageManager"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/p')
	if ! printf '%s\n' "$package_manager" | grep -Eq '^pnpm@[0-9]+\.[0-9]+\.[0-9]+$'; then
		printf 'MISSING: apps/dashboard/package.json needs exact packageManager pnpm@X.Y.Z\n' >&2
		status=1
	fi
	if [ ! -f "apps/dashboard/pnpm-lock.yaml" ]; then
		printf 'MISSING: apps/dashboard/pnpm-lock.yaml is required by apps/dashboard/package.json\n' >&2
		status=1
	fi
	if ! command -v pnpm >/dev/null 2>&1; then
		printf 'MISSING: pnpm is not installed, so the dashboard lockfile cannot be verified\n' >&2
		status=1
	elif [ -f "apps/dashboard/pnpm-lock.yaml" ] && ! pnpm --dir apps/dashboard install --frozen-lockfile --lockfile-only --ignore-scripts >/dev/null 2>&1; then
		printf 'STALE: pnpm-lock.yaml is out of date -- run: pnpm install\n' >&2
		status=1
	fi
fi

exit "$status"
