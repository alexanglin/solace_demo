#!/usr/bin/env sh
# Dashboard browser acceptance: exact runtimes, an already-cached pinned Chromium,
# the complete package-owned Playwright suite, and a secret-safe retained-artifact scan.
set -eu

cd "$(git rev-parse --show-toplevel)"
script_directory=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source-path=SCRIPTDIR
. "$script_directory/../quality-components.sh"

quality_dashboard_require 'dashboard Playwright acceptance' || exit 0

command -v node >/dev/null 2>&1 || {
	printf 'MISSING: Node.js is not installed, so dashboard Playwright acceptance cannot run\n' >&2
	exit 1
}

expected_node_version=$(node --print "require('./apps/dashboard/package.json').engines.node") || {
	printf 'INVALID: apps/dashboard/package.json must declare engines.node\n' >&2
	exit 1
}
actual_node_version=$(node --version)
actual_node_version=${actual_node_version#v}
if [ "$actual_node_version" != "$expected_node_version" ]; then
	printf 'WRONG: dashboard Playwright acceptance requires Node.js %s, found %s\n' \
		"$expected_node_version" "$actual_node_version" >&2
	exit 1
fi

expected_pnpm_version=$(node --print "require('./apps/dashboard/package.json').engines.pnpm") || {
	printf 'INVALID: apps/dashboard/package.json must declare engines.pnpm\n' >&2
	exit 1
}
# A Corepack shim may otherwise fetch the requested package manager on first use. The
# local hook is check-only: setup is an explicit contributor or CI action.
COREPACK_ENABLE_NETWORK=0
export COREPACK_ENABLE_NETWORK
actual_pnpm_version=$(pnpm --version 2>/dev/null) || {
	printf 'MISSING: the manifest-pinned pnpm runtime is not available offline\n' >&2
	exit 1
}
if [ "$actual_pnpm_version" != "$expected_pnpm_version" ]; then
	printf 'WRONG: dashboard Playwright acceptance requires pnpm %s, found %s\n' \
		"$expected_pnpm_version" "$actual_pnpm_version" >&2
	exit 1
fi

# Playwright 1.62.1 resolves both Chromium executables at revision 1234. Listing the
# cache is read-only; local hooks never turn a missing browser into an implicit download.
chromium_revision=1234
browser_listing=$(pnpm --dir apps/dashboard exec playwright install --list 2>/dev/null) || {
	printf 'MISSING: the Playwright browser cache could not be inspected\n' >&2
	exit 1
}
chromium_cached=true
printf '%s\n' "$browser_listing" | grep -F -q "chromium-$chromium_revision" ||
	chromium_cached=false
printf '%s\n' "$browser_listing" | grep -F -q "chromium_headless_shell-$chromium_revision" ||
	chromium_cached=false
if [ "$chromium_cached" != true ]; then
	printf 'MISSING: Playwright Chromium revision %s is not cached; run explicitly: ' \
		"$chromium_revision" >&2
	printf 'pnpm --dir apps/dashboard exec playwright install chromium\n' >&2
	exit 1
fi

expected_test_count=$(node --print \
	"require('./apps/dashboard/package.json').config.playwrightExpectedTests") || {
	printf 'INVALID: apps/dashboard/package.json must declare config.playwrightExpectedTests\n' >&2
	exit 1
}
case "$expected_test_count" in
'' | *[!0-9]*)
	printf 'INVALID: dashboard Playwright expected-test inventory must be a positive integer\n' >&2
	exit 1
	;;
esac
if [ "$expected_test_count" -eq 0 ]; then
	printf 'INVALID: dashboard Playwright expected-test inventory must be a positive integer\n' >&2
	exit 1
fi
discovery_listing=$(PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \
	pnpm --dir apps/dashboard exec playwright test --list 2>/dev/null) || {
	printf 'ERROR: dashboard Playwright test discovery failed\n' >&2
	exit 1
}
discovered_test_count=$(printf '%s\n' "$discovery_listing" |
	sed -n 's/^Total: \([0-9][0-9]*\) tests in .*$/\1/p' |
	tail -n 1)
if [ "$discovered_test_count" != "$expected_test_count" ]; then
	printf 'WRONG: dashboard Playwright expected %s tests, discovered %s\n' \
		"$expected_test_count" "${discovered_test_count:-none}" >&2
	exit 1
fi

playwright_status=0
PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 pnpm --dir apps/dashboard run test:e2e || playwright_status=$?

artifact_status=0
artifact_leak=false
artifact_scan_failed=false
for artifact_directory in apps/dashboard/test-results apps/dashboard/playwright-report; do
	[ -d "$artifact_directory" ] || continue
	scan_status=0
	grep -R -q 'synthetic-browser-bearer-do-not-persist' "$artifact_directory" || scan_status=$?
	case "$scan_status" in
	0) artifact_leak=true ;;
	1) ;;
	*) artifact_scan_failed=true ;;
	esac
done
if [ "$artifact_leak" = true ]; then
	printf 'LEAK: dashboard Playwright artifacts contain the forbidden synthetic bearer sentinel\n' >&2
	artifact_status=1
fi
if [ "$artifact_scan_failed" = true ]; then
	printf 'ERROR: dashboard Playwright artifacts could not be scanned completely\n' >&2
	artifact_status=1
fi

# The browser result remains the primary status when it already failed; an artifact leak
# still emits its independent verdict. A passing browser run fails on either scan defect.
if [ "$playwright_status" -ne 0 ]; then
	exit "$playwright_status"
fi
exit "$artifact_status"
