#!/usr/bin/env sh
# Pre-push: verify EVERY diagram, not just the ones staged in this commit.
set -eu

cd "$(git rev-parse --show-toplevel)"
script_directory=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)

sources_file=$(mktemp "${TMPDIR:-/tmp}/aerial-rescue-diagrams.XXXXXX")
trap 'rm -f "$sources_file"' 0 1 2 15
find docs/architecture -type f -name '*.dot' -print | sort >"$sources_file"
[ -s "$sources_file" ] || exit 0

set --
while IFS= read -r source; do
	set -- "$@" "$source"
done <"$sources_file"

"$script_directory/check-diagrams.sh" "$@"
