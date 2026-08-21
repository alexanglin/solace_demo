"""Fail-closed adjudication of known dependency advisories against reviewed waivers.

``AGENTS.md`` requires a safe upgrade, an upstream fix, or a time-bounded
human-approved waiver before a known upstream advisory reaches a release. This gate
makes that requirement executable. It reads a ``pip-audit`` JSON report for a Python
lock, or a Trivy JSON report for a container image or the deploy configuration
(``docs/adr/0048``), and the waiver registry, then enforces both directions: no blocking
advisory may go unwaived, and no waiver may outlive the advisory it was written for.

A pip-audit finding always blocks. A Trivy finding blocks when its severity is HIGH or
CRITICAL and a fixed version exists, or, for a Dockerfile check, when the check failed;
every other Trivy finding is printed as an ``INFO:`` line and blocks nothing. Each loader
refuses the other tool's report shape, so a report read with the wrong ``--source`` is an
error rather than a clean run. This module is pure: it launches nothing.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Final

REGISTRY_NAME = "dependency-waivers.toml"
MAX_WAIVER_REVIEW_DAYS = 30
MIN_WAIVER_REASON_CHARACTERS = 20
DOMAINS = ("agent-mesh", "dashboard", "root")
"""The Python lock domains pip-audit reports on."""

PIP_AUDIT: Final = "pip-audit"
TRIVY: Final = "trivy"
SOURCES: Final = (PIP_AUDIT, TRIVY)
CONFIG_DOMAIN: Final = "deploy-config"
"""The domain for Dockerfile checks reported by ``trivy config``."""
IMAGE_DOMAIN_PATTERN: Final = re.compile(r"^image:[a-z0-9][a-z0-9._/-]*$")
"""An image domain names the repository without tag or digest; neither is representable."""
TRIVY_SCHEMA_VERSION: Final = 2
TRIVY_BLOCKING_SEVERITIES: Final = frozenset({"HIGH", "CRITICAL"})
"""The severities that block when a fix exists (``docs/adr/0048``)."""
MISCONFIGURATION_FAILED: Final = "FAIL"
CONFIG_VERSION: Final = "config"
"""The version a misconfiguration waiver is keyed on; a check has no installed version."""
TEXT_FIELDS = (
    "domain",
    "package",
    "version",
    "advisory",
    "reason",
    "reachability",
    "compensating_control",
    "reviewed_by",
)
DATE_FIELDS = ("reviewed_on", "expires_on")
KNOWN_FIELDS = frozenset(TEXT_FIELDS + DATE_FIELDS)


@dataclass(frozen=True)
class WaiverRecord:
    """One time-bounded human review of an exact advisory against a pinned version."""

    domain: str
    package: str
    version: str
    advisory: str
    reason: str
    reachability: str
    compensating_control: str
    reviewed_by: str
    reviewed_on: date
    expires_on: date

    @property
    def identity(self) -> tuple[str, str, str, str]:
        """Return the fields that make this waiver unique within the registry."""
        return (self.domain, self.package, self.version, self.advisory)

    @property
    def label(self) -> str:
        """Return the human-readable identity used in diagnostics."""
        return f"{self.package} {self.version} {self.advisory}"


@dataclass(frozen=True)
class Finding:
    """One advisory reported against an exact installed version.

    A pip-audit finding always blocks. A Trivy finding carries its severity and blocks only
    under the policy in ``docs/adr/0048``; the rest is informational.
    """

    domain: str
    package: str
    version: str
    advisory: str
    fix_versions: tuple[str, ...]
    severity: str = ""
    blocking: bool = True

    @property
    def identity(self) -> tuple[str, str, str, str]:
        """Return the fields a waiver must match to cover this advisory."""
        return (self.domain, self.package, self.version, self.advisory)

    @property
    def label(self) -> str:
        """Return the human-readable identity used in diagnostics."""
        return f"{self.package} {self.version} {self.advisory}"


def _text(context: str, entry: dict[str, object], key: str, errors: list[str]) -> str | None:
    value = entry.get(key)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{context}.{key} must be a non-empty string")
        return None
    return value.strip()


def _text_fields(
    context: str,
    entry: dict[str, object],
    errors: list[str],
) -> dict[str, str] | None:
    values: dict[str, str] = {}
    for key in TEXT_FIELDS:
        value = _text(context, entry, key, errors)
        if value is not None:
            values[key] = value
    return values if len(values) == len(TEXT_FIELDS) else None


def _date_field(context: str, entry: dict[str, object], key: str, errors: list[str]) -> date | None:
    raw = _text(context, entry, key, errors)
    if raw is None:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        errors.append(f"{context}.{key} must be an ISO-8601 calendar date")
        return None


def is_domain(name: str) -> bool:
    """Return whether ``name`` is a Python lock domain, the deploy configuration, or an image."""
    if name in DOMAINS or name == CONFIG_DOMAIN:
        return True
    return IMAGE_DOMAIN_PATTERN.fullmatch(name) is not None


def _domain_description() -> str:
    return f"{', '.join(DOMAINS)}, {CONFIG_DOMAIN}, or image:<repository>"


def _content_errors(context: str, record: WaiverRecord) -> list[str]:
    """Return diagnostics for values that parse but are not admissible."""
    errors: list[str] = []
    if not is_domain(record.domain):
        errors.append(f"{context}.domain must be one of {_domain_description()}")
    if len(record.reason) < MIN_WAIVER_REASON_CHARACTERS:
        errors.append(
            f"{context}.reason must contain at least {MIN_WAIVER_REASON_CHARACTERS} characters"
        )
    return errors


def _parse_waiver(
    context: str,
    entry: dict[str, object],
    errors: list[str],
) -> WaiverRecord | None:
    unknown = sorted(set(entry) - KNOWN_FIELDS)
    if unknown:
        errors.append(f"{context} has unknown fields: {', '.join(unknown)}")
        return None
    texts = _text_fields(context, entry, errors)
    reviewed_on = _date_field(context, entry, "reviewed_on", errors)
    expires_on = _date_field(context, entry, "expires_on", errors)
    if texts is None or reviewed_on is None or expires_on is None:
        return None
    return WaiverRecord(
        domain=texts["domain"],
        package=texts["package"],
        version=texts["version"],
        advisory=texts["advisory"],
        reason=texts["reason"],
        reachability=texts["reachability"],
        compensating_control=texts["compensating_control"],
        reviewed_by=texts["reviewed_by"],
        reviewed_on=reviewed_on,
        expires_on=expires_on,
    )


def _registry_table(path: Path, errors: list[str]) -> dict[str, object] | None:
    if not path.is_file():
        errors.append(f"missing dependency waiver registry: {path.name}")
        return None
    try:
        data: dict[str, object] = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        errors.append(f"{path.name}: cannot read the waiver registry: {error}")
        return None
    if type(data.get("format")) is not int or data["format"] != 1:
        errors.append(f"{path.name}: format must be integer 1")
        return None
    return data


def load_waivers(path: Path, errors: list[str]) -> tuple[WaiverRecord, ...]:
    """Return every parsable waiver, appending a diagnostic for each rejected entry."""
    data = _registry_table(path, errors)
    if data is None:
        return ()
    raw_waivers = data.get("waivers", [])
    if not isinstance(raw_waivers, list):
        errors.append(f"{path.name}: waivers must be an array of tables")
        return ()
    records: list[WaiverRecord] = []
    seen: set[tuple[str, str, str, str]] = set()
    for index, raw in enumerate(raw_waivers, start=1):
        context = f"{path.name}: waivers[{index}]"
        if not isinstance(raw, dict):
            errors.append(f"{context} must be a table")
            continue
        record = _parse_waiver(context, raw, errors)
        if record is None:
            continue
        errors.extend(_content_errors(context, record))
        if record.identity in seen:
            errors.append(f"{context} duplicates waiver {record.label}")
            continue
        seen.add(record.identity)
        records.append(record)
    return tuple(records)


def _report_table(
    path: Path, errors: list[str], source: str = PIP_AUDIT
) -> dict[str, object] | None:
    if not path.is_file():
        errors.append(f"missing {source} report: {path.name}")
        return None
    try:
        parsed: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        errors.append(f"{path.name}: cannot read the {source} report: {error}")
        return None
    if not isinstance(parsed, dict):
        errors.append(f"{path.name}: the {source} report must be an object")
        return None
    return {str(key): value for key, value in parsed.items()}


def _fix_versions(entry: dict[str, object]) -> tuple[str, ...]:
    raw = entry.get("fix_versions", [])
    if not isinstance(raw, list):
        return ()
    return tuple(item for item in raw if isinstance(item, str))


def _dependency_findings(
    context: str,
    domain: str,
    entry: dict[str, object],
    errors: list[str],
) -> list[Finding]:
    name = _text(context, entry, "name", errors)
    version = _text(context, entry, "version", errors)
    raw_vulnerabilities = entry.get("vulns", [])
    if not isinstance(raw_vulnerabilities, list):
        errors.append(f"{context}.vulns must be an array")
        return []
    if name is None or version is None:
        return []
    findings: list[Finding] = []
    for position, raw in enumerate(raw_vulnerabilities, start=1):
        advisory_context = f"{context}.vulns[{position}]"
        if not isinstance(raw, dict):
            errors.append(f"{advisory_context} must be an object")
            continue
        vulnerability = {str(key): value for key, value in raw.items()}
        advisory = _text(advisory_context, vulnerability, "id", errors)
        if advisory is not None:
            findings.append(Finding(domain, name, version, advisory, _fix_versions(vulnerability)))
    return findings


def load_findings(path: Path, domain: str, errors: list[str]) -> tuple[Finding, ...]:
    """Return each distinct advisory in a pip-audit report, keeping the first of any repeat."""
    data = _report_table(path, errors)
    if data is None:
        return ()
    raw_dependencies = data.get("dependencies")
    if raw_dependencies is None:
        errors.append(f"{path.name}: dependencies is required by a pip-audit report")
        return ()
    if not isinstance(raw_dependencies, list):
        errors.append(f"{path.name}: dependencies must be an array")
        return ()
    findings: dict[tuple[str, str, str, str], Finding] = {}
    for index, raw in enumerate(raw_dependencies, start=1):
        context = f"{path.name}: dependencies[{index}]"
        if not isinstance(raw, dict):
            errors.append(f"{context} must be an object")
            continue
        entry = {str(key): value for key, value in raw.items()}
        for finding in _dependency_findings(context, domain, entry, errors):
            findings.setdefault(finding.identity, finding)
    return tuple(findings.values())


def _fixed_versions(raw: object) -> tuple[str, ...]:
    """Return Trivy's comma-separated fixed versions, or nothing when no fix exists."""
    if not isinstance(raw, str):
        return ()
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def is_domain_image(domain: str) -> bool:
    """Return whether ``domain`` names a container image rather than a resolved manifest."""
    return bool(IMAGE_DOMAIN_PATTERN.match(domain))


