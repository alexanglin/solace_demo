#!/usr/bin/env sh
# Block environment files from ever being staged.
# AGENTS.md §6 (Security and privacy hygiene): commit .env.example placeholders, never .env or live values.
# Receives staged file paths as arguments.
set -eu

status=0
for file in "$@"; do
	base=$(basename "$file")
	case "$base" in
	.env.example)
		# Placeholder templates are the one permitted form.
		;;
	.env | .env.*)
		printf 'BLOCKED: %s\n' "$file" >&2
		status=1
		;;
	esac
done

if [ "$status" -ne 0 ]; then
	cat >&2 <<'MSG'

Environment files must never be committed. Only .env.example is tracked.
If this file contains no secrets, rename it to .env.example.
If it does contain secrets, they are now in your working tree only --
unstage the file and rotate anything that was exposed.
MSG
fi

exit "$status"
