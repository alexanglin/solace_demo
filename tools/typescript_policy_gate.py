"""Hold the dashboard's TypeScript configuration to the baseline ADR-0057 fixes.

``tsc`` holds code to whatever configuration exists. Nothing held the configuration, so
the strictness rule was prose that no build could fail on -- the defect ADR-0011 names in
its own context. This gate reads the configuration as text and refuses one that does not
carry the baseline. It never runs Node, never resolves a package, and never starts a
subprocess: ADR-0025 confines subprocess to four reviewed owners, so the enumeration of
files lives in the shell driver and arrives here as arguments.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final, cast

# ADR-0057. `strict` expands to fifteen flags and stops; every entry below is one it omits,
# except `strict` itself, which is listed so its deletion is a finding rather than a gap.
REQUIRED_COMPILER_OPTIONS: Final[Mapping[str, bool]] = {
    "allowUnreachableCode": False,
    "allowUnusedLabels": False,
    "erasableSyntaxOnly": True,
    "exactOptionalPropertyTypes": True,
    "forceConsistentCasingInFileNames": True,
    "isolatedModules": True,
    "noFallthroughCasesInSwitch": True,
    "noImplicitOverride": True,
    "noImplicitReturns": True,
    "noPropertyAccessFromIndexSignature": True,
    "noUncheckedIndexedAccess": True,
    "noUncheckedSideEffectImports": True,
    "noUnusedLocals": True,
    "noUnusedParameters": True,
    "skipLibCheck": False,
    "strict": True,
    "verbatimModuleSyntax": True,
}

REQUIRED_SCRIPTS: Final = ("build", "format:check", "lint", "test", "test:coverage", "typecheck")

# docs/operating-parameters.md is the home for the number; it is repeated here because a
# gate cannot cite a document at runtime, and the contract test holds the two equal.
COVERAGE_THRESHOLD_PERCENT: Final = 95
COVERAGE_DIMENSIONS: Final = ("statements", "branches", "functions", "lines")

# erasableSyntaxOnly landed in TypeScript 5.8.
MINIMUM_TYPESCRIPT: Final = (5, 8)

EXACT_VERSION: Final = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")
MAX_WARNINGS: Final = re.compile(r"--max-warnings[= ](\d+)")

NO_CONFIGURATION: Final = "apps/dashboard holds TypeScript source but no tsconfig.json"


def _load_json(path: Path, errors: list[str]) -> Mapping[str, object] | None:
    """Return one parsed JSON document, recording a finding when it cannot be read."""
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as cause:
        errors.append(f"{path}: is not readable as JSON ({cause})")
        return None
    if not isinstance(parsed, dict):
        errors.append(f"{path}: does not hold a JSON object")
        return None
    return cast("Mapping[str, object]", parsed)


def _mapping(document: Mapping[str, object], key: str) -> Mapping[str, object]:
    """Return one nested object member, or an empty mapping when it is absent."""
    value = document.get(key)
    if not isinstance(value, dict):
        return {}
    return cast("Mapping[str, object]", value)


def _extends_target(path: Path, document: Mapping[str, object]) -> tuple[Path | None, list[str]]:
    """Resolve one ``extends`` member, refusing anything that is not a relative path."""
    declared = document.get("extends")
    if declared is None:
        return None, []
    if not isinstance(declared, str) or not declared.startswith("."):
        return None, [
            f"{path}: extends {declared!r}; only a relative path inside apps/dashboard may "
            f"carry the baseline, because a published preset can be relaxed by a version bump"
        ]
    target = (path.parent / declared).resolve()
    if target.is_dir():
        target = target / "tsconfig.json"
    return target, []


def effective_compiler_options(path: Path, errors: list[str]) -> Mapping[str, object]:
    """Return one configuration's compiler options merged over its ``extends`` chain."""
    merged: dict[str, object] = {}
    seen: set[Path] = set()
    current: Path | None = path
    while current is not None and current not in seen:
        seen.add(current)
        document = _load_json(current, errors)
        if document is None:
            break
        merged = {**_mapping(document, "compilerOptions"), **merged}
        current, findings = _extends_target(current, document)
        errors.extend(findings)
    return merged


def compiler_option_issues(path: Path, options: Mapping[str, object]) -> list[str]:
    """Return one finding for every baseline compiler option missing or relaxed."""
    return [
        f"{path}: compilerOptions.{option} must be {str(required).lower()}, found "
        f"{json.dumps(options.get(option))} (docs/adr/0057)"
        for option, required in REQUIRED_COMPILER_OPTIONS.items()
        if options.get(option) is not required
    ]


def evaluate_tsconfig(path: Path, errors: list[str]) -> list[str]:
    """Return every baseline finding for one TypeScript configuration."""
    options = effective_compiler_options(path, errors)
    return compiler_option_issues(path, options)