def _blocks(severity: str, *, actionable: bool) -> bool:
    return severity.upper() in TRIVY_BLOCKING_SEVERITIES and actionable


def _vulnerability_finding(
    context: str, domain: str, entry: dict[str, object], errors: list[str]
) -> Finding | None:
    advisory = _text(context, entry, "VulnerabilityID", errors)
    package = _text(context, entry, "PkgName", errors)
    version = _text(context, entry, "InstalledVersion", errors)
    severity = _text(context, entry, "Severity", errors)
    if advisory is None or package is None or version is None or severity is None:
        return None
    fixes = _fixed_versions(entry.get("FixedVersion"))
    # ADR-0055: inside a pinned third-party image a published fix is not something this
    # project can take -- its only lever is the digest it pins, which the pin gate checks.
    # An advisory here is reported, never enforced.
    blocking = _blocks(severity, actionable=bool(fixes) and not is_domain_image(domain))
    return Finding(domain, package, version, advisory, fixes, severity.upper(), blocking)


def _misconfiguration_finding(
    context: str, domain: str, target: str, entry: dict[str, object], errors: list[str]
) -> Finding | None:
    advisory = _text(context, entry, "ID", errors)
    severity = _text(context, entry, "Severity", errors)
    status = _text(context, entry, "Status", errors)
    if advisory is None or severity is None or status is None:
        return None
    if status != MISCONFIGURATION_FAILED:
        return None
    blocking = _blocks(severity, actionable=True)
    return Finding(domain, target, CONFIG_VERSION, advisory, (), severity.upper(), blocking)


