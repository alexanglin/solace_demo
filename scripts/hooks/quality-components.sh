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
	for root in agent-mesh/plugins agent-mesh/tools; do
		if quality_tree_has_files "$root" -name '*.py' -o -name '*.pyi'; then
			return 0
		fi
	done
	return 1
}

quality_dashboard_source_present() {
	quality_tree_has_files apps/dashboard \
		-name '*.ts' -o -name '*.tsx' -o -name '*.js' -o -name '*.jsx'
}

# Emit, one per line, every top-level directory holding owned Python, so the whole-program
# gates reach a new root the day it appears instead of the day someone remembers to edit a
# literal list (docs/adr/0056). The listing is git's -- tracked plus unignored -- so .venv,
# dist/, and mutants/ are excluded without a second exclusion list to keep in step.
# agent-mesh/ is omitted because it is checked by its own toolchain (docs/adr/0029).
quality_root_python_paths() {
	git ls-files --cached --others --exclude-standard -- '*.py' '*.pyi' |
		grep -v '^agent-mesh/' |
		sed -n 's|^\([^/]*\)/.*|\1|p' |
		sort -u
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

# The deploy/ stack is active once its tracked-or-unignored listing holds a compose file or a
# Dockerfile -- the same basenames the compose-spec and hadolint hooks lint, and the same
# git-listing rule the compose policy gate arms on (docs/adr/0045, docs/adr/0048).
quality_deploy_stack_active() {
	[ -d deploy ] || return 1
	git ls-files --cached --others --exclude-standard -- deploy |
		grep -Eq '(^|/)(docker-)?compose(\.[^/]*)?\.ya?ml$|(^|/)Dockerfile(\.[^/]*)?$'
}
