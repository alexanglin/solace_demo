#!/usr/bin/env sh
# Own ADR-0147's disposable PubSub+ and PostgreSQL integration project.
set -eu

LIVE_TEST_FILES='tests/integration/test_durable_store_live.py
tests/security/test_broker_authorization.py
tests/integration/test_fleet_simulator_live.py
tests/integration/test_guaranteed_delivery_live.py
tests/integration/test_command_dispatch_live.py
tests/integration/test_backlog_recovery_live.py
tests/integration/test_application_data_plane_live.py'

PROVISION_DRONES='drone-delivery-probe
drone-dispatch-probe
drone-vision-01
drone-thermal-02
drone-audio-03
drone-backlog-01
drone-backlog-02
drone-backlog-03
drone-backlog-04
drone-backlog-05
drone-backlog-06
drone-backlog-07
drone-backlog-08
drone-backlog-09
drone-backlog-10
drone-backlog-11
drone-backlog-12
drone-backlog-13
drone-backlog-14
drone-backlog-15
drone-backlog-16
drone-backlog-17
drone-backlog-18
drone-backlog-19
drone-backlog-20
drone-backlog-21
drone-backlog-22
drone-backlog-23'

PRIVATE_BASENAMES='ca.key
broker-server.key
broker-server.crt
broker-server.pem
broker-admin-password
postgres-password
semp-monitor-password
session-secret-key
scenario-control-bearer
fleet-control-bearer
broker-fleet-simulator-password
broker-command-gateway-password
broker-dashboard-api-password
broker-evidence-service-password
broker-recorder-password
broker-event-mesh-gateway-password
broker-event-mesh-tool-password
broker-agent-mesh-agent-password
.env.roles'

COMPOSE_FILE=deploy/compose.yaml
TEMPLATE_ENV=.env.example
ROLE_ENV=deploy/secrets/.env.roles
OWNERSHIP_MARKER=deploy/.ci-live-project
JOB_IDENTITY=pubsub-postgres-integration
NAMESPACE=aerial-rescue-mesh
COMMAND_BUDGET_SECONDS=1200
DIAGNOSTIC_LOG_LINES=200
DIAGNOSTIC_STOP_SECONDS=10
BROKER_LOG_DIRECTORIES='/var/lib/solace/jail/logs
/usr/sw/jail/logs'
# The two files deploy/compose.yaml mounts into the pinned PubSub+ image, whose processes
# run as uid 1000001 and therefore cannot read owner-only host material (docs/adr/0203).
BROKER_READABLE_BASENAMES='broker-admin-password
broker-server.pem'
APPLICATION_DATA_PLANE_TEST=tests/integration/test_application_data_plane_live.py
RESTART_CONTROLLER=scripts/ci/broker-restart-controller.sh
RESTART_REQUEST_TOKEN=AERIAL_RESCUE_BROKER_RESTART_ONCE_V1

usage() {
	printf 'usage: scripts/ci/live-integration.sh {run|cleanup}\n' >&2
}

fail() {
	printf 'FAILED: %s\n' "$1" >&2
	return 1
}

require_command() {
	command -v "$1" >/dev/null 2>&1 || fail "$1 is required for live integration"
}

bounded() {
	current_seconds=$(date +%s)
	remaining_seconds=$((command_deadline_seconds - current_seconds))
	if [ "$remaining_seconds" -le 0 ]; then
		fail "the live integration command budget expired"
		return 124
	fi
	timeout "${remaining_seconds}s" "$@"
}