def _entries(
    context: str, result: dict[str, object], key: str, errors: list[str]
) -> list[tuple[str, dict[str, object]]]:
    raw = result.get(key, [])
    if not isinstance(raw, list):
        errors.append(f"{context}.{key} must be an array")
        return []
    entries: list[tuple[str, dict[str, object]]] = []
    for position, item in enumerate(raw, start=1):
        item_context = f"{context}.{key}[{position}]"
        if not isinstance(item, dict):
            errors.append(f"{item_context} must be an object")
            continue
        entries.append((item_context, {str(key): value for key, value in item.items()}))
    return entries


def _result_findings(
    context: str, domain: str, result: dict[str, object], errors: list[str]
) -> list[Finding]:
    target = _text(context, result, "Target", errors)
    if target is None:
        return []
    findings: list[Finding] = []
    for item_context, entry in _entries(context, result, "Vulnerabilities", errors):
        finding = _vulnerability_finding(item_context, domain, entry, errors)
        if finding is not None:
            findings.append(finding)
    for item_context, entry in _entries(context, result, "Misconfigurations", errors):
        finding = _misconfiguration_finding(item_context, domain, target, entry, errors)
        if finding is not None:
            findings.append(finding)
    return findings


def load_trivy_findings(path: Path, domain: str, errors: list[str]) -> tuple[Finding, ...]:
    """Return each distinct finding in a Trivy report, blocking or informational."""
    data = _report_table(path, errors, TRIVY)
    if data is None:
        return ()
    version = data.get("SchemaVersion")
    if type(version) is not int or version != TRIVY_SCHEMA_VERSION:
        errors.append(f"{path.name}: SchemaVersion must be integer {TRIVY_SCHEMA_VERSION}")
        return ()
    raw_results = data.get("Results", [])
    if not isinstance(raw_results, list):
        errors.append(f"{path.name}: Results must be an array")
        return ()
    findings: dict[tuple[str, str, str, str], Finding] = {}
    for index, raw in enumerate(raw_results, start=1):
        context = f"{path.name}: Results[{index}]"
        if not isinstance(raw, dict):
            errors.append(f"{context} must be an object")
            continue
        result = {str(key): value for key, value in raw.items()}
        for finding in _result_findings(context, domain, result, errors):
            findings.setdefault(finding.identity, finding)
    return tuple(findings.values())


