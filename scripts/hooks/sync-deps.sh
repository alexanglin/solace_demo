#!/usr/bin/env sh
# post-checkout / post-merge: resync environments when a lockfile changed.
#
# Without this, switching branches leaves you running yesterday's dependencies
# and produces failures that look like code defects.
#
# This hook must never fail a checkout, so every path exits 0.
set -u

cd "$(git rev-parse --show-toplevel)" || exit 0

# Prefer the exact transition pre-commit exposes. Git sets ORIG_HEAD for merges;
# the reflog is only the final fallback because unrelated commands can move it.
from_ref=${PRE_COMMIT_FROM_REF:-}
to_ref=${PRE_COMMIT_TO_REF:-HEAD}
if [ -n "$from_ref" ] && git rev-parse --verify --quiet "$from_ref^{tree}" >/dev/null; then
	changed=$(git diff --name-only "$from_ref" "$to_ref" 2>/dev/null || true)
elif git rev-parse --verify --quiet 'ORIG_HEAD^{tree}' >/dev/null; then
	changed=$(git diff --name-only ORIG_HEAD HEAD 2>/dev/null || true)
else
	changed=$(git diff --name-only 'HEAD@{1}' HEAD 2>/dev/null || true)
fi

case "$changed" in
*uv.lock* | *pyproject.toml*)
	if command -v uv >/dev/null 2>&1; then
		if [ -f pyproject.toml ]; then
			printf 'Syncing root environment...\n'
			# --all-packages, matching .github/workflows/checks.yml. `uv sync` is exact by
			# default, so without it every workspace member's editable install is pruned
			# and a member test can no longer import its own package.
			uv sync --all-packages --frozen ||
				printf 'WARN: root uv sync failed\n' >&2
		fi
		if [ -f agent-mesh/pyproject.toml ]; then
			printf 'Syncing agent-mesh environment...\n'
			uv sync --frozen --project agent-mesh || printf 'WARN: agent-mesh uv sync failed\n' >&2
		fi
	else
		printf 'WARN: uv is missing; Python environments were not synchronized\n' >&2
	fi
	;;
esac

case "$changed" in
*pnpm-lock.yaml* | *package.json*)
	if command -v pnpm >/dev/null 2>&1 && [ -f apps/dashboard/package.json ]; then
		printf 'Syncing dashboard dependencies...\n'
		pnpm --dir apps/dashboard install --frozen-lockfile ||
			printf 'WARN: pnpm install failed\n' >&2
	elif [ -f apps/dashboard/package.json ]; then
		printf 'WARN: pnpm is missing; dashboard dependencies were not synchronized\n' >&2
	fi
	;;
esac

exit 0
