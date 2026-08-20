# ADR-0028: Contain the pinned Solace client's static-analysis defects at its boundary

- **Status:** Accepted
- **Date:** 2026-08-19
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

Phase 0 pinned `solace-pubsubplus` 1.11.0 into the Python 3.14.7 application workspace and probed whether it functions on that interpreter. It does: the bundled native library loads through `ctypes`, session creation marshals its callback structures across the boundary, and the API version, application identifier, and a message payload all read back. [ADR-0004](0004-split-python-runtimes.md)'s split-runtime decision survives its kill criterion.

Making that probe run revealed two defects in the distribution, neither of which is a defect in this project, and both of which this project's gates correctly refuse to ignore.

**The distribution carries no type information.** Measured on 2026-08-19: `solace-pubsubplus` 1.11.0 ships 175 files and no `py.typed` marker, and neither `types-solace-pubsubplus` nor `solace-pubsubplus-stubs` exists on PyPI. Under `strict = true`, every import reports `import-untyped`. [ADR-0011](0011-no-exception-lint-typecheck-and-complexity-budgets.md) prohibits a blanket inline suppression and there is no `# type: ignore` anywhere in the owned tree, so the relaxation cannot be written at the import site.

**The distribution's docstrings carry invalid escape sequences.** `solace/messaging/config/solace_properties/authentication_properties.py:115` contains the literal `\@`. Python 3.14 reports that as a `SyntaxWarning` at compile time. `[tool.pytest.ini_options] filterwarnings = ["error"]` escalates every warning, and CPython converts an escalated compile-time `SyntaxWarning` into a `SyntaxError`, so the import raises.

The second defect is the more dangerous of the two, because **it is cache-dependent**. The warning is emitted only when CPython compiles the source; once `__pycache__` holds the `.pyc`, it never fires again. Measured on the same commit: with a warm cache the probe suite reports 7 passed, and with `__pycache__` removed the same suite reports 6 failed. A developer who has run the suite once sees green forever; continuous integration, which checks out and installs fresh every time, sees red every time. A gate whose verdict depends on the state of a build cache is not a gate.

## Decision

Contain both defects at the boundary of the distribution that causes them, as narrowly as each mechanism allows, and treat neither as fixed.

**Typing.** Declare one `[[tool.mypy.overrides]]` section in the root `pyproject.toml` scoped to `module = ["solace.*"]` with `ignore_missing_imports = true`. The scope is the distribution, not a file, a directory, or a rule set. Everything the client returns is therefore `Any`, which is the reason it must stay behind a typed adapter in `packages/broker` rather than being called from domain or service code; [ADR-0011](0011-no-exception-lint-typecheck-and-complexity-budgets.md)'s layering ban already forbids `packages/domain` from importing `solace` at all.

**Warnings.** Keep `error` as the first entry of `filterwarnings` and append exactly one exemption, `ignore:.*invalid escape sequence.*:SyntaxWarning`, scoped by message text rather than by category or module. Every other warning is still an error.

Scoping this one globally rather than to the importing test is deliberate, and it rests on a compensating control that already exists. The warning is the runtime half of a defect the linter also detects: Ruff `W605` reports an invalid escape sequence in owned source, `W` is in the selected rule set, and Ruff honours `.gitignore` so it never scans the virtual environment. Owned code that commits this defect therefore still fails, at lint time, which is the correct stage for a static defect. Nothing is lost by declining to also detect it at test time in a third-party distribution.

A narrower scope was tried and does not work. A `pytest.mark.filterwarnings` on the probe module applies when the test runs, but the client is imported when the module is collected, so the marker only takes effect if the import is moved inside each test function — which Ruff `PLC0415` prohibits. Choosing the marker would have meant waiving a lint rule to avoid waiving a warning.

Both relaxations are removed the day upstream publishes a `py.typed` marker and a release without the invalid escape sequence. Report both defects upstream.

## Consequences

- The probe is deterministic. It reports the same verdict on a cold cache and a warm one, which is what makes it usable as continuous-integration evidence rather than a local convenience.
- The repository still contains no inline suppression. Both relaxations are declarative, greppable, and scoped by name to the thing that needs them.
- **Static analysis of every call into the Solace client is lost.** A misspelled method, a wrong argument count, and a wrong argument type all pass type checking. This is a real reduction in safety on the exact boundary that carries every command and every telemetry event, and the only compensating control is the typed adapter and its tests.
- Confining the client behind an adapter stops being a stylistic preference and becomes the mechanism that limits the blast radius of the lost typing. `packages/broker` must expose a fully typed façade, and no other package may import `solace` directly.
- The warning relaxation is scoped by message text, not by module, so it applies to owned code too. Ruff `W605` is what keeps that from being a loss, which makes this decision depend on `W` staying in the selected rule set — removing it would silently widen this waiver. A *different* warning class from the same distribution is still an error and would fail loudly.
- Two waivers now exist whose removal depends on an upstream release nobody here controls. If upstream never fixes them, they never expire, unlike the dependency waivers in [ADR-0026](0026-expiring-dependency-waivers.md), which do.
- A future contributor who adds a second untyped distribution must add a second override rather than widening this one, and review has to enforce that.

## Alternatives considered

- **Hand-written stub files for the client.** Rejected for now: the API surface Phase 0 touches is four calls, and stubs for the surface `packages/broker` will eventually need would be a substantial artifact tracking an upstream that publishes no interface contract. Worth revisiting once the adapter's real surface is known, because it is the only option that restores type checking.
- **A blanket `ignore_missing_imports = true` with no module scope.** Rejected: it would silence the next untyped dependency silently, which is the failure mode [ADR-0011](0011-no-exception-lint-typecheck-and-complexity-budgets.md) exists to prevent.
- **`# type: ignore[import-untyped]` at each import site.** Rejected: prohibited by [ADR-0011](0011-no-exception-lint-typecheck-and-complexity-budgets.md), and `PGH` flags it.
- **Removing `filterwarnings = ["error"]` altogether, or ignoring `SyntaxWarning` as a category.** Rejected: the escalate-everything policy is what turned a cache-dependent import failure into a visible finding in the first place, and a category-wide ignore would cover unrelated syntax defects that have no compensating linter rule.
- **A `pytest.mark.filterwarnings` scoped to the probe module.** Rejected on evidence: module-scoped filters apply at test-call time, while the client is imported at collection time, so the marker only works if every import moves inside a test function, which Ruff `PLC0415` prohibits. It traded a warning waiver for a lint waiver and was strictly worse.
- **Pre-compiling bytecode at install time with `uv sync --compile-bytecode`.** Measured and it does work: compiling all 3077 files at install moves the warning to install output, where it is not escalated, and the probe then passes on a fresh environment. Rejected as the primary mechanism because correctness would depend on every developer, every hook, and every continuous-integration step remembering a flag, and forgetting it fails in the cache-dependent way this decision exists to eliminate. It remains available as a performance option.
- **Vendoring a patched copy of the offending module.** Rejected: it forks a pinned upstream distribution over a docstring, and [ADR-0001](0001-self-hosted-open-source-agent-mesh.md) installs released packages rather than vendoring source.
