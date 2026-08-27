# ADR-0115: Record normalized events and serve session-neutral replay

- **Status:** Accepted
- **Date:** 2026-08-25
- **Deciders:** Alex Anglin
- **Supersedes in part:** ADR-0094 and ADR-0112

## Context

ADR-0094 requires one immutable validator output before replay readiness, while the current replay bundle
contains `sessionId` and includes it in integrity material. ADR-0113 requires a fresh session for every
replay start and reset. A validator cannot emit immutable bytes that already contain every future session,
and the API must not rewrite and re-checksum validator output.

The recorder also needs a narrow public-safe format. Raw CloudEvents contain transport identities and
metadata the dashboard reducer neither needs nor exposes. The authoritative export order is the audit
ordinal, not broker arrival or file order.

## Decision

Remove `sessionId` from `dashboard-replay-bundle/v1`. The bundle contains scenario identity, prepared
initial state and witness, ordered normalized dashboard events, expected final digest, and integrity only.
Its checksum covers the complete canonical bundle with only `integrity.checksum` omitted. Replay session
identity exists solely in the start/reset response, durable session mapping, and
`/api/v1/replays/{sessionId}` lookup. A valid lookup serves the replay validator's exact bytes.

Export `recordings/v1/wilderness-missing-person.r1.ndjson` from one selected synthetic mission's
authoritative audit rows. The recording is UTF-8 without BOM, LF-only, has no blank lines, and ends with
one LF. Its first canonical JSON line is a version-one header containing scenario identity, prepared
state and witness, expected final digest, event count, checksum algorithm, and checksum. Every subsequent
canonical line contains version one and exactly one `OrderedDashboardEvent`.

The recording checksum is lowercase SHA-256 over the canonical header with its checksum member omitted,
followed by LF, then every canonical record followed by LF. The input is bounded to 1 MiB, 64 KiB per
line, 512 events, and canonical nesting depth 16. Duplicate keys, floats, unsupported versions or kinds,
blank lines, missing final LF, truncated lines, count or checksum mismatch, and trailing content refuse.

The exporter constructs each record from the validated normalized dashboard allowlist. Transport host,
VPN, client, queue, envelope source, trace, authorization, credential, prompt, model, and person fields
are absent by construction. This first format is restricted to the synthetic dashboard slice; generalized
CloudEvent recording and infrastructure pseudonymization remain follow-on work.

Write only below the configured local recording root. Refuse symlinks and arbitrary paths. Create an
exclusive temporary file, write and flush the complete validated output, fsync it, then atomically rename
it. A failure leaves no accepted partial file and never overwrites a committed recording.

The one-shot validator reads the committed recording, validates and folds it through production
contracts, verifies every witness plus the final digest across ten independent folds, and writes exactly
one session-neutral bundle. It runs with no network, a read-only root and input, bounded writable output,
no credentials, and no live broker, store writer, model, Agent Mesh, approval, command, rescue, or
escalation imports. Invalid input writes no bundle.

## Consequences

- Fresh sessions can share immutable validated content without changing its bytes or integrity.
- Public replay input contains mission semantics but no transport identity.
- The first recording format is deliberately not a general audit export.
- Exact-byte and atomic-file rules add filesystem and container-isolation tests.

## Alternatives considered

- **Inject a session identifier after validation.** Rejected because it changes integrity-covered bytes.
- **Serve NDJSON to the browser.** Rejected because the isolated validator must close the complete input
  before playback.
- **Record raw CloudEvents.** Rejected because their transport metadata is unnecessary and widens the
  sanitization boundary.
- **Write directly to the final path.** Rejected because interruption could create an apparently valid
  partial artifact.
