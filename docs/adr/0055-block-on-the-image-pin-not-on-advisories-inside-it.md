# ADR-0055: Block on the image pin, not on advisories inside a pinned image

- **Status:** Accepted
- **Date:** 2026-08-20
- **Deciders:** Alex Anglin
- **Supersedes:** the blocking rule of
  [ADR-0048](0048-scan-images-and-deploy-configuration-with-trivy.md) for `image:` domains only.
  Its `deploy-config` rule, its waiver contract, its Trivy pin, and its reporting all stand.

## Context

[ADR-0048](0048-scan-images-and-deploy-configuration-with-trivy.md) set the image policy before any
image had been scanned: a HIGH or CRITICAL finding **with a fixed version** blocks unless an expiring
waiver covers it. The reasoning was the one that governs a Python lock file, where a fixed version is
something the project can take by editing a pin.

The first run, in continuous integration on 2026-08-20, reported **307 distinct blocking findings**
across the seven images: 63 in `solace/event-management-agent`, 58 in `postgres`, 48 each in
`solace/solace-agent-mesh` and the derived `aerial-rescue/agent-mesh`, 38 each in `python` and the
derived `aerial-rescue/application`, and 14 in `solace/solace-pubsub-standard`. Roughly 180 are the
`util-linux` family of Debian packages, 51 are the Go standard library inside the Solace images, and
the remainder are Python packages in the vendor's own virtual environment.

Every one of them was checked against the registry on the same day:

| Pinned | Newest tag | Newest digest for the pinned tag |
| --- | --- | --- |
| `python:3.14.7-slim-trixie` | 3.14.7 is the newest 3.14.x | already pinned |
| `postgres:17.11-trixie` | 17.11 is the newest 17.x | already pinned |
| `solace/solace-agent-mesh:1.28.7` | 1.28.7 is the newest | already pinned |
| `solace/solace-pubsub-standard:10.26.0.8799` | — | already pinned |
| `solace/event-management-agent:1.9.9` | — | already pinned |

**There is no upgrade to take.** The "fixed version" Trivy reports is the version the *distribution*
published, not a version obtainable inside an image whose publisher has not rebuilt it. The project
pins by digest on purpose ([ADR-0044](0044-docker-compose-runtime-with-official-agent-mesh-image.md),
[ADR-0045](0045-fail-closed-compose-policy-gate.md)) and does not patch vendor images
([ADR-0007](0007-solace-first-implementation-policy.md)). The one remaining lever, an
`apt-get upgrade` layer in the two derived Dockerfiles, is precisely what hadolint's `DL3005` refuses
and would trade a pinned, reproducible image for a floating one.

So the rule as written can be satisfied in exactly one way: 307 waiver entries, each with a reason, a
reachability statement, a reviewer, and a 30-day expiry, re-reviewed monthly. Nobody reviews 307
entries a month. They would be rubber-stamped, and a registry of rubber stamps is worse than an honest
record, because it launders an unexamined risk through a control that looks examined.

The gate is measuring the wrong thing. It asks "does a fix exist for this package?" when the only
question the project can act on is "is this the newest image its publisher has released?"

## Decision

**An advisory inside an image is informational. A stale image pin blocks.**

1. Every vulnerability Trivy reports in an `image:` domain is reported as an `INFO:` line and never
   fails the scan, whatever its severity and whether or not a fixed version exists. The full list is
   printed on every run and kept by the daily workflow of
   [ADR-0051](0051-rescan-daily-and-let-dependabot-raise-pinned-updates.md).
2. A new blocking control, `tools/image_pin_gate.py`, driven by
   `scripts/security/check-image-pins.sh`, resolves the digest each pinned tag currently carries and
   fails when a pinned digest is not the newest one. That is the actionable signal the old rule was
   trying to be: when a publisher rebuilds an image to fix exactly these advisories, the pin goes
   stale and the gate says so.
3. `deploy-config` misconfigurations keep ADR-0048's blocking rule unchanged. The project writes those
   Dockerfiles, so a finding there is actionable by definition.
4. The dependency graph the project itself resolves keeps its zero-tolerance rule under `pip-audit`
   and the waiver registry ([ADR-0026](0026-expiring-dependency-waivers.md)). Nothing about
   [ADR-0047](0047-override-the-asteval-pin-to-close-cve-2026-55244.md) or the eleven reviewed waivers
   changes. That control, not the image scan, is where a Python advisory the project can fix is caught.

## Consequences

- The image scan stops blocking on findings nobody can act on, and starts blocking on the one thing
  anybody can: an out-of-date pin. The loop closes — a publisher rebuilds, the pin goes stale, CI
  fails, somebody bumps the digest, and the advisories go away because the image actually changed.
- No waiver is written for any of the 307. Their existence is recorded in `TECH_DEBT.md` as a
  standing, visible risk of running pinned third-party images, which is what it is.
- **This is a real reduction in enforcement, and it is worth naming.** A CRITICAL advisory in a base
  image no longer fails a build. What is left is a printed list on every scan, a daily re-scan, and a
  human reading it on the `TECH_DEBT.md` review date. The alternative was not more enforcement; it was
  the same list behind 307 signatures.
- The pin check needs a registry round trip per pulled image and fails closed when the registry cannot
  be reached, so a Docker Hub outage or an anonymous rate limit fails the scan. The job already pulls
  those images, so it is not a new dependency, but it is a new way for the job to go red.
- Should the project ever build an image from a base it controls, or vendor a runtime it can patch,
  this record is the one to revisit: at that point a package-level finding becomes actionable again.

## Alternatives considered

- **Write the 307 waivers.** Rejected above: unreviewable in practice, and a control that cannot be
  exercised honestly is worse than a documented acceptance.
- **Add wildcard or image-scoped waivers to the registry.** Rejected: a wildcard in a waiver registry
  is a permanent hole with a date on it, and [ADR-0026](0026-expiring-dependency-waivers.md) binds a
  waiver to an exact package, version, and advisory precisely so that it expires when the fact changes.
- **`apt-get upgrade` in the two derived Dockerfiles.** Rejected: hadolint `DL3005` forbids it, and it
  would replace a digest-pinned reproducible build with one whose contents depend on the day.
- **Keep blocking but only for the two images the project builds.** Rejected: their OS packages are
  inherited wholesale from the pinned base and are no more actionable there than upstream. The
  distinction that matters is not who ran `docker build`, it is whether a newer image exists.
- **Drop image scanning.** Rejected: the printed inventory is how anybody learns what is in these
  images, and the pin check depends on the same run.
