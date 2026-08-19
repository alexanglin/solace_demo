# ADR-0016: Split the planning documents and add a precedence rule

- **Status:** Accepted
- **Date:** 2026-08-18

## Context

`AGENTS.md` and `docs/IMPLEMENTATION_PLAN.md` restate a large share of the same material — the technology decisions, the topic taxonomy, the HTTP API, the test taxonomy, and the definition of done all appear in both. The topic block is byte-identical in the two files.

Neither file says which one wins, and they have already diverged in practice: the health endpoint is described differently in each, and a model-fallback rule was stated with opposite trigger conditions. Instructing contributors to "keep both aligned" is a manual process that has already failed once.

Both files also carry material that is neither process guidance nor delivery sequencing — normative interface and safety semantics that belong somewhere they can be cited precisely.

## Decision

Give each document one job. `AGENTS.md` keeps process rules — how to work, TDD, review, commits, security hygiene. `docs/IMPLEMENTATION_PLAN.md` keeps sequenced delivery — phases, numeric exit criteria, risks, and the definition of done. Normative interface, architecture, safety, threat, testing, parameter, and operations content each move to their own document, so every fact has exactly one home and is referenced rather than restated elsewhere.

Add an explicit precedence rule naming which document is authoritative for which class of fact, and stating that where two documents conflict, the stricter statement governs until an ADR resolves it.

## Consequences

- A fact has one home, so drift becomes a broken link rather than a silent contradiction.
- Contributors and agents can be pointed at the one document that governs the work in front of them.
- More files to navigate, and a genuine risk of over-fragmentation if the split is taken too far.
- The migration is a large, mostly mechanical edit that must not lose content, and the two files' existing divergences must each be resolved deliberately rather than by whichever version is pasted last.

## Status note

Proposed. Approved in principle, then deferred pending a second review pass after both documents were substantially revised.

Accepted on 2026-08-19 after that review pass. The context paragraph above names two divergences that have since been resolved in the source documents; it is left unedited because this log preserves the reasoning as it stood when the decision was made.

## Alternatives considered

- **Merge everything into one document.** Rejected: a single file covering process, architecture, contracts, safety, testing, and delivery becomes unnavigable and cannot serve its different audiences.
- **Keep both files and add a precedence rule only.** Partially viable and much cheaper, but it leaves the duplication in place and therefore leaves the drift mechanism intact.
