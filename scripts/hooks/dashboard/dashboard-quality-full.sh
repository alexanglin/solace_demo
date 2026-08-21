#!/usr/bin/env sh
# Whole-tree dashboard linting and formatting, the counterpart of python-quality-full.sh.
#
# The commit-stage eslint and prettier hooks see only staged files. This is the run that
# covers the tree, so a commit whose staged paths miss those patterns cannot skip them
# (docs/adr/0057-typescript-strictness-baseline-before-the-dashboard.md).
set -eu

cd "$(git rev-parse --show-toplevel)"
script_directory=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source-path=SCRIPTDIR
. "$script_directory/../quality-components.sh"

quality_dashboard_require 'whole-tree dashboard quality checks' || exit 0

# Both legs run and both verdicts are reported, rather than the first failure masking the
# second, exactly as python-quality-full.sh accumulates its status.
status=0
pnpm --dir apps/dashboard run lint || status=1
pnpm --dir apps/dashboard run format:check || status=1
exit "$status"
