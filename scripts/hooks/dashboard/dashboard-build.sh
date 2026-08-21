#!/usr/bin/env sh
# Production dashboard build; inactive until the dashboard manifest exists.
set -eu

cd "$(git rev-parse --show-toplevel)"
script_directory=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source-path=SCRIPTDIR
. "$script_directory/../quality-components.sh"

quality_dashboard_require 'the dashboard build' || exit 0

exec pnpm --dir apps/dashboard run build
