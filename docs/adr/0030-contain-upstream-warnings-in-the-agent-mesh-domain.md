# ADR-0030: Contain the pinned Agent Mesh runtime's upstream warnings

- **Status:** Superseded by [ADR-0034](0034-scope-agent-mesh-warning-filters-to-upstream-modules.md)
- **Date:** 2026-08-19
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

Phase 0 pinned `solace-agent-mesh==1.28.7`, `sam-event-mesh-gateway==1.1.0`, and `sam-event-mesh-tool==0.1.1` into the Python 3.13.15 project and probed whether the three independently released wheels work together. They do: all three resolve into one 251-package lock, the gateway's entry point loads against the runtime, the tool imports by module path, and every runtime symbol both plugins depend on is present and callable.

The probe suite nevertheless failed, and it failed for a reason that has nothing to do with compatibility. Both pytest projects set `filterwarnings = ["error"]`, and the pinned upstream code emits warnings:

- **`PydanticDeprecatedSince20`, 11 occurrences across four modules**, raised while importing Agent Mesh's own models: `solace_agent_mesh/tools/web_search/models.py` (4), `solace_agent_mesh/common/rag_dto.py` (4), `solace_agent_mesh/agent/tools/tool_result.py` (2), `solace_agent_mesh/agent/tools/tool_definition.py` (1). Agent Mesh 1.28.7 defines models with Pydantic's class-based `Config` against the `pydantic==2.12.5` it pins itself. Pydantic states the support is removed in V3.
- **`SyntaxWarning: "\@" is an invalid escape sequence`**, from `solace-pubsubplus`, reaching this domain transitively through `solace-ai-connector`. [ADR-0028](0028-untyped-solace-client-boundary.md) already recorded and contained this in the application workspace; the 3.13 domain resolves `solace-pubsubplus==1.9.0` rather than 1.11.0 and carries the identical defect.
- **`DeprecationWarning: datetime.datetime.utcnow() is deprecated`**, at `solace/messaging/messaging_service.py:192`, where it is a *default argument value* and so is evaluated when the class is defined, meaning any import of the client raises it.
- **`RuntimeWarning: Couldn't find ffmpeg or avconv`**, at `pydub/utils.py:170`, reached through `markitdown[all]==0.1.4`, which Agent Mesh pins for document conversion.

The measurement took three attempts to get right, and how it failed is itself the finding. A warm bytecode cache hid two of these, and the last does not depend on the code at all: `pydub` warns only when neither `ffmpeg` nor `avconv` is on `PATH`, so the same commit passes on a machine that has them and fails on one that does not. **The gate's verdict was a function of build-cache state and of unrelated system packages**, which is a stronger version of the failure mode ADR-0028 was written to eliminate. The complete set above was obtained only after deleting every `__pycache__` directory in the 251-package environment.

With warnings not escalated, all six probes pass. So the choice is not between a working domain and a broken one; it is about which signal the gate should carry.

One fact makes this domain different from the application workspace. **`agent-mesh/` contains no owned production source.** `[tool.uv] package = false`; the project exists to pin, install, and verify upstream code, and its only owned Python is the probe suite. An exemption scoped to this project therefore cannot conceal a defect in code this repository wrote, which is the objection that shaped ADR-0028's narrower approach.

## Decision

Keep `error` as the first entry of `filterwarnings` in `agent-mesh/pyproject.toml`, and exempt exactly the four warning classes the pinned upstream emits, each scoped as narrowly as the warnings machinery allows:

- `ignore:.*invalid escape sequence.*:SyntaxWarning`, matching ADR-0028's message-scoped exemption in the root workspace, for the same defect in the same distribution.
- `ignore::pydantic.warnings.PydanticDeprecatedSince20`, scoped to that exact warning class rather than to `DeprecationWarning` generally.
- `ignore:.*datetime.datetime.utcnow.*:DeprecationWarning`, scoped by message rather than by category.
- `ignore:.*ffmpeg or avconv.*:RuntimeWarning`, scoped by message.

Every other warning from every other source remains an error in this domain.

**Record all four as Phase 0 findings rather than treating them as resolved.** They are upstream technical debt this project has now measured: Agent Mesh 1.28.7 will not import cleanly under Pydantic V3, and the Solace client will neither compile cleanly under a Python release that promotes the invalid-escape warning to an error nor import cleanly once `datetime.utcnow` is removed. Those bound how long the current pins remain viable. The `pydub` warning is different in kind: it reports a missing optional system dependency for audio transcription, a capability this project does not use, and it is recorded so that a future reader does not mistake it for a defect in the mesh.

**Revisit this decision the moment owned Python lands under `agent-mesh/plugins/`.** The argument above depends on the domain containing no owned production source, and that ceases to be true when the first owned extension is written.

## Consequences

- The compatibility probe becomes usable evidence: deterministic on a cold cache, and reporting on plugin compatibility rather than on upstream lint hygiene.
- The project now holds a measured, dated statement about four upstream warning sources, with named files and counts, instead of an untested assumption that the pinned runtime is clean.
- **The exemption is broader than the application workspace's.** `ignore::pydantic.warnings.PydanticDeprecatedSince20` covers a whole warning class, not one message, so a *different* class-based-config deprecation introduced by a future Agent Mesh release is silenced rather than surfaced. That is accepted only because no owned production code lives here.
- The condition for revisiting is a state of the repository rather than a date, so nothing expires and nothing reminds anyone. A contributor adding `agent-mesh/plugins/` has to notice this record. That is a weakness of the mechanism and is stated rather than hidden.
- Three pinned upstream defects now have no owner and no fix date. None is this project's to fix and none blocks the initial release, but each constrains a future dependency or interpreter upgrade.
- The `ffmpeg` exemption hides a genuine environment difference rather than a code defect. Silencing it means the suite no longer notices whether the host provides the media tooling `markitdown[all]` expects. That is acceptable only while nothing in this project converts audio, and it stops being acceptable if that changes.

## Alternatives considered

- **Remove `filterwarnings = ["error"]` from the Agent Mesh project.** Rejected: it would silence every warning from a 251-package dependency graph, including ones signalling a real incompatibility between the runtime and its plugins, which is the exact question this domain exists to answer.
- **Ignore `DeprecationWarning` as a category.** Rejected: far wider than the measured defect, and it would cover deprecations from any of the other 250 packages.
- **Mark the individual probe cases with `pytest.mark.filterwarnings`.** Rejected on the evidence in [ADR-0028](0028-untyped-solace-client-boundary.md): module- and item-scoped filters apply at call time, while these warnings fire at import time during collection, so the marker only works if imports move inside test bodies, which Ruff `PLC0415` prohibits.
- **Pin an older Pydantic.** Rejected: Agent Mesh 1.28.7 pins `pydantic==2.12.5` exactly, so there is nothing to choose, and overriding a vendor's exact pin to quiet its own deprecation warning would change the graph being tested.
- **Wait for an Agent Mesh release that fixes them.** Rejected: 1.28.7 is the latest release on PyPI as of 2026-08-19, so there is nothing to upgrade to.
- **Install `ffmpeg` as a documented prerequisite so its warning stops firing.** Rejected for this stage: it adds a system dependency to every contributor and continuous-integration machine to quiet a warning from a document-conversion path the search-and-rescue scenario never exercises.
