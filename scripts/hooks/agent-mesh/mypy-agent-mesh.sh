#!/usr/bin/env sh
# Type-check the Agent Mesh domain on its own interpreter, from its own directory.
#
# The working directory is the point. mypy maps a file to a module name by walking up
# while __init__.py exists, relative to where it runs; from the repository root
# `agent-mesh/tools/agent_mesh_config_validator.py` collides with the root `tools` package
# and `from tools import agent_mesh_config_validator` does not resolve. Running from
# agent-mesh/ makes it resolve, and checking the whole tree rather than the staged subset
# is what keeps this stage's verdict equal to the pre-push one
# (docs/adr/0029-verify-the-agent-mesh-domain-with-its-own-toolchain.md,
# docs/adr/0062-type-check-the-agent-mesh-domain-from-its-own-directory.md).
#
# Inert when the domain has no manifest; fails closed afterwards.
set -eu

cd "$(git rev-parse --show-toplevel)"
[ -f agent-mesh/pyproject.toml ] || exit 0

[ -f agent-mesh/uv.lock ] || {
	printf 'MISSING: agent-mesh/uv.lock is required for Agent Mesh type checking\n' >&2
	exit 1
}

# A quality gate must never pass because a tool is missing from PATH.
command -v uv >/dev/null 2>&1 || {
	printf 'MISSING: uv is not installed, so type checking cannot run\n' >&2
	exit 1
}

cd agent-mesh
exec uv run --frozen mypy --strict .
