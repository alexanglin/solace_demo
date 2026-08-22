#!/usr/bin/env sh
# Fast commit-stage tests: run the tests the staged change affects.
#
# The dependency map the previous comment here waited for now exists. `tools/affected_tests`
# builds it from the owned tree and prints the test files the staged paths reach, so this
# stage costs what the change costs rather than what the tree costs
# (docs/adr/0066-select-commit-stage-tests-from-an-import-graph.md).
#
# The enumeration lives here rather than inside the selector for the same reason it lives in
# directory-fanout.sh: docs/adr/0025-narrow-ruff-subprocess-waivers.md confines `subprocess`
# to four reviewed owners, and building a listing is not a reason to reopen that decision.
# `--cached --others --exclude-standard` is the tracked-or-unignored scope the other gates
# use, so a staged-but-uncommitted file is part of the graph.
#
# The selector fails safe: it prints `:all:` when a staged path cannot be narrowed, and this
# script then runs the whole deterministic suite. `-x` stops at the first failure because
# this stage is feedback, not a report. The full suite with coverage runs at pre-push.
#
# Inert until the root uv project exists.
set -eu

cd "$(git rev-parse --show-toplevel)"
script_directory=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source-path=SCRIPTDIR
. "$script_directory/../quality-components.sh"

quality_root_python_active || exit 0
[ -f pyproject.toml ] || {
	printf 'MISSING: pyproject.toml is required by owned root Python source\n' >&2
	exit 1
}
command -v uv >/dev/null 2>&1 || {
	printf 'MISSING: uv is not installed, so commit-stage tests cannot run\n' >&2
	exit 1
}
[ -f uv.lock ] || {
	printf 'MISSING: uv.lock is required for commit-stage tests\n' >&2
	exit 1
}

EXCLUDE='not broker and not ollama and not paid and not docker and not net'

listing=$(mktemp "${TMPDIR:-/tmp}/aerial-rescue-affected-listing.XXXXXX")
selected=$(mktemp "${TMPDIR:-/tmp}/aerial-rescue-affected-selected.XXXXXX")
trap 'rm -f "$listing" "$selected"' 0 1 2 15

# The Agent Mesh tree is excluded from the graph, not merely from the trigger: it carries
# its own `tools` package, so including it would make that module name ambiguous and the
# selector would widen every change to it (docs/adr/0062, docs/adr/0029).
git ls-files -z --cached --others --exclude-standard \
	-- '*.py' '*.pyi' ':(exclude)agent-mesh/*' >"$listing"

uv run --frozen python -m tools.affected_tests \
	--root . --paths-from "$listing" -- "$@" >"$selected"

if [ ! -s "$selected" ]; then
	printf 'affected tests: none reachable from the staged change\n'
	exit 0
fi

# Selection is by import graph and exclusion is by marker, and the two are independent: a
# change that touches only tests carrying an excluded marker -- every tests/phase0/*_live.py
# file, for instance -- selects files in which nothing is collectable. pytest reports that as
# exit code 5, which is not a failure of this stage. Every other status is propagated.
run_selected() {
	set +e
	uv run --frozen pytest -m "$EXCLUDE" -q -x --no-header -p no:cacheprovider "$@"
	status=$?
	set -e
	if [ "$status" -eq 5 ]; then
		printf 'affected tests: every selected test carries an excluded marker\n'
		return 0
	fi
	return "$status"
}

if [ "$(cat "$selected")" = ':all:' ]; then
	run_selected
	exit $?
fi

set --
while IFS= read -r selection; do
	set -- "$@" "$selection"
done <"$selected"

run_selected "$@"
exit $?
