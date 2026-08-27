#!/usr/bin/env sh
# Generate the per-checkout certificate authority, the broker's server certificate, and the
# stack's consumed passwords (docs/adr/0046 and docs/adr/0129).
#
# Outputs, relative to the deploy directory (default deploy/, override with
# AERIAL_RESCUE_DEPLOY_DIR so tests can redirect it):
#   certs/ca.pem                    public authority certificate -- the trust-store directory
#   secrets/ca.key                  authority key
#   secrets/broker-server.key       broker key
#   secrets/broker-server.crt       broker certificate (SANs localhost, broker, 127.0.0.1)
#   secrets/broker-server.pem       key then certificate, what tls_servercertificate_filepath names
#   secrets/broker-admin-password   32 random bytes, hexadecimal
#   secrets/postgres-password       32 random bytes, hexadecimal
#   secrets/semp-monitor-password   32 random bytes, hexadecimal; operator-provisioned SEMP
#   secrets/session-secret-key      32 random bytes, hexadecimal -- the Web UI session key
#   secrets/broker-<role>-password  one per enabled messaging role, same form
#   secrets/scenario-control-bearer private scenario HTTP credential, same form
#   secrets/fleet-control-bearer    private fleet HTTP credential, same form
#   secrets/.env.roles              the same role credentials as Compose variables
#
# The eight role names below are the enabled messaging subset of the Principal enum in
# packages/domain (docs/adr/0121 and docs/adr/0126). The SEMP-only Event Management Agent
# identity is separate, and the retired discovery SMF role receives no credential or
# environment pair. Scenario control is brokerless and also receives none.
# A gate test in tools/quality_gate_tests/deploy/ holds the subset equal.
#
# Every private file is created 0600. Existing material is left alone unless --rotate is
# given. Nothing here prints a key or a password: only paths, fingerprints, and the
# subject alternative names. Extensions are supplied through configuration files rather
# than -addext so the script runs under LibreSSL as well as OpenSSL.
set -eu

usage() {
	printf 'usage: scripts/broker-secrets.sh [--rotate]\n' >&2
}

rotate=false
case "${1:-}" in
'') ;;
--rotate) rotate=true ;;
*)
	usage
	exit 2
	;;
esac

command -v openssl >/dev/null 2>&1 || {
	printf 'MISSING: openssl is required to generate the local certificate authority\n' >&2
	exit 1
}

cd "$(git rev-parse --show-toplevel)"
umask 077

deploy_dir=${AERIAL_RESCUE_DEPLOY_DIR:-deploy}
certs="$deploy_dir/certs"
secrets="$deploy_dir/secrets"
validity_days=365

broker_roles="fleet-simulator command-gateway dashboard-api evidence-service recorder
event-mesh-gateway event-mesh-tool agent-mesh-agent"
private_http_bearers="scenario-control-bearer fleet-control-bearer"
passwords="broker-admin-password postgres-password semp-monitor-password session-secret-key
$private_http_bearers"
for role in $broker_roles; do
	passwords="$passwords broker-$role-password"
done

report() {
	printf 'authority:  %s\n' "$certs/ca.pem"
	openssl x509 -noout -fingerprint -sha256 -in "$certs/ca.pem"
	printf 'broker:     %s\n' "$secrets/broker-server.pem"
	openssl x509 -noout -fingerprint -sha256 -in "$secrets/broker-server.crt"
	openssl x509 -noout -text -in "$secrets/broker-server.crt" |
		grep -A1 'Subject Alternative Name' | tail -n 1 | sed 's/^[[:space:]]*//'
	printf 'passwords:  %s/{broker-admin,postgres,semp-monitor}-password\n' "$secrets"
	printf 'session:    %s/session-secret-key\n' "$secrets"
	printf 'controls:   %s/{scenario,fleet}-control-bearer\n' "$secrets"
	printf 'roles:      %s/broker-{%s}-password\n' "$secrets" \
		"$(printf '%s' "$broker_roles" | tr '\n ' ',,')"
	printf 'compose:    %s/.env.roles\n' "$secrets"
}