initialize_identity() {
	if [ -z "${GITHUB_RUN_ID:-}" ]; then
		fail "GITHUB_RUN_ID is required"
		return
	fi
	if [ -z "${GITHUB_RUN_ATTEMPT:-}" ]; then
		fail "GITHUB_RUN_ATTEMPT is required"
		return
	fi
	if [ -z "${GITHUB_JOB:-}" ]; then
		fail "GITHUB_JOB is required"
		return
	fi
	case "$GITHUB_RUN_ID" in
	*[!0-9]*)
		fail "GITHUB_RUN_ID must contain only decimal digits"
		return
		;;
	esac
	case "$GITHUB_RUN_ATTEMPT" in
	*[!0-9]*)
		fail "GITHUB_RUN_ATTEMPT must contain only decimal digits"
		return
		;;
	esac
	if [ "$GITHUB_RUN_ID" -eq 0 ] || [ "$GITHUB_RUN_ATTEMPT" -eq 0 ]; then
		fail "GitHub run identifiers must be positive"
		return
	fi
	normalized_job=$(printf '%s' "$GITHUB_JOB" | tr '[:upper:]_' '[:lower:]-')
	case "$normalized_job" in
	'' | *[!a-z0-9-]* | -* | *-)
		fail "GITHUB_JOB is outside the closed Compose project grammar"
		return
		;;
	esac
	if [ "$normalized_job" != "$JOB_IDENTITY" ]; then
		fail "GITHUB_JOB does not identify the authorized integration job"
		return
	fi
	compose_project="ci-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}-${normalized_job}"
}

compose_runtime() {
	bounded docker compose --project-name "$compose_project" --file "$COMPOSE_FILE" \
		--env-file "$TEMPLATE_ENV" --env-file "$ROLE_ENV" "$@"
}

compose_cleanup() {
	bounded docker compose --project-name "$compose_project" --file "$COMPOSE_FILE" \
		--env-file "$TEMPLATE_ENV" "$@"
}

project_resources() {
	resource_status=0
	for resource_command in "ps --all --quiet" "network ls --quiet"; do
		# The two fixed command shapes deliberately undergo word splitting. Neither contains
		# data derived from the workflow event or caller input.
		# shellcheck disable=SC2086
		bounded docker $resource_command \
			--filter "label=com.docker.compose.project=$compose_project" || resource_status=$?
	done
	return "$resource_status"
}

project_volumes() {
	bounded docker volume ls --quiet \
		--filter "label=com.docker.compose.project=$compose_project"
}

volume_listing_is_known() {
	observed_volume_listing=$1
	require_retained_volumes=$2
	broker_volume_seen=false
	postgres_volume_seen=false
	volume_listing_invalid=false
	while IFS= read -r observed_volume_name || [ -n "$observed_volume_name" ]; do
		[ -n "$observed_volume_name" ] || continue
		case "$observed_volume_name" in
		"${compose_project}_broker-storage")
			if [ "$broker_volume_seen" = true ]; then
				volume_listing_invalid=true
			else
				broker_volume_seen=true
			fi
			;;
		"${compose_project}_postgres-data")
			if [ "$postgres_volume_seen" = true ]; then
				volume_listing_invalid=true
			else
				postgres_volume_seen=true
			fi
			;;
		*) volume_listing_invalid=true ;;
		esac
	done <<EOF
$observed_volume_listing
EOF
	[ "$volume_listing_invalid" = false ] || return 1
	if [ "$broker_volume_seen" = true ] && [ "$postgres_volume_seen" = true ]; then
		return 0
	fi
	[ "$require_retained_volumes" = false ] &&
		[ "$broker_volume_seen" = false ] &&
		[ "$postgres_volume_seen" = false ]
}

marker_is_owned() {
	[ -f "$OWNERSHIP_MARKER" ] && [ ! -L "$OWNERSHIP_MARKER" ] || return 1
	marker_project=$(cat "$OWNERSHIP_MARKER") || return 1
	[ "$marker_project" = "$compose_project" ]
}

remove_checkout_links() {
	for generated_link in deploy/certs deploy/secrets; do
		if [ -L "$generated_link" ]; then
			rm -f "$generated_link"
		elif [ -e "$generated_link" ]; then
			fail "$generated_link is not the CI-created credential symlink"
			return
		fi
	done
}

