#!/usr/bin/env sh
# Validate every commit message in the pushed/CI range with the canonical hook.
set -eu

cd "$(git rev-parse --show-toplevel)"

command -v pre-commit >/dev/null 2>&1 || {
	printf 'MISSING: pre-commit is required to validate commit messages\n' >&2
	exit 1
}

if [ -n "${QUALITY_DIFF_BASE:-}" ] || [ -n "${QUALITY_DIFF_HEAD:-}" ]; then
	base=${QUALITY_DIFF_BASE:-}
	head=${QUALITY_DIFF_HEAD:-}
	remote=${QUALITY_DIFF_REMOTE_NAME:-}
else
	base=${PRE_COMMIT_FROM_REF:-}
	head=${PRE_COMMIT_TO_REF:-}
	remote=${PRE_COMMIT_REMOTE_NAME:-}
fi
zero=0000000000000000000000000000000000000000

if { [ -n "$base" ] && [ -z "$head" ]; } || { [ -z "$base" ] && [ -n "$head" ]; }; then
	printf 'Both commit-message range endpoints must be set\n' >&2
	exit 2
fi

if [ "$head" = "$zero" ]; then
	exit 0
fi

if [ -z "$head" ]; then
	head=HEAD
fi

git rev-parse --verify --quiet "$head^{commit}" >/dev/null || {
	printf 'Invalid head revision for commit-message check: %s\n' "$head" >&2
	exit 2
}

if [ -z "$base" ]; then
	commits=$(git rev-list --max-count=1 "$head")
elif [ "$base" = "$zero" ]; then
	# Anchor on the target branch itself, not on "any ref this clone happens to
	# hold". A CI checkout can carry remote refs that cover none of this branch's
	# ancestry, and --remotes then excludes nothing: the walk reaches the root
	# commit and fails on history written before Conventional Commits.
	target=
	if [ -n "$remote" ]; then
		for candidate in "refs/remotes/$remote/HEAD" "refs/remotes/$remote/main"; do
			if git rev-parse --verify --quiet "$candidate^{commit}" >/dev/null; then
				target=$candidate
				break
			fi
		done
	fi
	if [ -n "$target" ]; then
		commits=$(git rev-list --reverse "$head" --not "$target")
	else
		# With no target-remote history there is no sound way to distinguish new
		# commits from inherited local history. Validate HEAD rather than silently
		# accepting the complete ancestry or blocking on unrelated legacy commits.
		commits=$(git rev-list --max-count=1 "$head")
	fi
else
	git rev-parse --verify --quiet "$base^{commit}" >/dev/null || {
		printf 'Invalid base revision for commit-message check: %s\n' "$base" >&2
		exit 2
	}
	commits=$(git rev-list --reverse "$base..$head")
fi

# Say which range was resolved and how large it is. A gate that validates the
# wrong range reports "[Bad commit message] >> Initial commit" and nothing about
# where that commit came from, which is indistinguishable from a genuinely bad
# message until you can see the endpoints it used.
printf 'commit-message range: base=%s head=%s remote=%s commits=%s\n' \
	"${base:-<unset>}" "${head:-<unset>}" "${remote:-<unset>}" \
	"$(printf '%s\n' "$commits" | grep -c .)" >&2

message_dir=$(mktemp -d "${TMPDIR:-/tmp}/aerial-rescue-messages.XXXXXX")
trap 'rm -rf "$message_dir"' 0 1 2 15
status=0

for commit in $commits; do
	message_file="$message_dir/$commit.txt"
	git show --no-patch --format=%B "$commit" >"$message_file"
	if ! pre-commit run conventional-pre-commit --hook-stage commit-msg \
		--commit-msg-filename "$message_file"; then
		printf 'Invalid commit message in %s\n' "$commit" >&2
		status=1
	fi
done

exit "$status"
