#!/usr/bin/env sh
# Refuse to start the Agent Mesh unless the locked local model is actually served
# (docs/adr/0098-start-the-agent-mesh-with-the-default-profile.md).
#
# The mesh joined the default profile, so `just up` now starts the component the demonstration
# is about. Nothing in the container's readiness path touches Ollama: the management server's
# /readyz is broker-connected and every flow thread alive, so with the daemon stopped the
# container reports healthy and the first prompt fails. This check moves that failure from
# during a demonstration to before one.
#
# It reads agent-mesh/model-lock.toml, which is the only home of the manifest digest
# (docs/adr/0063-lock-local-models-by-manifest-digest.md), and compares it against what the
# running daemon reports. The offline configuration validator proves only that an identifier is
# listed in the lock in canonical form; this is the readiness half it cannot do.
#
# Two spellings differ across the boundary and both are normalised here:
#   the lock's identifier carries the LiteLLM provider prefix   ollama_chat/qwen3:4b
#   the daemon reports the bare Ollama name                     qwen3:4b
#   the lock's digest carries the algorithm prefix              sha256:359d7dd4...
#   the daemon reports the bare hexadecimal                     359d7dd4...
#
# The host endpoint defaults to loopback rather than to .env's OLLAMA_HOST, because that value
# is written for the container's view (host.docker.internal) and this runs on the host.
# Override with OLLAMA_PREFLIGHT_URL.
set -eu

usage() {
	printf 'usage: scripts/preflight-ollama.sh\n' >&2
}

case "${1:-}" in
'') ;;
*)
	usage
	exit 2
	;;
esac

command -v curl >/dev/null 2>&1 || {
	printf 'MISSING: curl is required to reach the Ollama daemon\n' >&2
	exit 1
}

lock=${OLLAMA_PREFLIGHT_MODEL_LOCK:-agent-mesh/model-lock.toml}
endpoint=${OLLAMA_PREFLIGHT_URL:-http://127.0.0.1:11434}

[ -f "$lock" ] || {
	printf 'MISSING: %s does not exist; the locked model cannot be identified\n' "$lock" >&2
	exit 1
}

# One identifier and one digest per [[models]] entry, in the canonical form the offline
# validator already enforces. Read positionally so the two stay paired.
identifiers=$(sed -n 's/^identifier = "\(.*\)"$/\1/p' "$lock")
digests=$(sed -n 's/^digest = "\(.*\)"$/\1/p' "$lock")

[ -n "$identifiers" ] || {
	printf 'MISSING: %s lists no model identifier\n' "$lock" >&2
	exit 1
}

tags=$(curl -sS --max-time 5 "$endpoint/api/tags" 2>/dev/null) || {
	printf 'REFUSED: the Ollama daemon is not answering at %s\n' "$endpoint" >&2
	printf '         Start it with: ollama serve -- then run just up again.\n' >&2
	exit 1
}

[ -n "$tags" ] || {
	printf 'REFUSED: the Ollama daemon at %s returned an empty response\n' "$endpoint" >&2
	printf '         Start it with: ollama serve -- then run just up again.\n' >&2
	exit 1
}

failed=false
position=0
for identifier in $identifiers; do
	position=$((position + 1))
	locked=$(printf '%s\n' "$digests" | sed -n "${position}p")
	# Strip the LiteLLM provider prefix and the digest algorithm prefix.
	name=${identifier#*/}
	locked=${locked#sha256:}

	# The daemon's entry for this exact name, and the digest inside it. Both fields are flat
	# JSON strings, so the object is isolated by its name and the digest read out of it.
	# Structural whitespace is stripped before matching: the daemon emits compact JSON, but a
	# proxy in front of it need not, and the fields being read never contain a space.
	fields=$(printf '%s' "$tags" | tr -d ' \t' | tr ',{}' '\n')
	entry=$(printf '%s\n' "$fields" | grep -F "\"name\":\"$name\"" || true)
	if [ -z "$entry" ]; then
		printf 'REFUSED: the daemon at %s does not serve %s, which %s locks\n' \
			"$endpoint" "$name" "$lock" >&2
		printf '         Pull it with: ollama pull %s -- then run just up again.\n' "$name" >&2
		failed=true
		continue
	fi

	served=$(printf '%s\n' "$fields" |
		grep -A20 -F "\"name\":\"$name\"" |
		sed -n 's/.*"digest":"\([0-9a-f]*\)".*/\1/p' | head -n 1)
	if [ "$served" != "$locked" ]; then
		printf 'REFUSED: %s is served at digest %s, but %s locks %s\n' \
			"$name" "${served:-unknown}" "$lock" "$locked" >&2
		printf '         The tag moved under the lock. Reconcile it before starting.\n' >&2
		failed=true
		continue
	fi

	printf 'ollama:     %s at %s\n' "$name" "$locked"
done

[ "$failed" = false ] || exit 1
printf 'preflight:  every locked model is served at its locked digest\n'