cleanup_project() {
	cleanup_status=0
	if ! resources_before=$(project_resources); then
		fail "the exact CI project resource readback failed before cleanup"
		return 1
	fi
	if ! volumes_before=$(project_volumes); then
		fail "the exact CI project retained volume readback failed before cleanup"
		return 1
	fi
	owned_marker=false
	if marker_is_owned; then
		owned_marker=true
		compose_cleanup down --remove-orphans >"$work_directory/cleanup.log" 2>&1 ||
			cleanup_status=$?
	elif [ -e "$OWNERSHIP_MARKER" ] || [ -L "$OWNERSHIP_MARKER" ]; then
		fail "the CI ownership marker is invalid; cleanup refused"
		return 1
	elif [ -n "$resources_before" ]; then
		fail "the exact CI project exists without its ownership marker; cleanup refused"
		return 1
	elif ! volume_listing_is_known "$volumes_before" false; then
		fail "the exact CI project retained volume readback is incomplete; cleanup refused"
		return 1
	fi

	if ! resources_after=$(project_resources); then
		fail "the exact CI project resource readback failed after cleanup"
		return 1
	fi
	if [ -n "$resources_after" ]; then
		fail "containers or networks still carry the exact CI project label after cleanup"
		return 1
	fi
	if ! volumes_after=$(project_volumes); then
		fail "the exact CI project retained volume readback failed after cleanup"
		return 1
	fi
	require_retained_volumes=false
	if [ "$owned_marker" = true ] &&
		{ [ "$runtime_started" = true ] || [ -n "$resources_before" ]; }; then
		require_retained_volumes=true
	fi
	if ! volume_listing_is_known "$volumes_after" "$require_retained_volumes"; then
		fail "the exact CI project retained volume readback is incomplete after cleanup"
		return 1
	fi
	if [ "$cleanup_status" -ne 0 ]; then
		return "$cleanup_status"
	fi
	if [ "$owned_marker" = true ]; then
		remove_checkout_links || return
		rm -f "$OWNERSHIP_MARKER"
	elif [ -e deploy/certs ] || [ -L deploy/certs ] || [ -e deploy/secrets ] ||
		[ -L deploy/secrets ]; then
		fail "credential paths exist without the exact CI ownership marker; removal refused"
		return 1
	fi
	if [ -n "$volumes_after" ]; then
		printf 'INFO: retained unique volumes %s_broker-storage and %s_postgres-data for hosted-runner teardown\n' \
			"$compose_project" "$compose_project"
	else
		printf 'INFO: no exact-project volumes were created before cleanup\n'
	fi
	return 0
}

append_redaction() {
	redaction_value=$1
	[ -n "$redaction_value" ] || return 0
	printf '%s\n' "$redaction_value" >>"$redaction_file"
	if [ "${GITHUB_ACTIONS:-false}" = true ]; then
		printf '::add-mask::%s\n' "$redaction_value"
	fi
}

file_mode() {
	stat -c '%a' "$1" 2>/dev/null || stat -f '%Lp' "$1" 2>/dev/null
}

load_redactions() {
	: >"$redaction_file"
	for private_basename in $PRIVATE_BASENAMES; do
		private_file="$generated_deploy/secrets/$private_basename"
		if [ ! -f "$private_file" ] || [ -L "$private_file" ]; then
			fail "generated private material is missing or is not a regular file"
			return
		fi
		private_mode=$(file_mode "$private_file") || {
			fail "generated private material mode could not be read"
			return
		}
		required_mode=600
		for readable_basename in $BROKER_READABLE_BASENAMES; do
			if [ "$private_basename" = "$readable_basename" ]; then
				required_mode=644
			fi
		done
		if [ "$private_mode" != "$required_mode" ]; then
			fail "generated private material does not have its required mode"
			return
		fi
		while IFS= read -r redaction_line || [ -n "$redaction_line" ]; do
			append_redaction "$redaction_line"
			case "$redaction_line" in
			*=*) append_redaction "${redaction_line#*=}" ;;
			esac
		done <"$private_file"
	done
	if [ ! -s "$redaction_file" ]; then
		fail "no generated secret values were available to the fail-closed redactor"
		return
	fi
	awk '!seen[$0]++' "$redaction_file" >"$redaction_file.unique"
	mv "$redaction_file.unique" "$redaction_file"
}

