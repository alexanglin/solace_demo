#!/usr/bin/env sh
# Whole-project dashboard type checking.
#
# TypeScript has no meaningful single-file mode: checking one staged file gives a
# different answer than checking the project, and a project-reference graph resolves
# only as a whole. There is nothing to approximate, so the commit-stage hook runs THIS
# SAME script on a trigger pattern and pre-push runs it unconditionally
# (docs/adr/0057-typescript-strictness-baseline-before-the-dashboard.md).
#
# `run typecheck` rather than `exec tsc --noEmit`: the package script is the one place
# that composes tsc over the project-reference set, so a hook calling tsc directly would
# silently check only the default configuration once references exist.
set -eu

cd "$(git rev-parse --show-toplevel)"
script_directory=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source-path=SCRIPTDIR
. "$script_directory/../quality-components.sh"

quality_dashboard_require 'dashboard type checking' || exit 0

exec pnpm --dir apps/dashboard run typecheck
