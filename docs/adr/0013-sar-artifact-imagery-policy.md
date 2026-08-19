# ADR-0013: The detection target is SAR artifacts, never photographs of real people

- **Status:** Accepted
- **Date:** 2026-08-18

## Context

The scenario requires the vision model to analyse "prepared wilderness imagery" and find a missing person, while the project's own safety rules forbid identifying individuals and require every committed asset to be synthetic or public-domain with full provenance. Those two requirements collide directly: a public-domain photograph of a person in wilderness still depicts a real, identifiable individual who did not consent to appearing in a search-and-rescue simulation.

In a public repository this is effectively irreversible — once such an image is committed, removing it requires rewriting Git history.

## Decision

The detection target is **search-and-rescue artifacts**: a high-visibility jacket, tarp, pack, tent, reflective panel, or disturbed ground, composited onto public-domain wilderness backgrounds.

**Photographs of real people are forbidden regardless of claimed license**, as are photorealistic AI-generated faces.

Every image carries a per-scenario asset record giving source URL, verbatim license text, retrieval date, checksum, compositing-script hash, and an explicit statement that no identifiable person is depicted. This is a hard **entry gate** on the edge-intelligence phase, not an exit check.

Thermal evidence is represented as synthesised structured data rather than imagery, so no thermal source needs to be licensed or defended.

## Consequences

- The privacy problem is eliminated at the source rather than mitigated.
- The scenario becomes **more** operationally realistic, not less: real aerial search overwhelmingly finds equipment, shelter, and ground disturbance rather than a clearly visible person.
- The demonstration loses the emotional immediacy of a visible human figure. That is an acceptable cost.
- An asset-preparation step is required — sourcing backgrounds and compositing artifacts — with its own reproducibility obligation via the committed compositing script.
- The vision model's prompts and output schema describe artifact classes rather than person detection, which also keeps the system clear of anything resembling person identification.

## Alternatives considered

- **Faceless rendered silhouettes.** Rejected for the initial release: keeps the "person" narrative but adds a 3D asset pipeline and still invites scrutiny on a public repository.
- **Unresolvable synthetic aerial frames** where a subject is a few ambiguous pixels. Rejected: the most honest depiction of real search conditions, but it makes the model's output impossible to show convincingly.
- **Public-domain photographs of people.** Rejected: conflicts with the no-identification rule, and license status does not address consent to this use.