LOADERS: Final[Mapping[str, Callable[[Path, str, list[str]], tuple[Finding, ...]]]] = {
    PIP_AUDIT: load_findings,
    TRIVY: load_trivy_findings,
}


def _informational_lines(findings: tuple[Finding, ...], domain: str) -> list[str]:
    return [
        f"INFO: {finding.severity} {'fixed' if finding.fix_versions else 'unfixed'}: "
        f"{finding.label} in {domain}"
        for finding in findings
        if not finding.blocking
    ]


def _domain_argument(value: str) -> str:
    if not is_domain(value):
        message = f"domain must be one of {_domain_description()}"
        raise argparse.ArgumentTypeError(message)
    return value


def _window_errors(waiver: WaiverRecord, *, today: date) -> list[str]:
    """Return diagnostics for a review window that is not open on ``today``."""
    errors: list[str] = []
    label = f"waiver {waiver.label}"
    if waiver.reviewed_on > today:
        errors.append(f"{label} is reviewed in the future")
    if waiver.expires_on <= today:
        errors.append(f"{label} expired on {waiver.expires_on}")
    lifetime = (waiver.expires_on - waiver.reviewed_on).days
    if lifetime <= 0 or lifetime > MAX_WAIVER_REVIEW_DAYS:
        errors.append(f"{label} must expire within {MAX_WAIVER_REVIEW_DAYS} days of review")
    return errors


def evaluate(
    waivers: tuple[WaiverRecord, ...],
    findings: tuple[Finding, ...],
    domain: str,
    *,
    today: date,
) -> list[str]:
    """Return diagnostics for one audited domain, in both directions.

    No advisory may go unwaived, and no waiver may outlive the advisory it was written
    for. A waiver whose review window is not open on ``today`` covers nothing, so its
    advisory is reported as unwaived in addition to the window diagnostic.
    """
    scoped = tuple(waiver for waiver in waivers if waiver.domain == domain)
    reported = {finding.identity for finding in findings}
    covering: set[tuple[str, str, str, str]] = set()
    errors: list[str] = []
    for waiver in scoped:
        window = _window_errors(waiver, today=today)
        errors.extend(window)
        if not window:
            covering.add(waiver.identity)
        if waiver.identity not in reported:
            errors.append(f"stale waiver: {waiver.label} matches no advisory in {domain}")
    errors.extend(
        f"unwaived advisory: {finding.label} in {domain}"
        for finding in findings
        if finding.identity not in covering
    )
    return sorted(set(errors))


def _parse_arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="dependency-waiver-gate",
        description="Adjudicate a pip-audit or Trivy report against the reviewed waiver registry.",
    )
    parser.add_argument("--source", choices=SOURCES, default=PIP_AUDIT)
    parser.add_argument("--domain", required=True, type=_domain_argument)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--registry", default=Path(REGISTRY_NAME), type=Path)
    parser.add_argument("--today", default=None, help="ISO-8601 date used for review windows.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Print diagnostics and return a blocking status when adjudication fails."""
    arguments = _parse_arguments(argv)
    today = date.fromisoformat(arguments.today) if arguments.today else date.today()
    errors: list[str] = []
    waivers = load_waivers(arguments.registry, errors)
    findings = LOADERS[arguments.source](arguments.report, arguments.domain, errors)
    blocking = tuple(finding for finding in findings if finding.blocking)
    errors.extend(evaluate(waivers, blocking, arguments.domain, today=today))
    for note in sorted(_informational_lines(findings, arguments.domain)):
        print(note)
    for error in sorted(set(errors)):
        print(f"DEPENDENCY: {error}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