redact_diagnostics() {
	raw_diagnostics=$1
	safe_diagnostics=$2
	if [ ! -s "$redaction_file" ]; then
		printf '<redacted: runtime diagnostics suppressed>\n'
		return
	fi
	if ! awk '
		{
			lower = tolower($0)
			if (lower ~ /authorization[[:space:]]*:/ ||
			    lower ~ /(^|[^a-z])(password|token|api[_-]?key)[[:space:]]*[:=]/ ||
			    $0 ~ /:\/\/[^[:space:]\/@]+:[^[:space:]@]+@/) {
				print "<redacted>"
				next
			}
			print
		}
	' "$raw_diagnostics" >"$safe_diagnostics"; then
		printf '<redacted: runtime diagnostics suppressed>\n'
		return
	fi
	if grep -F -f "$redaction_file" "$safe_diagnostics" >/dev/null 2>&1; then
		printf '<redacted: runtime diagnostics suppressed>\n'
		return
	else
		grep_status=$?
		if [ "$grep_status" -ne 1 ]; then
			printf '<redacted: runtime diagnostics suppressed>\n'
			return
		fi
	fi
	cat "$safe_diagnostics"
}

# The reader above discards a whole document when any generated value survives its line
# filter. That is correct for a status table, but it makes a container's own output
# unreadable: a role name is both a generated value and ordinary broker log text, so one
# incidental occurrence hides the reason a service never started. This reader replaces
# every generated value with the same fixed marker and then applies the identical
# survival proof, so what it prints is proven free of generated material rather than
# discarded because generated material was present.
redact_substituting() {
	raw_diagnostics=$1
	safe_diagnostics=$2
	if [ ! -s "$redaction_file" ]; then
		printf '<redacted: runtime diagnostics suppressed>\n'
		return
	fi
	if ! awk '
		NR == FNR {
			if (length($0) > 0) { token[++tokens] = $0 }
			next
		}
		{
			lower = tolower($0)
			if (lower ~ /authorization[[:space:]]*:/ ||
			    lower ~ /(^|[^a-z])(password|token|api[_-]?key)[[:space:]]*[:=]/ ||
			    $0 ~ /:\/\/[^[:space:]\/@]+:[^[:space:]@]+@/) {
				print "<redacted>"
				next
			}
			line = $0
			for (index_of_token = 1; index_of_token <= tokens; index_of_token++) {
				secret = token[index_of_token]
				kept = ""
				while ((position = index(line, secret)) > 0) {
					kept = kept substr(line, 1, position - 1) "<redacted>"
					line = substr(line, position + length(secret))
				}
				line = kept line
			}
			print line
		}
	' "$redaction_file" "$raw_diagnostics" >"$safe_diagnostics"; then
		printf '<redacted: runtime diagnostics suppressed>\n'
		return
	fi
	if grep -F -f "$redaction_file" "$safe_diagnostics" >/dev/null 2>&1; then
		printf '<redacted: runtime diagnostics suppressed>\n'
		return
	else
		grep_status=$?
		if [ "$grep_status" -ne 1 ]; then
			printf '<redacted: runtime diagnostics suppressed>\n'
			return
		fi
	fi
	cat "$safe_diagnostics"
}

