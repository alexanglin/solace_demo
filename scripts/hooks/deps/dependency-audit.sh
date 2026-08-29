#!/usr/bin/env sh
# Audit the exact locked dependency graphs for known vulnerabilities.
set -eu

cd "$(git rev-parse --show-toplevel)"
script_directory=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source-path=SCRIPTDIR
. "$script_directory/../quality-components.sh"

root_active=$(quality_root_python_manifest_state)
agent_active=$(quality_agent_python_manifest_state)
dashboard_active=$(quality_dashboard_manifest_state)

if [ "$root_active" = false ] && [ "$agent_active" = false ] && [ "$dashboard_active" = false ]; then
	exit 0
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

# pip-audit exits 1 both when it reports advisories and when it rejects its own
# invocation, so the exit status alone cannot tell those apart. The written report is
# the oracle instead: the waiver gate fails closed when it is absent or unparsable, so
# an audit that never ran cannot be mistaken for a clean one. A status above 1 means
# pip-audit did not start and is refused here.
adjudicate() {
	audit_domain=$1
	audit_report=$2
	shift 2
	audit_status=0
	"$@" || audit_status=$?
	if [ "$audit_status" -gt 1 ]; then
		printf 'FAILED: pip-audit did not complete for %s (exit %s)\n' \
			"$audit_domain" "$audit_status" >&2
		exit 1
	fi
	uv run --frozen python -m tools.dependency_waiver_gate \
		--domain "$audit_domain" --report "$audit_report"
}

if [ "$root_active" = true ]; then
	root_requirements="$audit_dir/root-requirements.txt"
	uv export --frozen --all-packages --all-extras --all-groups \
		--no-emit-workspace --no-annotate --no-header --output-file "$root_requirements"
	root_report="$audit_dir/root-audit.json"
	adjudicate root "$root_report" \
		uv run --frozen pip-audit --strict --require-hashes --disable-pip \
		--progress-spinner off --timeout 15 --format json \
		--output "$root_report" --requirement "$root_requirements"
fi

if [ "$agent_active" = true ]; then
	[ -f agent-mesh/uv.lock ] || {
		printf 'MISSING: agent-mesh/uv.lock is required for dependency auditing\n' >&2
		exit 1
	}
	agent_requirements="$audit_dir/agent-mesh-requirements.txt"
	uv export --project agent-mesh --frozen --all-extras --all-groups \
		--no-emit-project --no-annotate --no-header --output-file "$agent_requirements"
	agent_report="$audit_dir/agent-mesh-audit.json"
	adjudicate agent-mesh "$agent_report" \
		uv run --project agent-mesh --frozen pip-audit --strict --require-hashes \
		--disable-pip --progress-spinner off --timeout 15 --format json \
		--output "$agent_report" --requirement "$agent_requirements"
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
