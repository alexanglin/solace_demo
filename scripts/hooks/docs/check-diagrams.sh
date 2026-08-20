#!/usr/bin/env sh
# Diagram freshness. AGENTS.md §7 (Documentation and diagrams) requires an editable source AND a generated
# PNG, both committed.
#
# Diagrams are Graphviz (.dot), rendered with `dot -Tpng`.
#
# Freshness is decided by a content hash, not by mtime: git does not preserve
# modification times, so a fresh clone would report every diagram as stale.
# Each <name>.dot has a <name>.dot.sha256 sidecar recording both the source and
# PNG hashes at render time. `just diagrams` regenerates all three together.
set -eu

status=0

hash_of() {
	if command -v shasum >/dev/null 2>&1; then
		shasum -a 256 "$1" | cut -d' ' -f1
	else
		sha256sum "$1" | cut -d' ' -f1
	fi
}

for src in "$@"; do
	case "$src" in
	*.dot) ;;
	*.dot.sha256) src=${src%.sha256} ;;
	*.png) src=${src%.png}.dot ;;
	*) continue ;;
	esac

	if [ ! -f "$src" ]; then
		printf 'MISSING: generated diagram artifact has no editable source %s\n' "$src" >&2
		status=1
		continue
	fi

	base=${src%.dot}
	png="$base.png"
	sidecar="$src.sha256"

	if [ ! -f "$png" ]; then
		printf 'MISSING: %s has no generated %s\n' "$src" "$png" >&2
		status=1
		continue
	fi

	if [ ! -f "$sidecar" ]; then
		printf 'MISSING: %s has no %s -- run: just diagrams\n' "$src" "$sidecar" >&2
		status=1
		continue
	fi

	signature=$(od -An -tx1 -N8 "$png" | tr -d ' \n')
	if [ "$signature" != '89504e470d0a1a0a' ]; then
		printf 'INVALID PNG: %s is empty or lacks the PNG signature\n' "$png" >&2
		status=1
		continue
	fi

	actual_source=$(hash_of "$src")
	actual_png=$(hash_of "$png")
	recorded_source=$(awk '$1 == "source" { print $2 }' "$sidecar")
	recorded_png=$(awk '$1 == "png" { print $2 }' "$sidecar")

	if [ -z "$recorded_source" ] || [ "$actual_source" != "$recorded_source" ]; then
		printf 'STALE: %s changed but %s was not regenerated\n' "$src" "$png" >&2
		status=1
	fi
	if [ -z "$recorded_png" ] || [ "$actual_png" != "$recorded_png" ]; then
		printf 'STALE PNG: %s changed after it was generated from %s\n' "$png" "$src" >&2
		status=1
	fi
done

if [ "$status" -ne 0 ]; then
	printf '\nRun "just diagrams" to regenerate PNGs and refresh hashes.\n' >&2
fi

exit "$status"