# The broker writes only its container-startup narration to stdout; the reason SolOS
# itself stopped stays in the broker's own log files. Stopping the service first is what
# makes them readable: a container the restart policy keeps cycling refuses a copy. The
# service is about to be torn down, so the stop costs nothing that cleanup would keep.
report_broker_internal_logs() {
	broker_log_copy="$work_directory/broker-internal-logs"
	bounded docker compose --project-name "$compose_project" --file "$COMPOSE_FILE" \
		--env-file "$TEMPLATE_ENV" --env-file "$ROLE_ENV" \
		stop --timeout "$DIAGNOSTIC_STOP_SECONDS" broker \
		>"$work_directory/broker-stop.log" 2>&1 || :
	for broker_log_directory in $BROKER_LOG_DIRECTORIES; do
		rm -rf "$broker_log_copy"
		compose_runtime cp "broker:$broker_log_directory" "$broker_log_copy" \
			>"$work_directory/broker-cp.log" 2>&1 || continue
		printf 'broker internal logs from %s:\n' "$broker_log_directory" >&2
		: >"$work_directory/broker-internal.raw"
		find "$broker_log_copy" -type f -name '*.log' | sort |
			while IFS= read -r broker_log_file; do
				printf '== %s\n' "$(basename "$broker_log_file")" \
					>>"$work_directory/broker-internal.raw"
				tail -n "$DIAGNOSTIC_LOG_LINES" "$broker_log_file" \
					>>"$work_directory/broker-internal.raw"
			done
		redact_substituting "$work_directory/broker-internal.raw" \
			"$work_directory/broker-internal.safe" >&2
		return 0
	done
	printf '<redacted: the broker internal log directory was unreachable>\n' >&2
}

report_failure_diagnostics() {
	printf 'runtime status for project %s:\n' "$compose_project" >&2
	if compose_runtime ps --all >"$work_directory/compose-ps.raw" 2>&1; then
		redact_diagnostics "$work_directory/compose-ps.raw" \
			"$work_directory/compose-ps.safe" >&2
	else
		printf '<redacted: project-scoped status command failed>\n' >&2
	fi
	# A container that never becomes healthy leaves no trace in `ps` beyond its restart
	# count, so the bounded tail of its own output is the only evidence of why. It passes
	# through the same fail-closed redactor as every other diagnostic.
	printf 'runtime logs for project %s:\n' "$compose_project" >&2
	if compose_runtime logs --no-color --timestamps --tail "$DIAGNOSTIC_LOG_LINES" \
		>"$work_directory/compose-logs.raw" 2>&1; then
		redact_substituting "$work_directory/compose-logs.raw" \
			"$work_directory/compose-logs.safe" >&2
	else
		printf '<redacted: project-scoped log command failed>\n' >&2
	fi
	report_broker_internal_logs
}

template_value() {
	template_key=$1
	template_result=$(awk -F= -v key="$template_key" \
		'$1 == key { count++; value = substr($0, index($0, "=") + 1) }
		 END { if (count == 1) print value; else exit 1 }' "$TEMPLATE_ENV") || {
		fail "$template_key must occur exactly once in the closed CI environment template"
		return
	}
	case "$template_result" in
	'' | *[!a-z0-9_]*)
		fail "$template_key is outside the closed PostgreSQL identifier grammar"
		return
		;;
	esac
	printf '%s\n' "$template_result"
}

