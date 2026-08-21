#!/usr/bin/env sh
# Fail when a pinned image digest is no longer the newest one its tag carries
# (docs/adr/0055-block-on-the-image-pin-not-on-advisories-inside-it.md).
#
# An advisory inside a third-party image is reported rather than enforced, because the only
# lever this project has on such an image is the digest it pins. This script is that lever's
# gate: for every pulled image in the inventory it asks the registry what the tag points at
# today and refuses a pin upstream has moved past. Built images are skipped -- their base is
# already in the inventory as a pulled reference.
#
# It launches docker (for the registry query), uv, and nothing else. The adjudication is in
# tools/image_pin_gate.py, which reads JSON and never reaches the network.
set -eu

cd "$(git rev-parse --show-toplevel)"
script_directory=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source-path=SCRIPTDIR
. "$script_directory/../hooks/quality-components.sh"

quality_deploy_stack_active || {
	printf 'no deploy/ stack to check\n'
	exit 0
}

command -v docker >/dev/null 2>&1 || {
	printf 'MISSING: docker is not installed, so image pins cannot be resolved\n' >&2
	exit 1
}
command -v uv >/dev/null 2>&1 || {
	printf 'MISSING: uv is not installed, so the pin gate cannot run\n' >&2
	exit 1
}
for required in pyproject.toml uv.lock tools/image_inventory.py tools/image_pin_gate.py; do
	[ -f "$required" ] || {
		printf 'MISSING: %s is required by the image pin check\n' "$required" >&2
		exit 1
	}
done

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

inventory=$(uv run --frozen python -m tools.image_inventory "$@") || {
	printf 'FAILED: the image inventory did not complete\n' >&2
	exit 1
}

work=$(mktemp -d "${TMPDIR:-/tmp}/aerial-rescue-image-pins.XXXXXX")
trap 'rm -rf "$work"' 0 1 2 15
report="$work/pins.json"

{
	printf '{"images":['
	separator=""
	while IFS=' ' read -r kind platform domain reference; do
		[ -n "$reference" ] || continue
		[ "$kind" = "pulled" ] || continue
		pinned=${reference##*@}
		case "$pinned" in
		sha256:*) ;;
		*) pinned="" ;;
		esac
		tag=${reference%@*}
		current=$(docker buildx imagetools inspect "$tag" --format '{{.Manifest.Digest}}' 2>/dev/null) || current=""
		printf '%s{"reference":"%s","repository":"%s","platform":"%s","pinned":"%s","current":"%s"}' \
			"$separator" "$reference" "${domain#image:}" "$platform" "$pinned" "$current"
		separator=","
	done <<INVENTORY
$inventory
INVENTORY
	printf ']}'
} >"$report"

exec uv run --frozen python -m tools.image_pin_gate --report "$report"
