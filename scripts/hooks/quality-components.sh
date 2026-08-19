#!/usr/bin/env sh
# Shared, side-effect-free component activation predicates for quality hooks.

quality_tree_has_files() {
	root=$1
	shift
	[ -d "$root" ] || return 1
	find "$root" -type f \( "$@" \) -print -quit | grep -q .
}

quality_root_python_source_present() {
	for root in tools packages services tests migrations; do
		if quality_tree_has_files "$root" -name '*.py' -o -name '*.pyi'; then
			return 0
		fi
	done
	return 1
}

quality_agent_python_source_present() {
	quality_tree_has_files agent-mesh/plugins -name '*.py' -o -name '*.pyi'
}

quality_dashboard_source_present() {
	quality_tree_has_files apps/dashboard \
		-name '*.ts' -o -name '*.tsx' -o -name '*.js' -o -name '*.jsx'
}

quality_root_python_active() {
	[ -f pyproject.toml ] || quality_root_python_source_present
}

quality_agent_python_active() {
	[ -f agent-mesh/pyproject.toml ] || quality_agent_python_source_present
}

quality_dashboard_active() {
	[ -f apps/dashboard/package.json ] || quality_dashboard_source_present
}
