#!/usr/bin/env sh
# Blocking documentation checks: factual regressions and broken internal links.
# Unquantified-language checking lives in the separate blocking docs-strict hook.
# Receives Markdown file paths as arguments.
set -eu

repo_root=$(git rev-parse --show-toplevel)
status=0

# --- 1. Factual regressions the review established as wrong -------------------
# There is no Solace Agent Mesh 2.x. Managed releases display as 1.x strings and
# the open-source line is 1.x.  See docs/adr/0001-self-hosted-open-source-agent-mesh.md
fact_pattern='agent[ -]mesh 2\.[0-9x]|\bsam 2\.x'
for file in "$@"; do
	if grep -nEiq "$fact_pattern" "$file"; then
		printf 'FACT: %s references a nonexistent "Agent Mesh 2.x"\n' "$file" >&2
		grep -nEi "$fact_pattern" "$file" | sed 's/^/    /' >&2
		status=1
	fi
done

# --- 2. Relative links must resolve ------------------------------------------
# External URLs and bare anchors are out of scope. grep -o extracts every link
# on a line, not just the last one; `|| true` keeps an empty result from
# tripping `set -e`.
targets=$(mktemp)
trap 'rm -f "$targets"' EXIT

for file in "$@"; do
	dir=$(dirname "$file")
	grep -oE '\]\([^)]+\)' "$file" 2>/dev/null |
		sed 's/^](//; s/)$//' |
		grep -vE '^(https?:|mailto:|#|<)' |
		sed 's/#.*$//' |
		grep -v '^[[:space:]]*$' >"$targets" 2>/dev/null || true

	while IFS= read -r target; do
		[ -n "$target" ] || continue
		case "$target" in
		/*) resolved="$repo_root$target" ;;
		*) resolved="$dir/$target" ;;
		esac
		if [ ! -e "$resolved" ]; then
			printf 'LINK: %s -> %s does not exist\n' "$file" "$target" >&2
			status=1
		fi
	done <"$targets"
done

# --- 3. ADR index integrity ---------------------------------------------------
# Every numbered ADR on disk must be listed in the index.
adr_dir="$repo_root/docs/adr"
index="$adr_dir/README.md"
if [ -f "$index" ]; then
	for adr in "$adr_dir"/[0-9][0-9][0-9][0-9]-*.md; do
		[ -e "$adr" ] || continue
		name=$(basename "$adr")
		[ "$name" = "0000-template.md" ] && continue
		if ! grep -qF "$name" "$index"; then
			printf 'ADR: %s is not listed in docs/adr/README.md\n' "$name" >&2
			status=1
		fi
	done
fi

exit "$status"
