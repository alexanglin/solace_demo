#!/usr/bin/env sh
# Render every Graphviz source under docs/architecture/ to PNG and record the
# source hash so check-diagrams.sh can detect staleness in a fresh clone.
#
# AGENTS.md §7 (Documentation and diagrams): commit both the editable source and generated PNG.
set -eu

cd "$(git rev-parse --show-toplevel)"

if ! command -v dot >/dev/null 2>&1; then
	printf 'Graphviz is required. Install it with: brew install graphviz\n' >&2
	exit 1
fi

hash_of() {
	if command -v shasum >/dev/null 2>&1; then
		shasum -a 256 "$1" | cut -d' ' -f1
	else
		sha256sum "$1" | cut -d' ' -f1
	fi
}

sources_file=$(mktemp "${TMPDIR:-/tmp}/aerial-rescue-diagrams.XXXXXX")
trap 'rm -f "$sources_file"' 0 1 2 15
find docs/architecture -type f -name '*.dot' -print | sort >"$sources_file"

if [ ! -s "$sources_file" ]; then
	printf 'No .dot sources found under docs/architecture/\n' >&2
	exit 0
fi

while IFS= read -r src; do
	png="${src%.dot}.png"
	dot -Tpng -Gdpi=150 "$src" -o "$png"
	{
		printf 'source %s\n' "$(hash_of "$src")"
		printf 'png %s\n' "$(hash_of "$png")"
	} >"$src.sha256"
	printf 'rendered %s -> %s\n' "$src" "$png"
done <"$sources_file"
