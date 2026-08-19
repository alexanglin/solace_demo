#!/usr/bin/env sh
# The environment template must carry names and placeholders, never live values.
#
# `.env.example` is tracked and this repository is public, so a real value committed here
# cannot be undone by a later commit. Two rules apply:
#
#   1. No value may embed URL userinfo credentials (scheme://user:password@host), whatever
#      the variable is called -- a connection string is the most common way a live
#      credential reaches a template.
#   2. A secret-bearing variable must hold an explicit placeholder:
#        empty          SOLACE_PASSWORD=
#        angle bracket  OPENAI_API_KEY=<required>
#        indirection    SOLACE_PASSWORD=${SOLACE_PASSWORD}
#
# Names that carry no credential (URLs, hosts, ports, flags) may hold a literal, because
# a loopback URL is configuration rather than a secret.
set -eu

SECRET_NAME='(^|_)(API_?KEY|KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIALS?|AUTH|AUTHORIZATION|BEARER|SIGNATURE|SALT)(_|$)'
URL_USERINFO='[A-Za-z][A-Za-z0-9+.-]*://[^/@[:space:]]+:[^/@[:space:]]+@'

status=0

for file in "$@"; do
	[ -f "$file" ] || continue

	line_number=0
	while IFS= read -r line || [ -n "$line" ]; do
		line_number=$((line_number + 1))

		trimmed=$(printf '%s\n' "$line" | sed -E 's/^[[:space:]]*//; s/\r$//')
		case "$trimmed" in
		'' | '#'*) continue ;;
		esac

		# An `export ` prefix is still an assignment.
		assignment=$(printf '%s\n' "$trimmed" | sed -E 's/^export[[:space:]]+//')

		if ! printf '%s\n' "$assignment" |
			grep -Eq '^[A-Za-z_][A-Za-z0-9_]*='; then
			printf '%s:%s: invalid dotenv assignment\n' "$file" "$line_number" >&2
			status=1
			continue
		fi

		name=${assignment%%=*}
		value=${assignment#*=}

		if printf '%s\n' "$value" | grep -Eq "$URL_USERINFO"; then
			printf '%s:%s: %s has embedded credentials in a URL; use a separate variable\n' \
				"$file" "$line_number" "$name" >&2
			status=1
			continue
		fi

		# Case-insensitive: a lowercase name carries exactly the same secret.
		printf '%s\n' "$name" | grep -Eqi "$SECRET_NAME" || continue

		[ -z "$value" ] && continue
		if printf '%s\n' "$value" | grep -Eq '^<[^<>[:space:]]+>$'; then
			continue
		fi
		expected_indirection="\${$name}"
		[ "$value" = "$expected_indirection" ] && continue

		# shellcheck disable=SC2016
		printf '%s:%s: %s holds a literal value; use <required> or ${%s}\n' \
			"$file" "$line_number" "$name" "$name" >&2
		status=1
	done <"$file"
done

exit "$status"
