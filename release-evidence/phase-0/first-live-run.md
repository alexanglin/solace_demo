# Phase 0 evidence: the first live run of the compose stack

- **Recorded:** 2026-08-21
- **Host:** Apple Silicon, macOS arm64. Docker Desktop, 7.652 GiB allocated to the virtual machine.
- **Scope:** the **default profile only** — the PubSub+ broker container and Postgres. This does
  **not** cover the `mesh`, `services`, or `event-portal` profiles, the Ollama models, SEMP identity
  and queue provisioning, or the Solace Cloud showcase service. None of those was exercised.

Redaction: no credential, password, private key, broker URL, or tenant identifier appears here. The
generated material lives under `deploy/secrets/`, which is untracked, and only public certificate
fingerprints and subject alternative names are reproduced below.

## What was run

```sh
PATH=/usr/bin:/bin scripts/broker-secrets.sh
docker compose --env-file .env -f deploy/compose.yaml up --detach --wait
```

`PATH` is forced at `/usr/bin` on purpose: it selects macOS's LibreSSL 3.3.6 over the Homebrew
OpenSSL 3.6.2 that otherwise shadows it, which is the case
[ADR-0046](../../docs/adr/0046-generated-local-certificate-authority.md) asked the first live run to
confirm. `just` is not installed on this workstation, so the recipes were run as the commands they
wrap; the `justfile` header already states that hooks and continuous integration call the scripts
directly for exactly this reason.

## Result

Both services reached `healthy` and `up --wait` returned 0 in **40.75s**, including the pull of both
images.

| Service | Image | State |
| --- | --- | --- |
| `broker` | `solace/solace-pubsub-standard:10.26.0.8799@sha256:05f80ec…4698f` | Up, healthy |
| `postgres` | `postgres:17.11-trixie@sha256:e38411…50449` | Up, healthy |

Published bindings, read back from the running containers:

```text
broker:    127.0.0.1:1943->1943/tcp, 127.0.0.1:55443->55443/tcp
postgres:  127.0.0.1:5432->5432/tcp
```

Memory in use at rest, default profile:

| Container | Resident | Share of the 7.652 GiB allocation |
| --- | --- | --- |
| `broker` | 1.543 GiB | 20.16% |
| `postgres` | 35.71 MiB | 0.46% |

## Questions this settles

- **The broker healthcheck's `curl` exists inside the image.** `/usr/bin/curl`, curl 7.76.1
  (`aarch64-redhat-linux-gnu`, libcurl/7.76.1, OpenSSL/3.5.5). The container reaching `healthy` is
  the behavioural proof; the documented `/dev/tcp` fallback is not needed. The image is native
  arm64, not emulated.
- **The generated authority works, under LibreSSL.** `scripts/broker-secrets.sh` ran with LibreSSL
  3.3.6 and produced a certificate the broker serves on both published ports. Authority fingerprint
  `3D:F2:47:66:C3:BE:F5:38:9F:6E:62:75:BC:A6:92:FF:B2:C5:AF:B7:99:6C:02:D0:FE:77:D7:F6:A6:28:47:0E`;
  broker certificate `7C:E0:6B:9C:C5:0E:67:79:1A:BE:F0:0F:32:D0:F0:DF:C5:46:11:0E:BC:91:5F:B7:F6:26:5F:5A:49:27:A0:00`;
  subject alternative names `DNS:localhost, DNS:broker, IP Address:127.0.0.1`.
- **TLS validates without anything relaxed.** `tests/phase0/test_first_live_stack.py` completes a
  full handshake against 55443 and 1943 with chain verification against `deploy/certs/ca.pem` and
  hostname checking left on, and reads back all three subject alternative names. Three probes, all
  passing; all three failed with `ConnectionRefusedError` before the stack was started.
- **Every published port is on loopback.** What the compose policy gate asserts about the file is
  now also observed on the running containers.
- **Postgres starts from a file-sourced password.** `pg_isready -U "$POSTGRES_USER" -d
  "$POSTGRES_DB"` succeeds, so the secret indirection and the environment names agree.

## What this run does not settle

- The `mesh` profile. `agent-mesh/configs/` does not exist, so the container would start with no
  configuration and its `/readyz` healthcheck could not pass. The in-container plugin-compatibility
  probe that [ADR-0044](../../docs/adr/0044-docker-compose-runtime-with-official-agent-mesh-image.md)
  requires before that profile may be called supported has not been run.
- The `services` profile. Each container's command is an import probe that exits, so `--wait` cannot
  converge; the profile is documentation, not live behaviour.
- The `event-portal` profile. Its connection file is produced in the Solace Cloud console and no
  clean checkout can obtain it.
- SEMP identity, ACL profile, and queue provisioning, and the fleet's connection count against the
  Developer-class service's limit of 100.
- Full-stack memory. Only the default profile was measured; Agent Mesh's 3.92 GB image and the
  emulated Event Management Agent are the two that make the figure interesting, and neither ran.
- Ollama. The daemon is running and holds `llama3:8b`, but no model is pinned and the model-lock
  representation is still an open question in the decision log.

## One environment finding worth recording

The first attempt failed with `write /var/lib/docker/tmp/GetImageBlob…: no space left on device`.
Docker Desktop's virtual disk was at 59 GB against a 60 GB cap, holding 43.6 GB of images and 28.66
GB of build cache from unrelated projects. Reclaiming the build cache and 19 dangling layers freed
42.24 GB, after which the pull succeeded unchanged.

This is not a defect in the stack, and it is not a number the project controls. It is recorded
because the reference environment is part of the reproducibility claim: a clean checkout on a
workstation whose Docker disk is full fails at the pull with an error that says nothing about this
repository.
