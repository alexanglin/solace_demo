#!/usr/bin/env sh
# Directory fan-out gate: bound how many files one directory holds as immediate children.
#
# The enumeration lives here rather than inside the Python gate because
# docs/adr/0025-narrow-ruff-subprocess-waivers.md confines `subprocess` to four reviewed
# owners, and counting directory entries is not a reason to reopen that decision. The
# gate is therefore a pure function of the listing produced below and the reviewed
# registry (docs/adr/0033-bound-directory-fan-out.md).
#
# Unlike the component gates beside it, this one has no component to be inert for: every
# repository has directories, so it is always active and fails closed on a missing
# registry.
#
# `--cached --others --exclude-standard` is the same tracked-or-unignored scope the
# Arrange-Act-Assert checker uses. It includes a file that is staged but not yet
# committed, so the gate refuses the commit that crosses the limit rather than the one
# after it.
set -eu

cd "$(git rev-parse --show-toplevel)"

[ -f directory-fanout.toml ] || {
	printf 'MISSING: directory-fanout.toml is required by the directory fan-out gate\n' >&2
	exit 1
}

command -v uv >/dev/null 2>&1 || {
	printf 'MISSING: uv is not installed, so the directory fan-out gate cannot run\n' >&2
	exit 1
}

listing=$(mktemp "${TMPDIR:-/tmp}/aerial-rescue-fanout.XXXXXX")
trap 'rm -f "$listing"' 0 1 2 15
git ls-files -z --cached --others --exclude-standard >"$listing"

uv run --frozen python -m tools.directory_fanout_gate --paths-from "$listing"
