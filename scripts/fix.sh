#!/usr/bin/env sh
# Apply every automatic fix. Hooks are check-only by design (docs/adr/0012), so
# this is the explicit, opt-in path that modifies files.
set -eu

cd "$(git rev-parse --show-toplevel)"

run() {
	printf '\n>>> %s\n' "$*"
	"$@" || printf 'WARN: %s failed\n' "$*" >&2
}

if command -v uv >/dev/null 2>&1 && [ -f pyproject.toml ]; then
	run uv run --frozen ruff format .
	run uv run --frozen ruff check --fix .
fi

if command -v pnpm >/dev/null 2>&1 && [ -f apps/dashboard/package.json ]; then
	run pnpm --dir apps/dashboard exec prettier --write .
	run pnpm --dir apps/dashboard exec eslint --fix .
fi

run pre-commit run markdownlint-cli2 --all-files
run scripts/diagrams.sh

printf '\nDone. Review the changes before staging them.\n'
