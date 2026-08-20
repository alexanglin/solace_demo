# ADR-0046: Secure the local broker with a generated per-checkout certificate authority

- **Status:** Accepted
- **Date:** 2026-08-20
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

`AGENTS.md` requires every broker connection to "use `tcps` with certificate and hostname validation",
`.env.example` ships `tcps://` URLs, and the configuration validator of
[ADR-0032](0032-agent-mesh-semantic-configuration-validator.md) refuses an Agent Mesh broker URL that is
not `tcps` or WSS on port 443. A Solace Cloud service satisfies that with a publicly trusted
certificate. The PubSub+ software event broker container satisfies none of it on its own: its TLS
listeners stay dark until a server certificate is supplied through the `tls_servercertificate_filepath`
configuration key, which expects a PEM file holding the private key and the certificate, and Solace
documents a secrets mount at `/run/secrets` as the way to supply it.

The clients validate against a directory, not a bundle. The pinned Python API's
`solace.messaging.tls.trust-store-path` names "the location of the trust store files", and the Solace AI
Connector inside Agent Mesh reads the same path from the `TRUST_STORE` environment variable when the
configuration does not set `trust_store_path`. The Java Event Management Agent carries its own
truststore mechanism.

The repository already refuses to carry key material: `.gitignore` excludes `*.pem`, `*.key`, and any
`secrets/` directory, and the `detect-private-key` and gitleaks hooks block a key that is staged anyway.
[ADR-0043](0043-docker-broker-with-solace-cloud-showcase.md) made the container the broker for every
gated path, so a TLS story for it is not optional.

## Decision

**Each checkout generates its own certificate authority, and the broker's server certificate is
issued from it.** `scripts/broker-secrets.sh`, a POSIX shell script over `openssl`, produces:

- a certificate authority — an EC P-256 key and a self-signed certificate, valid 365 days — whose
  public certificate is written to `deploy/certs/ca.pem` and whose key stays under `deploy/secrets/`;
- a server certificate for the broker — an EC P-256 key and a certificate signed by that authority,
  valid 365 days, with subject alternative names `DNS:localhost`, `DNS:broker`, and `IP:127.0.0.1` so
  hostname validation holds from the host and from inside the Compose network — concatenated as key
  then certificate into `deploy/secrets/broker-server.pem`, the file the broker's
  `tls_servercertificate_filepath` names;
- the broker admin, Postgres, and SEMP-discovery passwords, 32 random bytes each from `openssl rand`,
  one file apiece under `deploy/secrets/`, which Compose mounts at `/run/secrets/`.

Every file is created with mode 0600, the script refuses to overwrite existing material unless asked to
rotate, and it prints paths and certificate fingerprints only — never a key. `deploy/certs/` holds
public material alone and is the trust-store directory: Compose mounts it read-only into every client
container at `/etc/aerial-rescue/certs`, `.env.example` sets `TRUST_STORE` to that path, and host
tooling points the Python API at `deploy/certs/`. The showcase profile sets `TRUST_STORE` empty so the
Cloud service's publicly trusted chain is validated against the system store. Hostname validation is
never disabled, and expiry is never ignored.

Until the Event Management Agent's truststore path is proven in a live run, that agent reaches the
broker's SEMP over plaintext **inside** the Compose network only; the plaintext SEMP port is never
published, which the compose policy gate enforces.

## Consequences

- `AGENTS.md` section 6 and the validator's rule stay exactly as written; the container meets them
  rather than being exempted from them.
- No key, certificate, or password is ever tracked, and rotation is one command.
- The local run and the showcase run share one validation code path and differ only in which trust
  store they are given.
- **Every checkout has a different authority.** A recorded fixture or a screenshot can never depend on
  a certificate value, and a second workstation cannot reuse the first one's material.
- Broker Manager over `https://localhost:1943` warns in the browser until the operator trusts
  `deploy/certs/ca.pem` or accepts the warning; the contributor guide says so.
- A 365-day validity means the material expires during the project's life; the script's rotate mode
  is the answer, and the validity is an operating parameter.
- `openssl` on the host is a prerequisite. macOS ships LibreSSL under that name, and the first live run
  must confirm the script's flags against it.
- The Event Management Agent's SEMP connection is the one plaintext path in the stack; it is internal
  and non-gating, and closing it is named as follow-up work.

## Alternatives considered

- **Plain `tcp://` on loopback for the local container.** Rejected: it contradicts `AGENTS.md`
  section 6, `.env.example`, and the validator, each of which would need amending, and the stricter
  rule governs.
- **A committed development authority.** Rejected: a private key in a public repository is a
  credential whatever it signs, and two hooks exist to stop exactly that.
- **`mkcert`.** Rejected: it installs its authority into the operating-system trust store, a side
  effect outside the repository, and adds a tool the project does not otherwise need.
- **A publicly trusted certificate.** Rejected: the broker has no public name, and a certificate for
  `localhost` cannot be issued by a public authority.
- **The broker's own self-signed certificate with validation relaxed.** Rejected: relaxing validation
  is the thing the rule forbids.
