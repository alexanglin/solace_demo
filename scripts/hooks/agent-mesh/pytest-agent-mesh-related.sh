#!/usr/bin/env sh
# Fast commit-stage tests for the Agent Mesh domain: run the tests the change affects.
#
# Before this stage existed, a commit touching only this domain ran no test at all: the root
# commit-stage hook carries `exclude: ^agent-mesh/`, and the domain's own suite ran solely at
# pre-push (docs/adr/0066-select-commit-stage-tests-from-an-import-graph.md).
#
# Selection and execution are deliberately split across the two toolchains. The selector is
# the root project's, because it is pure text analysis over source and parsing 3.13 syntax
# under 3.14 is not "verifying the domain". Execution stays inside agent-mesh/ under its own
# interpreter, which is what
# docs/adr/0029-verify-the-agent-mesh-domain-with-its-own-toolchain.md requires, and for the
# same working-directory reason docs/adr/0062 records: pytest is rooted at the working
# directory, so `uv run --project` is not equivalent here.
#
# Every path is made relative to agent-mesh/ before selection, so the domain's `tools`
# package resolves as its own rather than colliding with the root package of that name.
#
# Inert until the Agent Mesh project exists.
set -eu

cd "$(git rev-parse --show-toplevel)"
script_directory=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source-path=SCRIPTDIR
. "$script_directory/../quality-components.sh"

agent_active=$(quality_agent_python_manifest_state)
[ "$agent_active" = true ] || exit 0
[ -f agent-mesh/uv.lock ] || {
	printf 'MISSING: agent-mesh/uv.lock is required for Agent Mesh commit-stage tests\n' >&2
	exit 1
}
command -v uv >/dev/null 2>&1 || {
	printf 'MISSING: uv is not installed, so Agent Mesh commit-stage tests cannot run\n' >&2
	exit 1
}
[ -d agent-mesh/tests ] || {
	printf 'MISSING: agent-mesh/tests is required by the Agent Mesh project\n' >&2
	exit 1
}

EXCLUDE='not broker and not ollama and not paid and not docker and not net'

listing=$(mktemp "${TMPDIR:-/tmp}/aerial-rescue-mesh-listing.XXXXXX")
changed=$(mktemp "${TMPDIR:-/tmp}/aerial-rescue-mesh-changed.XXXXXX")
selected=$(mktemp "${TMPDIR:-/tmp}/aerial-rescue-mesh-selected.XXXXXX")
trap 'rm -f "$listing" "$changed" "$selected"' 0 1 2 15

(cd agent-mesh && git ls-files -z --cached --others --exclude-standard -- '*.py' '*.pyi') >"$listing"

: >"$changed"
for path in "$@"; do
	case "$path" in
	agent-mesh/*) printf '%s\n' "${path#agent-mesh/}" >>"$changed" ;;
	esac
done

set --
while IFS= read -r entry; do
	set -- "$@" "$entry"
done <"$changed"

uv run --frozen python -m tools.affected_tests \
	--root agent-mesh --paths-from "$listing" -- "$@" >"$selected"

if [ ! -s "$selected" ]; then
	printf 'affected Agent Mesh tests: none reachable from the staged change\n'
	exit 0
fi

cd agent-mesh

if [ "$(cat "$selected")" = ':all:' ]; then
	exec uv run --frozen pytest -m "$EXCLUDE" -q -x --no-header -p no:cacheprovider
fi

set --
while IFS= read -r selection; do
	set -- "$@" "$selection"
done <"$selected"

exec uv run --frozen pytest -m "$EXCLUDE" -q -x --no-header -p no:cacheprovider "$@"