prepare_runtime() {
	for required_file in \
		"$COMPOSE_FILE" "$TEMPLATE_ENV" scripts/broker-secrets.sh "$RESTART_CONTROLLER"; do
		if [ ! -f "$required_file" ] || [ -L "$required_file" ]; then
			fail "$required_file is required and must be a regular checkout file"
			return
		fi
	done
	if [ ! -x "$RESTART_CONTROLLER" ]; then
		fail "the broker restart controller must be executable"
		return
	fi
	for empty_path in deploy/certs deploy/secrets "$OWNERSHIP_MARKER"; do
		if [ -e "$empty_path" ] || [ -L "$empty_path" ]; then
			fail "$empty_path must be absent; CI never reuses developer credentials"
			return
		fi
	done

	bounded uv run --frozen python -m tools.live_integration_policy \
		>"$work_directory/policy.log" 2>&1 || {
		fail "the closed ADR-0147 live inventory is incomplete or inconsistent"
		return
	}
	preexisting_resources=$(project_resources) || {
		fail "the Docker daemon could not prove the exact CI project absent"
		return
	}
	preexisting_volumes=$(project_volumes) || {
		fail "the Docker daemon could not prove exact-project volumes absent"
		return
	}
	if [ -n "$preexisting_resources" ] || [ -n "$preexisting_volumes" ]; then
		fail "the exact CI Compose project already exists; adoption is refused"
		return
	fi

	printf '%s\n' "$compose_project" >"$OWNERSHIP_MARKER"
	cleanup_armed=true
	generated_deploy="$work_directory/generated"
	AERIAL_RESCUE_DEPLOY_DIR=$generated_deploy
	export AERIAL_RESCUE_DEPLOY_DIR
	bounded scripts/broker-secrets.sh >"$work_directory/secret-generation.log" 2>&1 || {
		fail "CI-only certificate and password generation failed"
		return
	}
	unset AERIAL_RESCUE_DEPLOY_DIR
	load_redactions
	ln -s "$generated_deploy/certs" deploy/certs
	ln -s "$generated_deploy/secrets" deploy/secrets

	compose_runtime pull broker postgres >"$work_directory/compose-pull.log" 2>&1 || {
		fail "the pinned PubSub+ or PostgreSQL image could not be pulled"
		return
	}
	compose_runtime up --detach --wait --wait-timeout "$COMMAND_BUDGET_SECONDS" broker postgres \
		>"$work_directory/compose-up.log" 2>&1 || {
		fail "the isolated PubSub+ and PostgreSQL services did not become healthy"
		return
	}
	runtime_started=true
}

provision_broker() {
	set --
	for drone_id in $PROVISION_DRONES; do
		set -- "$@" --drone "$drone_id"
	done
	bounded uv run --frozen python -m aerial_rescue_broker --namespace "$NAMESPACE" "$@" \
		>"$work_directory/provision.log" 2>&1 ||
		fail "least-privilege PubSub+ provisioning failed"
}

unset_restart_capability() {
	unset AERIAL_RESCUE_BROKER_RESTART_REQUEST_FIFO
	unset AERIAL_RESCUE_BROKER_RESTART_RESULT_FIFO
	unset AERIAL_RESCUE_BROKER_RESTART_REQUEST_TOKEN
}

start_restart_controller() {
	restart_control_directory="$work_directory/broker-restart-control"
	mkdir "$restart_control_directory" || {
		fail "the private broker restart directory could not be created"
		return 1
	}
	restart_request_fifo="$restart_control_directory/request"
	restart_result_fifo="$restart_control_directory/result"
	mkfifo "$restart_request_fifo" "$restart_result_fifo" || {
		fail "the private broker restart FIFOs could not be created"
		return 1
	}
	chmod 600 "$restart_request_fifo" "$restart_result_fifo" || {
		fail "the private broker restart FIFO modes could not be set"
		return 1
	}

	"$repository_root/$RESTART_CONTROLLER" \
		"$compose_project" \
		"$repository_root/$COMPOSE_FILE" \
		"$repository_root/$TEMPLATE_ENV" \
		"$repository_root/$ROLE_ENV" \
		"$restart_request_fifo" \
		"$restart_result_fifo" \
		>"$work_directory/broker-restart-controller.log" 2>&1 &
	restart_controller_pid=$!
	AERIAL_RESCUE_BROKER_RESTART_REQUEST_FIFO=$restart_request_fifo
	AERIAL_RESCUE_BROKER_RESTART_RESULT_FIFO=$restart_result_fifo
	AERIAL_RESCUE_BROKER_RESTART_REQUEST_TOKEN=$RESTART_REQUEST_TOKEN
	export AERIAL_RESCUE_BROKER_RESTART_REQUEST_FIFO
	export AERIAL_RESCUE_BROKER_RESTART_RESULT_FIFO
	export AERIAL_RESCUE_BROKER_RESTART_REQUEST_TOKEN
}

stop_restart_controller() {
	unset_restart_capability
	if [ -n "$restart_controller_pid" ]; then
		kill "$restart_controller_pid" 2>/dev/null || :
		wait "$restart_controller_pid" 2>/dev/null || :
		restart_controller_pid=
	fi
}

