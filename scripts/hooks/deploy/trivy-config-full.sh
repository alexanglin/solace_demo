#!/usr/bin/env sh
# Trivy misconfiguration audit over the Dockerfiles under deploy/
# (docs/adr/0048-scan-images-and-deploy-configuration-with-trivy.md).
#
# Inert until deploy/ holds a compose file or a Dockerfile in the tracked-or-unignored
# listing; from the first one it fails closed on a missing trivy, manifest, lockfile, uv,
# or gate module. Trivy's exit status is ignored in favour of the written report, which
# tools/dependency_waiver_gate.py adjudicates under the waiver registry with --source
# trivy: a HIGH or CRITICAL failed check blocks unless waived, everything else is printed
# as INFO. The generated secrets and certificates are skipped; they are never tracked.
# This script never runs Docker.
set -eu

cd "$(git rev-parse --show-toplevel)"
script_directory=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source-path=SCRIPTDIR
. "$script_directory/../quality-components.sh"

quality_deploy_stack_active || exit 0

command -v trivy >/dev/null 2>&1 || {
	printf 'MISSING: trivy is not installed, so the deploy misconfiguration audit cannot run\n' >&2
	exit 1
}
[ -f pyproject.toml ] || {
	printf 'MISSING: pyproject.toml is required by the deploy misconfiguration audit\n' >&2
	exit 1
}
[ -f uv.lock ] || {
	printf 'MISSING: uv.lock is required by the deploy misconfiguration audit\n' >&2
	exit 1
}
command -v uv >/dev/null 2>&1 || {
	printf 'MISSING: uv is not installed, so the deploy misconfiguration audit cannot run\n' >&2
	exit 1
}
[ -f tools/dependency_waiver_gate.py ] || {
	printf 'MISSING: tools/dependency_waiver_gate.py is required by the deploy misconfiguration audit\n' >&2
	exit 1
}

report=$(mktemp "${TMPDIR:-/tmp}/aerial-rescue-trivy-config.XXXXXX")
trap 'rm -f "$report"' 0 1 2 15

status=0
trivy config --format json --output "$report" --exit-code 0 --quiet \
	--skip-dirs secrets,certs deploy || status=$?
if [ "$status" -ne 0 ]; then
	printf 'FAILED: trivy config did not complete for deploy (exit %s)\n' "$status" >&2
	exit 1
fi

uv run --frozen python -m tools.dependency_waiver_gate --source trivy \
	--domain deploy-config --report "$report"
