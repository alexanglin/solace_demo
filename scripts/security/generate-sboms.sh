#!/usr/bin/env sh
# Generate and validate one CycloneDX SBOM for every image in the supported stack.
# The caller supplies an empty output directory so CI can retain the documents as a
# reviewable artifact (docs/adr/0127, docs/adr/0130). No existing file is overwritten.
set -eu

cd "$(git rev-parse --show-toplevel)"
script_directory=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source-path=SCRIPTDIR
. "$script_directory/../hooks/quality-components.sh"

quality_deploy_stack_active || {
	printf 'no deploy/ stack to inventory\n'
	exit 0
}

[ "$#" -eq 1 ] || {
	printf 'USAGE: %s OUTPUT_DIRECTORY\n' "$0" >&2
	exit 2
}

command -v trivy >/dev/null 2>&1 || {
	printf 'MISSING: trivy is not installed, so image SBOMs cannot be generated\n' >&2
	exit 1
}
command -v uv >/dev/null 2>&1 || {
	printf 'MISSING: uv is not installed, so image SBOMs cannot be validated\n' >&2
	exit 1
}
for required in pyproject.toml uv.lock tools/image_inventory.py tools/sbom_gate.py; do
	[ -f "$required" ] || {
		printf 'MISSING: %s is required by SBOM generation\n' "$required" >&2
		exit 1
	}
done

output_directory=$1
if [ -e "$output_directory" ] && [ ! -d "$output_directory" ]; then
	printf 'REFUSED: SBOM output path exists and is not a directory\n' >&2
	exit 1
fi
if [ -d "$output_directory" ] && [ -n "$(find "$output_directory" -mindepth 1 -maxdepth 1 -print -quit)" ]; then
	printf 'REFUSED: SBOM output directory is not empty\n' >&2
	exit 1
fi
mkdir -p "$output_directory"

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

status=0
index=0
while IFS=' ' read -r kind platform _domain reference; do
	[ -n "$reference" ] || continue
	index=$((index + 1))
	ordinal=$(printf '%03d' "$index")
	report="$output_directory/image-$ordinal.cdx.json"
	set -- image --format cyclonedx --output "$report" --no-progress --timeout 20m
	if [ "$platform" != "-" ]; then
		set -- "$@" --platform "$platform"
	fi
	if [ "$kind" = "built" ]; then
		set -- "$@" --image-src docker
	else
		set -- "$@" --image-src remote
	fi
	generation_status=0
	trivy "$@" "$reference" || generation_status=$?
	if [ "$generation_status" -ne 0 ]; then
		printf 'FAILED: SBOM generation did not complete for image %s (exit %s)\n' \
			"$index" "$generation_status" >&2
		status=1
		continue
	fi
	if ! uv run --frozen python -m tools.sbom_gate \
		--report "$report" --expected-reference "$reference"; then
		printf 'FAILED: SBOM validation failed for image %s\n' "$index" >&2
		status=1
	fi
done <<INVENTORY
$inventory
INVENTORY

exit "$status"
