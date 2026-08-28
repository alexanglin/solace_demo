# ADR-0195: Run the event monitor as the retained log's owner

- **Status:** Accepted
- **Date:** 2026-08-28
- **Deciders:** Alex Anglin
- **Supersedes in part:** ADR-0173, as to the user identity the monitor runs under

## Context

[ADR-0173](0173-follow-the-retained-broker-event-log-without-runtime-authority.md) has the
credentialless, networkless `broker-event-monitor` follow the broker's retained JSON event log
through the `broker-storage` volume's `jail/logs` subpath, mounted read-only. The application image
runs every process as the project's numeric user 10001.

The merged runtime's first composition (2026-08-28) was the first time that monitor ran against a
broker writing JSON events: it exited 3 after `BROKER_EVENT_SOURCE_FAILED`. The pinned broker image
creates `jail/logs` as mode `0700` and its files as `0600`, all owned by its own numeric user
1000001, so no other user can list the directory, let alone read `event.log`; a read as 10001 from
the application image on that volume is refused, and a read as 1000001 succeeds while the image's
virtual environment still imports.

## Decision

`broker-event-monitor` declares `user: "1000001:10001"`: the retained log's owner as its user, the
project's group as its group. Everything else ADR-0173 requires stands: the read-only subpath mount,
no network, no capabilities, a read-only root filesystem, and no credential. The value is the pinned
broker image's user and is pinned by a conformance test; a broker image change that moves it must
move this value with it.

## Consequences

- The monitor can read the log it exists to follow; the JSON event stream reaches the alert processor.
- The monitor holds the broker's file identity on one read-only mount and nothing else; it gains no
  write path, network, or capability.
- Rejected: loosening the broker's file modes, which the pinned image owns; mounting the whole volume,
  which ADR-0173 already rejected; and giving the monitor a credential, which ADR-0173 exists to avoid.
