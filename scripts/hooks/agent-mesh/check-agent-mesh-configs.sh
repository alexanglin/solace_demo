#!/usr/bin/env sh
# Offline semantic validation for every owned Agent Mesh configuration.
set -eu

cd "$(git rev-parse --show-toplevel)"

[ -d agent-mesh/configs ] || exit 0
if ! find agent-mesh/configs \( -type f -o -type l \) \
	\( -name '*.yaml' -o -name '*.yml' \) -print -quit | grep -q .; then
	exit 0
fi

[ -f agent-mesh/pyproject.toml ] || {
	printf 'MISSING: agent-mesh/pyproject.toml is required by Agent Mesh configuration\n' >&2
	exit 1
}
[ -f agent-mesh/uv.lock ] || {
	printf 'MISSING: agent-mesh/uv.lock is required for Agent Mesh configuration validation\n' >&2
	exit 1
}
command -v uv >/dev/null 2>&1 || {
	printf 'MISSING: uv is not installed, so Agent Mesh configuration validation cannot run\n' >&2
	exit 1
}
[ -f agent-mesh/tools/agent_mesh_config_validator.py ] || {
	printf 'MISSING: agent-mesh/tools/agent_mesh_config_validator.py is required for Agent Mesh configuration validation\n' >&2
	exit 1
}

cd agent-mesh
exec uv run --frozen python -m tools.agent_mesh_config_validator
