#!/usr/bin/env sh
# Check that committed dashboard contract types match their manifest-owned schemas.
set -eu

cd "$(git rev-parse --show-toplevel)"
script_directory=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source-path=SCRIPTDIR
. "$script_directory/../quality-components.sh"

quality_dashboard_require 'dashboard contract freshness' || exit 0

# A local verification hook must never let Corepack or pnpm resolve packages from
# the network. Generation is an explicit contributor action; this path only checks.
COREPACK_ENABLE_NETWORK=0
export COREPACK_ENABLE_NETWORK

exec pnpm --dir apps/dashboard run contracts:check