wait_restart_controller() {
	unset_restart_capability
	if wait "$restart_controller_pid"; then
		controller_status=0
	else
		controller_status=$?
	fi
	restart_controller_pid=
	if [ "$controller_status" -ne 0 ]; then
		fail "the one-shot broker restart controller failed"
		return 1
	fi
}

run_live_suite() {
	POSTGRES_USER=$(template_value POSTGRES_USER) || return
	POSTGRES_DB=$(template_value POSTGRES_DB) || return
	export POSTGRES_USER POSTGRES_DB
	unset ANTHROPIC_API_KEY OPENAI_API_KEY LLM_SERVICE_API_KEY OLLAMA_HOST
	unset SOLACE_PASSWORD SOLACE_URL SOLACE_BROKER_PASSWORD SOLACE_BROKER_USERNAME
	unset GITHUB_RUN_ID GITHUB_RUN_ATTEMPT GITHUB_JOB COMPOSE_PROJECT_NAME
	unset_restart_capability

	for test_file in $LIVE_TEST_FILES; do
		if [ "$test_file" = "$APPLICATION_DATA_PLANE_TEST" ]; then
			start_restart_controller || {
				failing_test=$test_file
				return 1
			}
		fi
		if ! bounded uv run --frozen pytest --no-header -q "$test_file" \
			>"$work_directory/test.raw" 2>&1; then
			failing_test=$test_file
			stop_restart_controller
			return 1
		fi
		if [ "$test_file" = "$APPLICATION_DATA_PLANE_TEST" ] &&
			! wait_restart_controller; then
			failing_test=$test_file
			return 1
		fi
		printf 'PASS: %s\n' "$test_file"
	done
}

finish_run() {
	run_status=$?
	trap - 0 1 2 15
	stop_restart_controller
	if [ "$run_status" -ne 0 ]; then
		if [ -n "$failing_test" ]; then
			printf 'FAILED: %s\n' "$failing_test" >&2
		else
			printf 'FAILED: ephemeral live integration did not complete\n' >&2
		fi
		if [ -s "$redaction_file" ]; then
			report_failure_diagnostics
		fi
	fi
	cleanup_status=0
	if [ "$cleanup_armed" = true ]; then
		cleanup_project || cleanup_status=$?
	fi
	if [ "$cleanup_status" -ne 0 ]; then
		printf 'FAILED: exact-project cleanup did not complete\n' >&2
		if [ "$run_status" -eq 0 ]; then
			run_status=$cleanup_status
		fi
	fi
	rm -rf "$work_directory"
	exit "$run_status"
}

operation=${1:-}
if [ "$#" -ne 1 ]; then
	usage
	exit 2
fi
case "$operation" in
run | cleanup) ;;
*)
	usage
	exit 2
	;;
esac

require_command git
require_command timeout
require_command docker
if [ "$operation" = run ]; then
	require_command uv
	require_command openssl
fi
initialize_identity
repository_root=$(git rev-parse --show-toplevel) || fail "the checkout root could not be resolved"
cd "$repository_root"
umask 077
command_deadline_seconds=$(($(date +%s) + COMMAND_BUDGET_SECONDS))
work_directory=$(mktemp -d "${TMPDIR:-/tmp}/aerial-rescue-live.XXXXXX")
redaction_file="$work_directory/redactions"
: >"$redaction_file"
cleanup_armed=false
runtime_started=false
failing_test=
generated_deploy=
restart_controller_pid=
restart_control_directory=
restart_request_fifo=
restart_result_fifo=

if [ "$operation" = cleanup ]; then
	cleanup_status=0
	cleanup_project || cleanup_status=$?
	rm -rf "$work_directory"
	exit "$cleanup_status"
fi

trap finish_run 0
trap 'exit 129' 1
trap 'exit 130' 2
trap 'exit 143' 15

prepare_runtime
provision_broker
run_live_suite
