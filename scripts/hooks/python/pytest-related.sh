#!/usr/bin/env sh
# Fast commit-stage tests: run the deterministic root suite when Python changes.
# A source-to-test dependency map does not exist yet, so path guessing would silently
# miss shared-contract and tooling consumers. The selector can become narrower only
# after a project-owned dependency map proves that every owned module maps to tests.
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

exec uv run --frozen pytest \
	-m 'not broker and not ollama and not paid and not docker and not net' \
	-q -x --no-header -p no:cacheprovider
