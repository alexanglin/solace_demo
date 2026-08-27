# ADR-0162: Generate and validate per-image CycloneDX SBOMs

- **Status:** Accepted
- **Date:** 2026-08-25
- **Deciders:** Alex Anglin
- **Extends:** ADR-0048, ADR-0055, and ADR-0159

## Context

ADR-0159 makes supported-release maintenance a required Solace practice. The stack already pins every
image by tag and digest, scans every image with Trivy, and fails when a publisher moves a pinned tag to
a newer digest. It did not produce a software bill of materials (SBOM), so a release could not retain
the exact package inventory that was reviewed. A vulnerability report is not a substitute: it includes
only packages relevant to the scanner's advisory database and changes when that database changes.

Solace recommends a supported LTS release with current maintenance updates. On 2026-08-25 the review of
Solace's [release-management guidance](https://docs.solace.com/Release-Notes/Release-Version-Management-Best-Practices.htm),
[support dates](https://solace.com/support/support-dates-for-release-versions/), and official
[container tags](https://hub.docker.com/r/solace/solace-pubsub-standard/tags) found the repository's
exact `10.26.0.8799` build on the 10.26 LTS line current. The official Python client page and package
index likewise showed the pinned `solace-pubsubplus` 1.11.0 release current. Those observations are
dated evidence, not permission to float either pin.

Trivy 0.74.0, already pinned by ADR-0048, can generate CycloneDX 1.6 directly from a container image.
Its official [SBOM guidance](https://trivy.dev/latest/docs/supply-chain/sbom/) identifies the scanned
image as the root `container` component and records the packages as components. Merely trusting a zero
exit status would repeat the false-green class ADR-0019 forbids: an empty, malformed, stale-tool, or
wrong-image document could be described as an SBOM.

An SBOM also exposes a detailed image and package inventory. The current authorization permits local
files and repository changes, but does not authorize publishing that inventory to a GitHub artifact
store or another external destination.

## Decision

**Generate one validated CycloneDX 1.6 SBOM for every pulled and built stack image, without external
publication.**

- `scripts/security/generate-sboms.sh` consumes the same complete image inventory as the image scanner.
  It preserves each image's platform and remote-versus-local source, uses the established per-image
  timeout, and writes only to a caller-selected absent or empty directory. It never overwrites an
  existing artifact.
- `tools/sbom_gate.py` independently reads every generated document. It requires CycloneDX 1.6, a
  positive document version, a UUID URN, metadata from the pinned Trivy 0.74.0, an exact root-image
  name of type `container`, a nonempty package component inventory, dependency data, and unique
  component identities. It fails closed on missing or malformed evidence and does not print package
  names or document contents in refusal diagnostics.
- The image-scan continuous-integration job generates and validates the documents after building and
  scanning every image. It writes beneath the runner's temporary directory and does not upload them.
  Hosted execution remains unproved until a later authorized push runs that workflow.
- An operator may retain a local output directory as release evidence after applying the repository's
  public-information review. External upload, publication, attestation, signing, or registry attachment
  requires separate destination and payload authorization plus readback. No workflow silently adds it.
- The SBOM does not alter ADR-0055's advisory verdict. Package inventory, advisory reporting, and image
  pin freshness remain separate evidence with separate failure modes.

## Consequences

- Every supported image has a package inventory bound to the exact reference the stack names, rather
  than a vulnerability-database-dependent approximation.
- A stale Trivy producer, wrong root image, empty package inventory, duplicate component identity, or
  malformed document fails the job.
- The generation pass repeats image inspection after the vulnerability scan, increasing registry,
  Docker-engine, and runner time. The existing bounded per-image timeout and job budget still apply and
  must be remeasured if hosted execution reaches the budget.
- CI proves generation and validation but retains no downloadable SBOM. That is an intentional privacy
  boundary, not a claim that external release evidence exists.
- CycloneDX or Trivy format changes require an explicit tested gate update; a tool upgrade cannot
  silently change accepted evidence.

## Alternatives considered

- **Treat the Trivy vulnerability JSON as the SBOM.** Rejected because it is an advisory report, not a
  complete package inventory, and its contents depend on the current vulnerability database.
- **Trust Trivy's exit status and a nonempty file.** Rejected because neither binds the document to the
  expected image or proves a complete, current-tool CycloneDX structure.
- **Upload every SBOM from GitHub Actions.** Deferred because it publishes detailed package metadata to
  an external store. The destination, retention, access, and payload need explicit authorization.
- **Commit generated SBOMs.** Rejected because they are large, build-derived, and timestamped; the
  source image pins and deterministic generator are reviewable repository inputs.