# Compose reads the enabled role identities from this file as a second --env-file, so no
# password is ever hand-copied into .env. It is derived from the password files above and
# rewritten on every run, which keeps it correct after a rotation or a filled gap. The
# name begins with .env so .gitignore's `.env.*` rule and the no-env-files hook both cover
# it in addition to `secrets/`; a live credential reaching a commit is the one failure a
# later commit cannot undo (AGENTS.md section 6).
write_role_environment() {
	pending="$secrets/.env.roles.pending"
	: >"$pending"
	chmod 600 "$pending"
	for role in $broker_roles; do
		variable=$(printf '%s' "$role" | tr 'a-z-' 'A-Z_')
		printf 'SOLACE_%s_USERNAME=%s\n' "$variable" "$role" >>"$pending"
		printf 'SOLACE_%s_PASSWORD=%s\n' "$variable" \
			"$(cat "$secrets/broker-$role-password")" >>"$pending"
	done
	# The Web UI's session signing key. The image ships a placeholder, .env.example carries
	# `<required>`, and the upstream schema check is presence-only, so an unreplaced value
	# signs real sessions. Generating it here means the fresh-clone path never has to set
	# it by hand (docs/adr/0094).
	printf 'SESSION_SECRET_KEY=%s\n' "$(cat "$secrets/session-secret-key")" >>"$pending"
	mv "$pending" "$secrets/.env.roles"
	chmod 600 "$secrets/.env.roles"
}

# Certificate material is all-or-nothing: a server certificate is only meaningful beside the
# authority that signed it. Passwords are independent of it and of each other, so a role
# added later fills its own gap rather than rotating the authority the running broker is
# already presenting.
certificates=true
for required in "$certs/ca.pem" "$secrets/ca.key" "$secrets/broker-server.key" \
	"$secrets/broker-server.crt" "$secrets/broker-server.pem"; do
	[ -f "$required" ] || certificates=false
done
complete=$certificates
for name in $passwords; do
	[ -f "$secrets/$name" ] || complete=false
done
if [ "$complete" = true ] && [ "$rotate" = false ]; then
	write_role_environment
	printf 'unchanged: material already present; pass --rotate to regenerate\n'
	report
	exit 0
fi

work=$(mktemp -d "${TMPDIR:-/tmp}/aerial-rescue-secrets.XXXXXX")
trap 'rm -rf "$work"' 0 1 2 15

cat >"$work/ca.cnf" <<CNF
[req]
distinguished_name = dn
prompt = no
x509_extensions = authority

[dn]
CN = Aerial Rescue Mesh local CA

[authority]
basicConstraints = critical, CA:TRUE, pathlen:0
keyUsage = critical, keyCertSign, cRLSign
subjectKeyIdentifier = hash
CNF

cat >"$work/server.cnf" <<CNF
[req]
distinguished_name = dn
prompt = no

[dn]
CN = broker
CNF

cat >"$work/server.ext" <<EXT
basicConstraints = CA:FALSE
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = DNS:localhost, DNS:broker, IP:127.0.0.1
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid
EXT

openssl ecparam -name prime256v1 -genkey -noout -out "$work/ca.key" 2>/dev/null
openssl req -x509 -new -key "$work/ca.key" -sha256 -days "$validity_days" \
	-config "$work/ca.cnf" -out "$work/ca.pem" 2>/dev/null
openssl ecparam -name prime256v1 -genkey -noout -out "$work/broker-server.key" 2>/dev/null
openssl req -new -key "$work/broker-server.key" -sha256 -config "$work/server.cnf" \
	-out "$work/broker-server.csr" 2>/dev/null
openssl x509 -req -in "$work/broker-server.csr" -CA "$work/ca.pem" -CAkey "$work/ca.key" \
	-CAcreateserial -days "$validity_days" -sha256 -extfile "$work/server.ext" \
	-out "$work/broker-server.crt" 2>/dev/null
cat "$work/broker-server.key" "$work/broker-server.crt" >"$work/broker-server.pem"
mkdir -p "$certs" "$secrets"
chmod 755 "$certs"
chmod 700 "$secrets"

if [ "$certificates" = false ] || [ "$rotate" = true ]; then
	cp "$work/ca.pem" "$certs/ca.pem"
	chmod 644 "$certs/ca.pem"
	for name in ca.key broker-server.key broker-server.crt broker-server.pem; do
		cp "$work/$name" "$secrets/$name"
		chmod 600 "$secrets/$name"
	done
fi

for name in $passwords; do
	if [ "$rotate" = true ] || [ ! -f "$secrets/$name" ]; then
		openssl rand -hex 32 | tr -d '\n' >"$secrets/$name"
		chmod 600 "$secrets/$name"
	fi
done

write_role_environment

printf 'written: certificate authority, broker certificate, and passwords under %s\n' "$deploy_dir"
report
