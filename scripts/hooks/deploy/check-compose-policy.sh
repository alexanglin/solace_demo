#!/usr/bin/env sh
# Compose policy gate: hold the deploy/ stack to docs/adr/0044 through the pure gate that
# docs/adr/0045 records.
#
# Inert while the tracked-or-unignored listing of deploy/ holds no compose file or
# Dockerfile; from the first one it fails closed on a missing environment template,
# manifest, lockfile, uv, or gate module. The enumeration lives here because
# docs/adr/0025 confines subprocess to four reviewed Python owners, and the gate only
# parses files: Docker never enters the commit path.
#
# The basenames selected are exactly the ones the check-compose-spec and hadolint hooks
# lint, so every file those hooks see is also held to the policy.
set -eu

cd "$(git rev-parse --show-toplevel)"

[ -d deploy ] || exit 0

set --
while IFS= read -r file; do
	[ -n "$file" ] || continue
	case "$(basename "$file")" in
	compose.yaml | compose.yml | compose.*.yaml | compose.*.yml | docker-compose.yaml | docker-compose.yml | docker-compose.*.yaml | docker-compose.*.yml)
		set -- "$@" --compose "$file"
		;;
	Dockerfile | Dockerfile.*)
		set -- "$@" --dockerfile "$file"
		;;
	esac
done <<LISTING
$(git ls-files --cached --others --exclude-standard -- deploy)
LISTING

[ "$#" -gt 0 ] || exit 0

[ -f .env.example ] || {
	printf 'MISSING: .env.example is required by the compose policy gate\n' >&2
	exit 1
}
[ -f pyproject.toml ] || {
	printf 'MISSING: pyproject.toml is required by the compose policy gate\n' >&2
	exit 1
}
[ -f uv.lock ] || {
	printf 'MISSING: uv.lock is required by the compose policy gate\n' >&2
	exit 1
}
command -v uv >/dev/null 2>&1 || {
	printf 'MISSING: uv is not installed, so the compose policy gate cannot run\n' >&2
	exit 1
}
[ -f tools/compose_policy_gate.py ] || {
	printf 'MISSING: tools/compose_policy_gate.py is required by the compose policy gate\n' >&2
	exit 1
}

exec uv run --frozen python -m tools.compose_policy_gate --env-template .env.example "$@"
