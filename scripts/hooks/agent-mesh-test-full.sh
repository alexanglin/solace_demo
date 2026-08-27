#!/usr/bin/env sh
# Agent Mesh compatibility and probe suite, on its own Python 3.13 interpreter.
#
# This enters agent-mesh/ rather than using `uv run --project agent-mesh`. The difference
# is load-bearing, not stylistic: --project does not change the working directory, and
# pytest is rooted at the working directory. Invoked from the repository root it would
# load the ROOT pytest configuration and collect tests, tools, packages and services under
# the 3.13 interpreter -- the mirror image of the hazard the root `testpaths` setting
# guards against (docs/adr/0029-verify-the-agent-mesh-domain-with-its-own-toolchain.md).
#
# Tests needing a broker, a model, or the network are excluded for the same reason the
# root suite excludes them: CI asserts no such credentials are configured.
set -eu

cd "$(git rev-parse --show-toplevel)"
script_directory=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source-path=SCRIPTDIR
. "$script_directory/quality-components.sh"

quality_agent_python_active || exit 0
[ -f agent-mesh/pyproject.toml ] || {
	printf 'MISSING: agent-mesh/pyproject.toml is required by owned Agent Mesh source\n' >&2
	exit 1
}
[ -f agent-mesh/uv.lock ] || {
	printf 'MISSING: agent-mesh/uv.lock is required for Agent Mesh tests\n' >&2
	exit 1
}
command -v uv >/dev/null 2>&1 || {
	printf 'MISSING: uv is not installed, so Agent Mesh tests cannot run\n' >&2
	exit 1
}
[ -d agent-mesh/tests ] || {
	printf 'MISSING: agent-mesh/tests is required by the Agent Mesh project\n' >&2
	exit 1
}
[ -d agent-mesh/aerial_rescue_event_mesh_gateway ] || {
	printf 'MISSING: agent-mesh/aerial_rescue_event_mesh_gateway is required by the Agent Mesh project\n' >&2
	exit 1
}
[ -d agent-mesh/aerial_rescue_runtime_compat ] || {
	printf 'MISSING: agent-mesh/aerial_rescue_runtime_compat is required by the Agent Mesh project\n' >&2
	exit 1
}

cd agent-mesh
exec uv run --frozen pytest \
	-m 'not broker and not ollama and not paid and not docker and not net' \
	-q --no-header \
	--cov=tools.agent_mesh_config_validator \
	--cov=aerial_rescue_event_mesh_gateway \
	--cov-branch \
	--cov-report=term-missing \
	--cov-fail-under=100
