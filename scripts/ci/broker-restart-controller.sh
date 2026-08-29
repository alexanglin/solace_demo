#!/usr/bin/env sh
# Own the one-shot ADR-0186 broker restart capability outside pytest.
set -eu

REQUEST_TOKEN=AERIAL_RESCUE_BROKER_RESTART_ONCE_V1
SUCCEEDED_TOKEN=AERIAL_RESCUE_BROKER_RESTART_SUCCEEDED_V1
FAILED_TOKEN=AERIAL_RESCUE_BROKER_RESTART_FAILED_V1
OPERATION_TIMEOUT_SECONDS=30

active_pid=

usage() {
	printf '%s\n' \
		'usage: broker-restart-controller.sh [--manual-authority] PROJECT COMPOSE PUBLIC_ENV ROLE_ENV REQUEST_FIFO RESULT_FIFO' >&2
}

fail() {
	printf 'FAILED: %s\n' "$1" >&2
	return 1
}

refuse_authority() {
	fail "$1" || :
	exit 2
}

stop_active_child() {
	if [ -n "$active_pid" ]; then
		kill "$active_pid" 2>/dev/null || :
		wait "$active_pid" 2>/dev/null || :
		active_pid=
	fi
}

finish_signal() {
	stop_active_child
	exit "$1"
}

run_bounded() {
	timeout "${OPERATION_TIMEOUT_SECONDS}s" "$@" &
	active_pid=$!
	if wait "$active_pid"; then
		active_pid=
		return 0
	else
		bounded_status=$?
		active_pid=
		return "$bounded_status"
	fi
}

is_clean_absolute_path() {
	case "$1" in
	/*) ;;
	*) return 1 ;;
	esac
	case "$1" in
	*/../* | */./* | *//*) return 1 ;;
	esac
}

is_regular_authority_file() {
	is_clean_absolute_path "$1" && [ -f "$1" ] && [ ! -L "$1" ]
}

file_mode() {
	stat -c '%a' "$1" 2>/dev/null || stat -f '%Lp' "$1" 2>/dev/null
}

is_private_fifo() {
	is_clean_absolute_path "$1" && [ -p "$1" ] && [ ! -L "$1" ] || return 1
	fifo_mode=$(file_mode "$1") || return 1
	[ "$fifo_mode" = 600 ]
}

publish_result() {
	result_token=$1
	run_bounded sh -c "printf '%s\\n' \"\$1\" >\"\$2\"" \
		broker-restart-result "$result_token" "$result_fifo"
}

publish_failure() {
	publish_result "$FAILED_TOKEN" || :
}

receive_one_request() {
	run_bounded sh -c "
		exec 3<\"\$1\"
		IFS= read -r request <&3 || exit 1
		[ \"\$request\" = \"\$2\" ] || exit 1
		if IFS= read -r extra <&3; then
			exit 1
		fi
	" broker-restart-request "$request_fifo" "$REQUEST_TOKEN"
}

trap 'finish_signal 129' 1
trap 'finish_signal 130' 2
trap 'finish_signal 143' 15

manual_authority=false
if [ "${1:-}" = --manual-authority ]; then
	manual_authority=true
	shift
fi
if [ "$#" -ne 6 ]; then
	usage
	exit 2
fi

project=$1
compose_file=$2
public_env=$3
role_env=$4
request_fifo=$5
result_fifo=$6

if [ "$manual_authority" = true ]; then
	if [ "$project" != aerial-rescue-mesh ]; then
		refuse_authority "manual authority names no authorized Compose project"
	fi
else
	if ! printf '%s\n' "$project" | awk '
		/^ci-[1-9][0-9]*-[1-9][0-9]*-pubsub-postgres-integration$/ { valid = 1 }
		END { exit(valid ? 0 : 1) }
	'; then
		refuse_authority "CI authority is outside the closed Compose project grammar"
	fi
fi

if ! is_regular_authority_file "$compose_file"; then
	refuse_authority "the Compose authority file is invalid"
fi
case "$compose_file" in
*/deploy/compose.yaml) repository_root=${compose_file%/deploy/compose.yaml} ;;
*)
	refuse_authority "the Compose authority file is not the expected checkout path"
	;;
esac
if [ -z "$repository_root" ]; then
	refuse_authority "the checkout root cannot be empty"
fi

if [ "$manual_authority" = true ]; then
	expected_public_env="$repository_root/.env"
else
	expected_public_env="$repository_root/.env.example"
fi
expected_role_env="$repository_root/deploy/secrets/.env.roles"
if [ "$public_env" != "$expected_public_env" ] ||
	! is_regular_authority_file "$public_env"; then
	refuse_authority "the public environment authority file is invalid"
fi
if [ "$role_env" != "$expected_role_env" ] ||
	! is_regular_authority_file "$role_env"; then
	refuse_authority "the role environment authority file is invalid"
fi
if [ "$request_fifo" = "$result_fifo" ] ||
	! is_private_fifo "$request_fifo" ||
	! is_private_fifo "$result_fifo"; then
	refuse_authority "the private FIFO capability is invalid"
fi

cd "$repository_root"
if ! receive_one_request; then
	publish_failure
	fail "the one-shot broker restart request was refused"
	exit 1
fi

if ! run_bounded docker compose --project-name "$project" --file deploy/compose.yaml \
	--env-file "${public_env#"$repository_root"/}" \
	--env-file "${role_env#"$repository_root"/}" \
	restart --no-deps broker; then
	publish_failure
	fail "the project-scoped broker restart failed"
	exit 1
fi
if ! run_bounded docker compose --project-name "$project" --file deploy/compose.yaml \
	--env-file "${public_env#"$repository_root"/}" \
	--env-file "${role_env#"$repository_root"/}" \
	up --detach --wait --wait-timeout "$OPERATION_TIMEOUT_SECONDS" broker; then
	publish_failure
	fail "the project-scoped broker health recovery failed"
	exit 1
fi
if ! publish_result "$SUCCEEDED_TOKEN"; then
	fail "the broker restart result could not be delivered"
	exit 1
fi
