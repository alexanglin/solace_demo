# ADR-0048: Scan every stack image and the deploy configuration with Trivy, blocking on fixed HIGH and CRITICAL findings under the waiver registry

- **Status:** Accepted
- **Date:** 2026-08-20
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

The dependency audit covers the two Python locks. It covers nothing else, and since
[ADR-0044](0044-docker-compose-runtime-with-official-agent-mesh-image.md) the runtime is five pulled
container images and two built ones: the PubSub+ broker, Postgres, the official Agent Mesh image and
the derived one, the Python application base and image, and the Event Management Agent. Each carries an
operating-system package set and, for the Java and Python images, language packages that no gate
examines. The advisory [ADR-0047](0047-override-the-asteval-pin-to-close-cve-2026-55244.md) closes in
the lock is still present inside the official Agent Mesh image, and nothing would have said so.

Three rules shape any scanner here. Docker stays out of the commit path — the hook configuration
records twice that a `language: docker_image` hook was rejected for that reason — so a scanner that
pulls images cannot be a local hook. [ADR-0025](0025-narrow-ruff-subprocess-waivers.md) confines
`subprocess` to four reviewed Python owners, so a scanner is launched by a shell script and its report is
adjudicated by a pure gate, exactly as `scripts/hooks/deps/dependency-audit.sh` launches `pip-audit`
and hands the JSON to `tools/dependency_waiver_gate.py`. And
[ADR-0026](0026-expiring-dependency-waivers.md) fixes how an accepted advisory is recorded: an expiring,
reviewed waiver keyed on domain, package, version, and identifier, enforced in both directions.

Trivy 0.74.0 scans container images for operating-system and language-package advisories, scans
Dockerfiles for misconfiguration, writes a versioned JSON report, and is a single binary published by
Homebrew and installed in GitHub Actions by `aquasecurity/setup-trivy`. Its report carries
`SchemaVersion` 2, a `Results` array that is absent on a clean scan, and for each result the
`Vulnerabilities` with `VulnerabilityID`, `PkgName`, `InstalledVersion`, `FixedVersion`, and `Severity`,
and the `Misconfigurations` with `ID`, `Severity`, and a `Status` of `PASS` or `FAIL`. Its built-in
misconfiguration checks are Dockerfile checks; a compose file is read only by custom checks, of which
this repository has none — the compose file is held by the gate of
[ADR-0045](0045-fail-closed-compose-policy-gate.md) instead.

A zero-tolerance rule, which the Python locks meet, cannot be met by a Debian or Alpine base image:
every such image carries advisories the distribution has not fixed and for which no action exists
except waiting. The user chose the threshold: a finding blocks when it is HIGH or CRITICAL **and** a
fixed version exists; everything else is reported and does not block.

## Decision

**Trivy 0.74.0 scans every image the stack runs and the Dockerfiles under `deploy/`, and its findings
are adjudicated by the dependency waiver gate under the existing registry.**

- **Where it runs.** `trivy config deploy` runs at pre-push through `scripts/hooks/deploy/trivy-config-full.sh`,
  armed by the same tracked-or-unignored listing rule the compose policy gate uses and failing closed
  with `MISSING: trivy` when the binary is absent; `brew install trivy` becomes a documented
  prerequisite. `trivy image` runs only in continuous integration, after `docker compose build` has
  produced the two derived images, through `scripts/security/scan-images.sh`, once per image the
  inventory names. The shell scripts do every launch; the gate never does.
- **What Trivy is told.** Never `--ignore-unfixed` and never `--severity`: the report must contain
  every finding so the gate, not the scanner, applies the policy and so the unfixed and lower-severity
  findings remain visible. Trivy's exit status is ignored in favour of the written report, as the
  audit already does for `pip-audit`; a run that does not write a report fails.
- **How findings are adjudicated.** The gate gains `--source trivy`. An image's findings are
  adjudicated in the domain `image:<repository>` — the repository without tag or digest, so a waiver
  survives a digest bump and is keyed on the package's installed version instead. Misconfigurations
  are adjudicated in the domain `deploy-config`, keyed on the Dockerfile path, the version `config`, and
  the check identifier. A vulnerability blocks when its severity is HIGH or CRITICAL and its
  `FixedVersion` is non-empty; a misconfiguration blocks when its severity is HIGH or CRITICAL and its
  status is `FAIL`. A blocking finding without a current waiver fails the gate exactly as an
  unwaived Python advisory does; every other finding is printed as one `INFO:` line and blocks
  nothing. A waiver for a finding that does not block is stale, because nothing needs it. The
  thirty-day lifetime, the twenty-character reason, the reviewer, and the both-direction rule are
  unchanged. Each loader refuses the other tool's report shape — a Trivy report read as pip-audit, or
  the reverse, is an error, not a clean run.

## Consequences

- Seven images and two Dockerfiles are now held to a policy with a number, and the finding ADR-0047
  left inside the official Agent Mesh image is reported on every run instead of being unknown.
- **The image policy is weaker than the Python policy by design.** An unfixed HIGH advisory in a base
  image does not block; it is printed. That is the honest threshold for images the project does not
  build, and the daily run keeps the list in view.
- A vendor's severity rating can change after a finding is first reported, turning an informational
  line into a blocking one overnight; the daily run surfaces that, and a waiver is the response if
  nothing can be upgraded.
- A base-image advisory appears under two domains, the base image and the image built from it, and
  needs two waivers. Accepted for now; collapsing the derived image's findings onto its base is a
  later simplification.
- The tag-free domain and the `config` version let a waiver outlive the digest or the line it was
  written against. The installed package version still changes on a real upgrade, which is the case
  that must invalidate it.
- The commit path is unchanged; the pre-push stage gains one scan over two small files, and
  continuous integration gains image pulls of roughly 2.6 GB per run.
- pre-commit hides the output of a passing hook, so the Trivy hook is registered `verbose: true` and
  its `INFO:` lines are shown on a pass.

## Alternatives considered

- **Trivy's own `.trivyignore` file, which supports an expiry.** Rejected: it makes Trivy stop
  reporting the finding, so a stale ignore can never be detected — the same reason ADR-0026 rejected
  passing `--ignore-vuln` to `pip-audit` — and it carries no reviewer, reachability, or compensating
  control.
- **Grype and Syft.** Rejected: two binaries, and no Dockerfile misconfiguration scanner.
- **Docker Scout.** Rejected: it needs a Docker Hub account, and continuous integration holds no
  credential by policy.
- **Zero tolerance across every severity.** Rejected: no Debian or Alpine base image is ever green
  under it, so the gate would be permanently red or permanently waived.
- **CRITICAL only.** Rejected: the user chose to block on HIGH as well.
- **Image scans at pre-push.** Rejected: gigabytes of pulls on every push, and Docker in the hook
  path.
- **Blocking on unfixed findings.** Rejected: there is no action a contributor can take, so the gate
  could only be cleared by a waiver that says so, which is paperwork rather than a control.
