# ADR-0031: Reject the google-adk version override and waive PYSEC-2026-344

- **Status:** Accepted
- **Date:** 2026-08-19
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

The open-questions register in [README.md](README.md) recorded that `google-adk` 1.18.0 carries an unauthenticated remote-code-execution advisory, and required that a `[tool.uv] override-dependencies` bump "must be tried against the black-box compatibility suite before a waiver is accepted". Phase 0 pinned Agent Mesh 1.28.7, which armed the dependency audit and made that question due.

The audit reports **11 distinct advisories across 5 packages** in the `agent-mesh` domain, and none in the application workspace. The advisory in question is now identified as **PYSEC-2026-344**, not `CVE-2026-4810` as the register named it: `pip-audit` merges aliases and reports the PYSEC identifier first, so the waiver registry — which matches on the reported identifier — must key on the PYSEC form or fail in both directions at once.

**All five affected packages are pinned exactly by Agent Mesh 1.28.7**: `cryptography==48.0.1`, `starlette==0.49.1`, `google-adk==1.18.0`, `python-multipart==0.0.30`, `setuptools==80.10.2`. There is no transitive bump available for any of them; every fix requires overriding a vendor pin.

The override was attempted, as the register demanded. `uv lock --project agent-mesh` with `override-dependencies = ["google-adk==1.28.1"]` reports, verbatim:

```text
  × No solution found when resolving dependencies for split (markers:
  │ python_full_version == '3.13.*' and platform_machine == 'arm64' and
  │ sys_platform == 'darwin'):
  ╰─▶ Because google-adk==1.28.1 depends on google-genai>=1.64.0,<2.0.0 and
      solace-agent-mesh>=1.28.7 depends on google-adk==1.28.1, we can conclude
      that solace-agent-mesh>=1.28.7 depends on google-genai>=1.64.0,<2.0.0.
      And because solace-agent-mesh>=1.28.7 depends on google-genai==1.49.0
      and your project depends on solace-agent-mesh==1.28.7, we can conclude
      that your project's requirements are unsatisfiable.
```

`google-adk` 1.28.1 additionally requires `fastapi>=0.124.1` against Agent Mesh's `fastapi==0.120.1`, so satisfying it needs at least three simultaneous overrides of the vendor's exact pins. `solace-agent-mesh` 1.28.7 is the latest release on PyPI as of 2026-08-19, so the safe upgrade `AGENTS.md` section 6 prefers does not exist either.

## Decision

**Reject the override.** Record PYSEC-2026-344 as an expiring waiver in `dependency-waivers.toml` instead, alongside the other ten advisories.

A single-package override does not resolve. A three-package override — `google-adk`, `google-genai`, and `fastapi` — would resolve only by replacing three of the vendor's exact pins, bumping `google-genai` by fifteen minor versions. That graph is no longer the artifact under test: [TESTING.md](../TESTING.md) defines the black-box compatibility class as running against *the exact Agent Mesh and plugin wheels*, and the compatibility probe can detect a moved symbol but cannot detect a behavioural change in FastAPI's request handling or a schema change in `google-genai`. Replacing three pins to satisfy a security scanner, and calling the result the pinned runtime, would be a worse outcome than a recorded, expiring, human-reviewed acceptance.

The compensating control this waiver depends on is the one [ARCHITECTURE.md](../ARCHITECTURE.md) already requires: the upstream Web UI is bound to loopback, the reference deployment is a single workstation with no public ingress, and no exposed route reaches the ADK. The waiver is bounded at 30 days by [ADR-0026](0026-expiring-dependency-waivers.md), so this acceptance has to be re-taken monthly rather than assumed.

## Consequences

- The register's open question is settled with executable evidence rather than an assumption, and the evidence is quoted here so a future reader does not have to re-run it.
- **The project knowingly ships a pinned dependency with an unauthenticated remote-code-execution advisory and no available fix.** That is a real release risk, not a scanner artifact, and it is now written down as one. It must appear in the release-readiness assessment rather than only in a machine-readable registry.
- The waiver expires within 30 days. The audit turns red around 2026-09-18 unless someone re-reviews, which is the intended pressure and also a recurring maintenance cost nothing currently reminds anyone about.
- Upgrading Agent Mesh becomes the only realistic path to clearing this, so the project now has a security reason, not just a feature reason, to track upstream releases.
- Flattening PYSEC-2026-344 into the same registry as a `setuptools` glob-matching bug loses the severity difference. This record exists so the difference is preserved somewhere a reader will find it.
- The register named the wrong identifier. Any other decision written against a CVE alias is suspect for the same reason, and the registry's identifier convention is now a documented trap.

## Alternatives considered

- **Override `google-adk` alone.** Rejected on evidence: unsatisfiable, output quoted above.
- **Override `google-adk`, `google-genai`, and `fastapi` together.** Rejected: it replaces three exact vendor pins, so what is verified is no longer the pinned runtime, and the compatibility probe cannot detect the behavioural drift that introduces.
- **Upgrade Agent Mesh.** Rejected: 1.28.7 is the latest release, so there is nothing to upgrade to.
- **Remove the dependency.** Rejected: `google-adk` is the agent runtime Agent Mesh is built on. Every agent in the mesh is an ADK agent.
- **Fail the release on the advisory.** Rejected for this phase: the reference deployment has no public ingress and no route to the vulnerable surface, and [ADR-0026](0026-expiring-dependency-waivers.md) exists precisely so that an accepted risk is bounded and re-reviewed rather than either ignored or treated as fatal. It remains the right answer if the deployment posture ever changes.
