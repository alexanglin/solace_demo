#!/usr/bin/env sh
# Audit the exact locked dependency graphs for known vulnerabilities.
set -eu

cd "$(git rev-parse --show-toplevel)"
script_directory=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source-path=SCRIPTDIR
. "$script_directory/quality-components.sh"

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

if [ "$root_active" = true ] || [ "$agent_active" = true ]; then
	command -v uv >/dev/null 2>&1 || {
		printf 'MISSING: uv is not installed, so dependency auditing cannot run\n' >&2
		exit 1
	}
fi

if [ "$root_active" = true ]; then
	[ -f uv.lock ] || {
		printf 'MISSING: uv.lock is required for dependency auditing\n' >&2
		exit 1
	}
fi

audit_dir=$(mktemp -d "${TMPDIR:-/tmp}/aerial-rescue-audit.XXXXXX")
trap 'rm -rf "$audit_dir"' 0 1 2 15

if [ "$root_active" = true ]; then
	root_requirements="$audit_dir/root-requirements.txt"
	uv export --frozen --all-packages --all-extras --all-groups \
		--no-emit-workspace --no-annotate --no-header --output-file "$root_requirements"
	uv run --frozen pip-audit --strict --require-hashes --disable-pip \
		--progress-spinner off --timeout 15 --requirement "$root_requirements"
fi

if [ "$agent_active" = true ]; then
	[ -f agent-mesh/uv.lock ] || {
		printf 'MISSING: agent-mesh/uv.lock is required for dependency auditing\n' >&2
		exit 1
	}
	agent_requirements="$audit_dir/agent-mesh-requirements.txt"
	uv export --project agent-mesh --frozen --all-extras --all-groups \
		--no-emit-project --no-annotate --no-header --output-file "$agent_requirements"
	uv run --project agent-mesh --frozen pip-audit --strict --require-hashes --disable-pip \
		--progress-spinner off --timeout 15 --requirement "$agent_requirements"
fi

if [ "$dashboard_active" = true ]; then
	[ -f apps/dashboard/pnpm-lock.yaml ] || {
		printf 'MISSING: apps/dashboard/pnpm-lock.yaml is required for dependency auditing\n' >&2
		exit 1
	}
	command -v pnpm >/dev/null 2>&1 || {
		printf 'MISSING: pnpm is not installed, so dashboard dependencies cannot be audited\n' >&2
		exit 1
	}
	pnpm --dir apps/dashboard audit --prod --audit-level high
fi
