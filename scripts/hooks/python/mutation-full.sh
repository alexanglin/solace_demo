#!/usr/bin/env sh
# Independent mutation runs and scores for every tier-one workspace member.
set -eu

repository_root=$(git rev-parse --show-toplevel)
cd "$repository_root"
script_directory=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=scripts/hooks/quality-components.sh
. "$script_directory/../quality-components.sh"

quality_root_python_active || exit 0
[ -f pyproject.toml ] || {
	printf 'MISSING: pyproject.toml is required by owned root Python source\n' >&2
	exit 1
}
[ -f uv.lock ] || {
	printf 'MISSING: uv.lock is required for mutation testing\n' >&2
	exit 1
}
command -v uv >/dev/null 2>&1 || {
	printf 'MISSING: uv is not installed, so mutation testing cannot run\n' >&2
	exit 1
}

members=$(uv run --frozen python -m tools.mutation_gate --list-tier-one)
[ -n "$members" ] || {
	printf 'MISSING: no tier-one workspace member was discovered\n' >&2
	exit 1
}

printf '%s\n' "$members" | while IFS= read -r member; do
	uv run --frozen python -m tools.mutation_gate --preflight "$member"
	(
		cd "$member"
		uv run --project "$repository_root" --frozen mutmut run --max-children 4
	)
done

uv run --frozen python -m tools.mutation_gate --evaluate
