#!/usr/bin/env sh
# Complete deterministic dashboard integration inventory, separate from browser evidence.
set -eu

cd "$(git rev-parse --show-toplevel)"
script_directory=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source-path=SCRIPTDIR
. "$script_directory/../quality-components.sh"

quality_dashboard_require 'dashboard integration tests' || exit 0

exec pnpm --dir apps/dashboard run test:integration
