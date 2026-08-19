#!/usr/bin/env sh
# Whitespace errors must not survive into a pushed commit.
#
# The commit-stage `git diff --check` inspects the working tree, so a whitespace error
# that was already committed slips past it. This checks the range actually being pushed.
#
# QUALITY_DIFF_BASE and QUALITY_DIFF_HEAD select the range in CI. pre-commit exposes
# PRE_COMMIT_FROM_REF and PRE_COMMIT_TO_REF during a normal pre-push. With neither
# pair set, the whole committed tree is checked from the empty tree through HEAD.
set -eu

cd "$(git rev-parse --show-toplevel)"

if [ -n "${QUALITY_DIFF_BASE:-}" ] || [ -n "${QUALITY_DIFF_HEAD:-}" ]; then
	base=${QUALITY_DIFF_BASE:-}
	head=${QUALITY_DIFF_HEAD:-}
else
	base=${PRE_COMMIT_FROM_REF:-}
	head=${PRE_COMMIT_TO_REF:-}
fi
zero=0000000000000000000000000000000000000000

if { [ -n "$base" ] && [ -z "$head" ]; } || { [ -z "$base" ] && [ -n "$head" ]; }; then
	printf 'Both range endpoints must be set (base=%s, head=%s)\n' "${base:-<unset>}" "${head:-<unset>}" >&2
	exit 2
fi

if [ "$head" = "$zero" ]; then
	# A deleted remote ref contains no new committed content.
	exit 0
fi

empty_tree=$(git hash-object -t tree /dev/null)

if [ -z "$base" ]; then
	base=$empty_tree
	head=HEAD
elif [ "$base" = "$zero" ]; then
	base=$empty_tree
fi

git rev-parse --verify --quiet "$base^{tree}" >/dev/null || {
	printf 'Invalid base revision for whitespace check: %s\n' "$base" >&2
	exit 2
}
git rev-parse --verify --quiet "$head^{tree}" >/dev/null || {
	printf 'Invalid head revision for whitespace check: %s\n' "$head" >&2
	exit 2
}

exec git -c core.whitespace=blank-at-eol,blank-at-eof,space-before-tab \
	diff --no-ext-diff --check "$base" "$head" --
