#!/usr/bin/env sh
# Scan every image the deploy/ stack pulls or builds with Trivy and adjudicate each report
# under the waiver registry (docs/adr/0048-scan-images-and-deploy-configuration-with-trivy.md).
#
# The security workflow runs this after `docker compose build` has produced the two derived
# images; `just scan-images` does the same locally. The inventory comes from
# tools/image_inventory.py, one line per image: `<kind> <platform or -> <domain> <reference>`.
# Pulled images are read from their registry by digest and built ones from the local Docker
# engine, each into its own JSON report that tools/dependency_waiver_gate.py adjudicates in
# the image's domain: a HIGH or CRITICAL finding with a fix blocks unless waived, everything
# else is printed as INFO. Trivy's exit status is ignored in favour of the written report;
# a scan that does not complete is refused. This script launches trivy and uv, never docker.
set -eu

cd "$(git rev-parse --show-toplevel)"
script_directory=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source-path=SCRIPTDIR
. "$script_directory/../hooks/quality-components.sh"

quality_deploy_stack_active || {
	printf 'no deploy/ stack to scan\n'
	exit 0
}

command -v trivy >/dev/null 2>&1 || {
	printf 'MISSING: trivy is not installed, so the image scan cannot run\n' >&2
	exit 1
}
command -v uv >/dev/null 2>&1 || {
	printf 'MISSING: uv is not installed, so the image scan cannot run\n' >&2
	exit 1
}
for required in pyproject.toml uv.lock tools/image_inventory.py tools/dependency_waiver_gate.py; do
	[ -f "$required" ] || {
		printf 'MISSING: %s is required by the image scan\n' "$required" >&2
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

work=$(mktemp -d "${TMPDIR:-/tmp}/aerial-rescue-image-scan.XXXXXX")
trap 'rm -rf "$work"' 0 1 2 15

status=0
index=0
while IFS=' ' read -r kind platform domain reference; do
	[ -n "$reference" ] || continue
	index=$((index + 1))
	report="$work/image-$index.json"
	set -- image --format json --output "$report" --exit-code 0 --no-progress --timeout 20m
	if [ "$platform" != "-" ]; then
		set -- "$@" --platform "$platform"
	fi
	if [ "$kind" = "built" ]; then
		set -- "$@" --image-src docker
	else
		set -- "$@" --image-src remote
	fi
	scan_status=0
	trivy "$@" "$reference" || scan_status=$?
	if [ "$scan_status" -ne 0 ]; then
		printf 'FAILED: trivy image did not complete for %s (exit %s)\n' "$reference" "$scan_status" >&2
		status=1
		continue
	fi
	uv run --frozen python -m tools.dependency_waiver_gate --source trivy \
		--domain "$domain" --report "$report" || status=1
done <<INVENTORY
$inventory
INVENTORY

exit "$status"
