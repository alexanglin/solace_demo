#!/usr/bin/env sh
# Create the Agent Mesh Platform service's database before the container that migrates it
# (docs/adr/0222-register-the-mesh-s-local-model-in-the-platform-service.md).
#
# The Platform service owns the registered model configurations and runs its own Alembic history
# at boot. That history's version table carries Alembic's default name, so it cannot share a
# database with the application's eleven-revision history. It is therefore given its own database
# in the same cluster, under the same owner.
#
# PostgreSQL will not create it on demand. The official image initialises exactly one database,
# named by POSTGRES_DB, and only on a first initialisation of an empty data volume -- which any
# running deployment passed long ago. So the creation is a startup step rather than a container
# setting, and it runs between the database becoming healthy and the mesh being started.
#
# The statement runs inside the PostgreSQL container over its local socket, so no credential is
# read, passed, or logged here. The database name is validated as a plain identifier first,
# because it reaches PostgreSQL inside a statement rather than as a parameter.
#
# Idempotent, because `just up` is. Override the Docker CLI with PLATFORM_DATABASE_DOCKER.
set -eu

usage() {
	printf 'usage: scripts/create-platform-database.sh\n' >&2
}

case "${1:-}" in
'') ;;
*)
	usage
	exit 2
	;;
esac

docker_cli=${PLATFORM_DATABASE_DOCKER:-docker}
compose_file=${PLATFORM_DATABASE_COMPOSE:-deploy/compose.yaml}
service=${PLATFORM_DATABASE_SERVICE:-postgres}
owner=${POSTGRES_USER:-aerial_rescue}
database=${PLATFORM_DATABASE_NAME:-aerial_rescue_platform}

command -v "$docker_cli" >/dev/null 2>&1 || {
	printf 'MISSING: %s is required to reach the PostgreSQL service\n' "$docker_cli" >&2
	exit 1
}

# Both names are interpolated into SQL, so both are held to an unquoted-identifier shape rather
# than escaped. A name outside it is a configuration error, not something to normalise.
for identifier in "$database" "$owner"; do
	case "$identifier" in
	'' | [0-9]* | *[!a-z0-9_]*)
		printf 'REFUSED: %s is not a plain lowercase identifier\n' "$identifier" >&2
		printf '         Set PLATFORM_DATABASE_NAME and POSTGRES_USER to [a-z_][a-z0-9_]* values.\n' >&2
		exit 1
		;;
	esac
done

compose() {
	"$docker_cli" compose \
		--env-file .env \
		--env-file deploy/secrets/.env.roles \
		-f "$compose_file" \
		exec -T "$service" "$@"
}

existing=$(compose psql -U "$owner" -d postgres -tAc \
	"select 1 from pg_database where datname = '$database'") || {
	printf 'REFUSED: could not ask %s whether %s exists\n' "$service" "$database" >&2
	exit 1
}

case "$existing" in
*1*)
	printf 'platform:   database %s already exists\n' "$database"
	exit 0
	;;
esac

compose psql -U "$owner" -d postgres -v ON_ERROR_STOP=1 \
	-c "create database \"$database\" owner \"$owner\"" >/dev/null || {
	printf 'REFUSED: could not create database %s\n' "$database" >&2
	exit 1
}

printf 'platform:   created database %s\n' "$database"
