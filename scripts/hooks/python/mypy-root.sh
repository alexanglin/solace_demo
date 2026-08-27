#!/usr/bin/env sh
# Type-check root-workspace paths with every active src-layout member as an import base.
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
[ -f uv.lock ] || {
	printf 'MISSING: uv.lock is required for root type checking\n' >&2
	exit 1
}
command -v uv >/dev/null 2>&1 || {
	printf 'MISSING: uv is not installed, so type checking cannot run\n' >&2
	exit 1
}

workspace_import_paths=
while IFS= read -r import_path; do
	[ -n "$import_path" ] || continue
	if [ -z "$workspace_import_paths" ]; then
		workspace_import_paths=$import_path
	else
		workspace_import_paths="$workspace_import_paths:$import_path"
	fi
done <<LISTING
$(quality_root_python_import_paths)
LISTING

# Do not inherit a caller-selected module search path into the verification authority.
MYPYPATH=$workspace_import_paths
export MYPYPATH
exec uv run --frozen mypy --strict "$@"
