#!/usr/bin/env sh
# Full unit suite with PER-MEMBER coverage gates.
#
# Coverage is enforced per workspace member, not as one global total: a single
# --cov-fail-under would let a well-tested domain package mask an untested adapter,
# which is the exact outcome the gates exist to prevent (docs/adr/0010, docs/adr/0015).
# tools/coverage_gate.py reads each member's declared risk tier and applies its
# threshold; a member with no declared tier or no measured source fails rather than
# defaulting to the weakest tier or passing vacuously (docs/adr/0017), and a scaffold
# with nothing to measure is reported as SCAFFOLD, not failed (docs/adr/0053).
#
# Tests needing a broker, a model, Docker, or the network are excluded: CI asserts no
# such credentials are configured, so they cannot be part of the blocking suite.
set -eu

cd "$(git rev-parse --show-toplevel)"
script_directory=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source-path=SCRIPTDIR
. "$script_directory/../quality-components.sh"

root_active=$(quality_root_python_manifest_state)
[ "$root_active" = true ] || exit 0

if ! command -v uv >/dev/null 2>&1; then
	printf 'MISSING: uv is not installed, so the test and coverage gate cannot run\n' >&2
	exit 1
fi

EXCLUDE='not broker and not ollama and not paid and not docker and not net'

coverage_file=$(mktemp "${TMPDIR:-/tmp}/aerial-rescue-coverage.XXXXXX")
rm -f "$coverage_file"
trap 'rm -f "$coverage_file"' 0 1 2 15
export COVERAGE_FILE="$coverage_file"

cov_args=' --cov=tools'
for member in packages/*/ services/*/; do
	[ -d "${member}src" ] || continue
	cov_args="$cov_args --cov=${member}src"
done

# shellcheck disable=SC2086
uv run --frozen pytest -m "$EXCLUDE" -q --no-header \
	$cov_args --cov-branch --cov-report= "$@"

uv run --frozen python -m tools.coverage_gate