def _script_issues(path: Path, scripts: Mapping[str, object]) -> list[str]:
    """Return one finding for every required package script that is absent."""
    return [
        f"{path}: scripts.{name!r} is required so the dashboard gates have an entry point"
        for name in REQUIRED_SCRIPTS
        if not isinstance(scripts.get(name), str)
    ]


def _warning_issues(path: Path, scripts: Mapping[str, object]) -> list[str]:
    """Return a finding when the lint script tolerates a warning."""
    lint = scripts.get("lint")
    if not isinstance(lint, str):
        return []
    found = MAX_WARNINGS.search(lint)
    if found is not None and found.group(1) == "0":
        return []
    return [
        f"{path}: scripts.lint must pass --max-warnings 0; a preset rule set to warn "
        f"otherwise passes the gate (docs/adr/0057)"
    ]


def _coverage_issues(path: Path, scripts: Mapping[str, object]) -> list[str]:
    """Return one finding for every coverage dimension below the declared threshold."""
    command = scripts.get("test:coverage")
    if not isinstance(command, str):
        return []
    issues = []
    for dimension in COVERAGE_DIMENSIONS:
        found = re.search(rf"--coverage\.thresholds\.{dimension}[= ](\d+)", command)
        if found is None or int(found.group(1)) < COVERAGE_THRESHOLD_PERCENT:
            issues.append(
                f"{path}: scripts['test:coverage'] must hold {dimension} at "
                f"{COVERAGE_THRESHOLD_PERCENT} percent or above"
            )
    return issues


def _version_issues(path: Path, document: Mapping[str, object]) -> list[str]:
    """Return one finding for every dependency named by a range rather than a version."""
    issues = []
    for group in ("dependencies", "devDependencies"):
        for name, declared in _mapping(document, group).items():
            if not isinstance(declared, str) or not EXACT_VERSION.match(declared):
                issues.append(
                    f"{path}: {group}.{name} is {declared!r}; every dependency is pinned to "
                    f"an exact version, as every image digest and hook revision is"
                )
    return issues


def _typescript_issues(path: Path, document: Mapping[str, object]) -> list[str]:
    """Return a finding when TypeScript is absent or older than the baseline needs."""
    declared = _mapping(document, "devDependencies").get("typescript")
    floor = ".".join(str(part) for part in MINIMUM_TYPESCRIPT)
    if not isinstance(declared, str) or not EXACT_VERSION.match(declared):
        return [f"{path}: devDependencies.typescript must be pinned at {floor} or above"]
    parts = tuple(int(part) for part in declared.split("-")[0].split(".")[:2])
    if parts < MINIMUM_TYPESCRIPT:
        return [f"{path}: devDependencies.typescript {declared} predates erasableSyntaxOnly"]
    return []


def evaluate_manifest(path: Path, errors: list[str]) -> list[str]:
    """Return every baseline finding for the dashboard package manifest."""
    document = _load_json(path, errors)
    if document is None:
        return []
    scripts = _mapping(document, "scripts")
    return [
        *_script_issues(path, scripts),
        *_warning_issues(path, scripts),
        *_coverage_issues(path, scripts),
        *_version_issues(path, document),
        *_typescript_issues(path, document),
    ]


def evaluate(
    manifest: Path | None,
    tsconfigs: Sequence[Path],
    sources: Sequence[Path],
    errors: list[str],
) -> list[str]:
    """Return every finding for one dashboard configuration."""
    issues = [issue for path in tsconfigs for issue in evaluate_tsconfig(path, errors)]
    if manifest is not None:
        issues.extend(evaluate_manifest(manifest, errors))
    if sources and not tsconfigs:
        issues.append(NO_CONFIGURATION)
    return issues


def _parse_arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="typescript-policy-gate",
        description="Hold apps/dashboard to the TypeScript baseline in docs/adr/0057.",
    )
    parser.add_argument("--package-json", type=Path)
    parser.add_argument("--tsconfig", action="append", default=[], type=Path)
    parser.add_argument("--source", action="append", default=[], type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Print diagnostics and return a blocking status when the configuration is relaxed."""
    arguments = _parse_arguments(argv)
    manifest = cast("Path | None", arguments.package_json)
    tsconfigs = cast("list[Path]", arguments.tsconfig)
    sources = cast("list[Path]", arguments.source)
    if manifest is None and not tsconfigs and not sources:
        return 0
    errors: list[str] = []
    issues = sorted(set(evaluate(manifest, tsconfigs, sources, errors) + errors))
    for issue in issues:
        print(f"TYPESCRIPT: {issue}", file=sys.stderr)
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
