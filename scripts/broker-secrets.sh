#!/usr/bin/env sh
# Generate the per-checkout certificate authority, the broker's server certificate, and the
# stack's passwords (docs/adr/0046-generated-local-certificate-authority.md).
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
#   secrets/semp-discovery-password 32 random bytes, hexadecimal
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

report() {
	printf 'authority:  %s\n' "$certs/ca.pem"
	openssl x509 -noout -fingerprint -sha256 -in "$certs/ca.pem"
	printf 'broker:     %s\n' "$secrets/broker-server.pem"
	openssl x509 -noout -fingerprint -sha256 -in "$secrets/broker-server.crt"
	openssl x509 -noout -text -in "$secrets/broker-server.crt" |
		grep -A1 'Subject Alternative Name' | tail -n 1 | sed 's/^[[:space:]]*//'
	printf 'passwords:  %s/{broker-admin,postgres,semp-discovery}-password\n' "$secrets"
}

complete=true
for required in "$certs/ca.pem" "$secrets/ca.key" "$secrets/broker-server.key" \
	"$secrets/broker-server.crt" "$secrets/broker-server.pem" \
	"$secrets/broker-admin-password" "$secrets/postgres-password" \
	"$secrets/semp-discovery-password"; do
	[ -f "$required" ] || complete=false
done
if [ "$complete" = true ] && [ "$rotate" = false ]; then
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
for name in broker-admin-password postgres-password semp-discovery-password; do
	openssl rand -hex 32 | tr -d '\n' >"$work/$name"
done

mkdir -p "$certs" "$secrets"
chmod 755 "$certs"
chmod 700 "$secrets"
cp "$work/ca.pem" "$certs/ca.pem"
chmod 644 "$certs/ca.pem"
for name in ca.key broker-server.key broker-server.crt broker-server.pem \
	broker-admin-password postgres-password semp-discovery-password; do
	cp "$work/$name" "$secrets/$name"
	chmod 600 "$secrets/$name"
done

printf 'written: certificate authority, broker certificate, and passwords under %s\n' "$deploy_dir"
report
