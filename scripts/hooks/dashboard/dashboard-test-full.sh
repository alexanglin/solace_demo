#!/usr/bin/env sh
# Full dashboard unit/component suite with all four coverage dimensions enforced.
set -eu

cd "$(git rev-parse --show-toplevel)"
script_directory=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source-path=SCRIPTDIR
. "$script_directory/../quality-components.sh"

quality_dashboard_require 'dashboard tests' || exit 0

exec pnpm --dir apps/dashboard run test:coverage
