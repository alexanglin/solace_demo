# ADR-0133: Persist the one-shot replay handoff

- **Status:** Accepted
- **Date:** 2026-08-26
- **Deciders:** Alex Anglin
- **Supersedes in part:** ADR-0094 and ADR-0117

## Context

The isolated replay validator must exit successfully before dashboard replay readiness is evaluated. The
initial Compose implementation represented its shared named output volume with the local driver's
`tmpfs` options. That looked ephemeral and capacity-bounded in static policy, but it broke the required
handoff: the validator was the only container mounting the volume while it ran. Docker unmounted the
memory filesystem when the one-shot container exited, then mounted a fresh empty filesystem when the
dependent dashboard API started. The validator reported success while replay readiness truthfully
reported `validated-replay-unavailable`.

The replay document is already bounded by the recorder's input, line, event, depth, output, and checksum
rules. Kernel-backed transient storage is not the owner of those content bounds. The disposable Compose
project is the owner of the handoff's lifetime.

## Decision

Use an ordinary project-scoped named volume for `validated-replay`. The one-shot validator is its only
writer and mounts it at the fixed output directory. The dashboard API mounts the same volume read-only at
the fixed replay directory and does not start until validation exits successfully. Invalid validation
writes no accepted output. The validator remains networkless, credential-free, non-root, read-only apart
from the output mount, and bound by the normalized-recording and replay-bundle limits.

The volume is ephemeral operationally because the production E2E run uses a unique disposable Compose
project and removes only that verified project's volumes during cleanup. It is persistent across the
intentional one-shot-to-long-running container boundary. Static packaging tests reject local-driver
`tmpfs` options for this volume, and live readiness must prove the dashboard can read the exact validated
bundle after the validator container has exited.

This correction does not change the dashboard Unix-socket volume or recorder-readiness volume. Their
long-running producers and consumers overlap while mounted, so their existing memory-backed lifetime has
a real producer-to-consumer path.

## Consequences

- A successful one-shot result now survives long enough for its ordered dependent to consume it.
- Replay output capacity is enforced by the validator and contracts rather than by a mount that erases
  the result at the dependency boundary.
- An interrupted disposable run can leave the synthetic bundle in that project's named volume until the
  exact project cleanup runs. It contains no credential, operational mission, or tenant data.
- Operators must continue to verify project labels before removing the disposable project's volumes; no
  global prune or shared-volume cleanup is introduced.

## Alternatives considered

- **Keep `tmpfs` and run validator and dashboard concurrently.** Rejected because it weakens the accepted
  `service_completed_successfully` gate and exposes a partial-output race.
- **Copy the bundle through a bind-mounted repository directory.** Rejected because it dirties the
  checkout, broadens host write authority, and complicates secret/cache hygiene.
- **Bake the validated output into the image.** Rejected because validation would no longer be an isolated
  startup gate over the committed recording used by the run.
- **Serve the source NDJSON when the bundle is absent.** Rejected because it bypasses the zero-network
  validator and makes a false ready state possible.
