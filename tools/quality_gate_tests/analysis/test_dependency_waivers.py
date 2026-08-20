"""Tests for the fail-closed dependency-waiver registry and advisory adjudication."""

from __future__ import annotations

import contextlib
import io
import json
import unittest
from datetime import date
from pathlib import Path

from tools import dependency_waiver_gate
from tools.quality_gate_tests.support import QualityGateTestCase

TODAY = date(2026, 8, 19)

WAIVER_FIELDS: dict[str, str] = {
    "domain": "agent-mesh",
    "package": "starlette",
    "version": "0.49.1",
    "advisory": "PYSEC-2026-161",
    "reason": "Host header reconstruction is unreachable from the pinned configuration.",
    "reachability": "The Agent Mesh Web UI binds to loopback and has no public ingress.",
    "compensating_control": "Loopback-only binding plus the deterministic command gateway.",
    "reviewed_by": "Alex Anglin",
    "reviewed_on": "2026-08-19",
    "expires_on": "2026-09-18",
}


def waiver(**overrides: str) -> dict[str, str]:
    """Return the canonical waiver entry with the given fields replaced or added."""
    return {**WAIVER_FIELDS, **overrides}


def registry_text(*entries: dict[str, str], registry_format: int = 1) -> str:
    """Render a waiver registry document for the given entries."""
    lines = [f"format = {registry_format}"]
    for entry in entries:
        lines.extend(("", "[[waivers]]"))
        lines.extend(f"{key} = {json.dumps(value)}" for key, value in entry.items())
    return "\n".join(lines) + "\n"


class DependencyWaiverRegistryTests(QualityGateTestCase):
    def registry(self, text: str) -> Path:
        path = self.temporary_directory() / "dependency-waivers.toml"
        path.write_text(text, encoding="utf-8")
        return path

    def test_a_missing_registry_is_an_error(self) -> None:
        # Arrange
        path = self.temporary_directory() / "dependency-waivers.toml"
        errors: list[str] = []

        # Act
        records = dependency_waiver_gate.load_waivers(path, errors)

        # Assert
        self.assertEqual((), records)
        self.assertTrue(any("missing dependency waiver registry" in item for item in errors))

    def test_an_unknown_field_is_rejected(self) -> None:
        # Arrange
        path = self.registry(registry_text(waiver(severity="high")))
        errors: list[str] = []

        # Act
        dependency_waiver_gate.load_waivers(path, errors)

        # Assert
        self.assertTrue(any("unknown fields: severity" in item for item in errors))

    def test_a_duplicate_waiver_identity_is_rejected(self) -> None:
        # Arrange
        path = self.registry(registry_text(waiver(), waiver()))
        errors: list[str] = []

        # Act
        dependency_waiver_gate.load_waivers(path, errors)

        # Assert
        self.assertTrue(any("duplicates waiver" in item for item in errors))

    def test_an_unsupported_format_version_is_rejected(self) -> None:
        # Arrange
        path = self.registry(registry_text(waiver(), registry_format=2))
        errors: list[str] = []

        # Act
        dependency_waiver_gate.load_waivers(path, errors)

        # Assert
        self.assertTrue(any("format must be integer 1" in item for item in errors))

    def test_a_non_iso_date_is_rejected(self) -> None:
        # Arrange
        path = self.registry(registry_text(waiver(reviewed_on="19-08-2026")))
        errors: list[str] = []

        # Act
        dependency_waiver_gate.load_waivers(path, errors)

        # Assert
        self.assertTrue(any("must be an ISO-8601 calendar date" in item for item in errors))

    def test_a_short_reason_is_rejected(self) -> None:
        # Arrange
        path = self.registry(registry_text(waiver(reason="too short")))
        errors: list[str] = []

        # Act
        dependency_waiver_gate.load_waivers(path, errors)

        # Assert
        self.assertTrue(any("reason must contain at least" in item for item in errors))

    def test_an_unknown_domain_is_rejected(self) -> None:
        # Arrange
        path = self.registry(registry_text(waiver(domain="edge")))
        errors: list[str] = []

        # Act
        dependency_waiver_gate.load_waivers(path, errors)

        # Assert
        self.assertTrue(any("domain must be one of" in item for item in errors))

    def test_a_valid_registry_preserves_every_reviewed_field(self) -> None:
        # Arrange
        path = self.registry(registry_text(waiver()))
        errors: list[str] = []

        # Act
        records = dependency_waiver_gate.load_waivers(path, errors)

        # Assert
        self.assertEqual([], errors)
        self.assertEqual(1, len(records))
        self.assertEqual("starlette", records[0].package)
        self.assertEqual("PYSEC-2026-161", records[0].advisory)
        self.assertEqual(date(2026, 9, 18), records[0].expires_on)


def advisory_entry(advisory: str, fix: str = "1.0.1") -> dict[str, object]:
    """Return one pip-audit vulnerability entry."""
    return {"id": advisory, "fix_versions": [fix], "aliases": [], "description": "detail"}


def report_text(*dependencies: dict[str, object]) -> str:
    """Render a pip-audit JSON report for the given dependency entries."""
    return json.dumps({"dependencies": list(dependencies)})


def dependency_entry(name: str, version: str, *advisories: str) -> dict[str, object]:
    """Return one pip-audit dependency entry carrying the given advisories."""
    return {
        "name": name,
        "version": version,
        "vulns": [advisory_entry(advisory) for advisory in advisories],
    }


class DependencyFindingTests(QualityGateTestCase):
    def report(self, text: str) -> Path:
        path = self.temporary_directory() / "audit.json"
        path.write_text(text, encoding="utf-8")
        return path

    def test_a_missing_report_is_an_error(self) -> None:
        # Arrange
        path = self.temporary_directory() / "audit.json"
        errors: list[str] = []

        # Act
        findings = dependency_waiver_gate.load_findings(path, "root", errors)

        # Assert
        self.assertEqual((), findings)
        self.assertTrue(any("missing pip-audit report" in item for item in errors))

    def test_an_unparsable_report_is_an_error(self) -> None:
        # Arrange
        path = self.report("{not json")
        errors: list[str] = []

        # Act
        findings = dependency_waiver_gate.load_findings(path, "root", errors)

        # Assert
        self.assertEqual((), findings)
        self.assertTrue(any("cannot read the pip-audit report" in item for item in errors))

    def test_a_repeated_advisory_is_reported_once(self) -> None:
        # Arrange
        entry = dependency_entry("starlette", "0.49.1", "PYSEC-2026-161", "PYSEC-2026-161")
        path = self.report(report_text(entry))
        errors: list[str] = []

        # Act
        findings = dependency_waiver_gate.load_findings(path, "agent-mesh", errors)

        # Assert
        self.assertEqual([], errors)
        self.assertEqual(1, len(findings))
        self.assertEqual("PYSEC-2026-161", findings[0].advisory)

    def test_a_dependency_without_advisories_produces_no_finding(self) -> None:
        # Arrange
        path = self.report(report_text(dependency_entry("anyio", "4.14.2")))
        errors: list[str] = []

        # Act
        findings = dependency_waiver_gate.load_findings(path, "root", errors)

        # Assert
        self.assertEqual([], errors)
        self.assertEqual((), findings)

    def test_every_finding_carries_the_audited_domain_and_pinned_version(self) -> None:
        # Arrange
        entry = dependency_entry("starlette", "0.49.1", "PYSEC-2026-248")
        path = self.report(report_text(entry))
        errors: list[str] = []

        # Act
        findings = dependency_waiver_gate.load_findings(path, "agent-mesh", errors)

        # Assert
        self.assertEqual("agent-mesh", findings[0].domain)
        self.assertEqual("starlette", findings[0].package)
        self.assertEqual("0.49.1", findings[0].version)


def waiver_record(**overrides: str) -> dependency_waiver_gate.WaiverRecord:
    """Return a parsed waiver record with the given fields replaced."""
    fields = waiver(**overrides)
    return dependency_waiver_gate.WaiverRecord(
        domain=fields["domain"],
        package=fields["package"],
        version=fields["version"],
        advisory=fields["advisory"],
        reason=fields["reason"],
        reachability=fields["reachability"],
        compensating_control=fields["compensating_control"],
        reviewed_by=fields["reviewed_by"],
        reviewed_on=date.fromisoformat(fields["reviewed_on"]),
        expires_on=date.fromisoformat(fields["expires_on"]),
    )


def finding_record(**overrides: str) -> dependency_waiver_gate.Finding:
    """Return a finding matching the canonical waiver unless overridden."""
    fields = waiver(**overrides)
    return dependency_waiver_gate.Finding(
        domain=fields["domain"],
        package=fields["package"],
        version=fields["version"],
        advisory=fields["advisory"],
        fix_versions=("1.0.1",),
    )


class DependencyAdjudicationTests(QualityGateTestCase):
    def evaluate(
        self,
        waivers: tuple[dependency_waiver_gate.WaiverRecord, ...],
        findings: tuple[dependency_waiver_gate.Finding, ...],
        domain: str = "agent-mesh",
    ) -> list[str]:
        return dependency_waiver_gate.evaluate(waivers, findings, domain, today=TODAY)

    def test_an_advisory_without_a_waiver_is_unwaived(self) -> None:
        # Arrange
        findings = (finding_record(),)

        # Act
        errors = self.evaluate((), findings)

        # Assert
        self.assertTrue(any("unwaived advisory" in item for item in errors))

    def test_a_current_waiver_covers_its_advisory(self) -> None:
        # Arrange
        waivers = (waiver_record(),)
        findings = (finding_record(),)

        # Act
        errors = self.evaluate(waivers, findings)

        # Assert
        self.assertEqual([], errors)

    def test_a_waiver_matching_no_advisory_is_stale(self) -> None:
        # Arrange
        waivers = (waiver_record(),)

        # Act
        errors = self.evaluate(waivers, ())

        # Assert
        self.assertTrue(any("stale waiver" in item for item in errors))

    def test_an_expired_waiver_stops_covering_its_advisory(self) -> None:
        # Arrange
        waivers = (waiver_record(reviewed_on="2026-07-01", expires_on="2026-07-31"),)
        findings = (finding_record(),)

        # Act
        errors = self.evaluate(waivers, findings)

        # Assert
        self.assertTrue(any("expired on 2026-07-31" in item for item in errors))
        self.assertTrue(any("unwaived advisory" in item for item in errors))

    def test_a_waiver_reviewed_in_the_future_is_rejected(self) -> None:
        # Arrange
        waivers = (waiver_record(reviewed_on="2026-08-20", expires_on="2026-09-01"),)
        findings = (finding_record(),)

        # Act
        errors = self.evaluate(waivers, findings)

        # Assert
        self.assertTrue(any("reviewed in the future" in item for item in errors))

    def test_a_waiver_outliving_the_maximum_window_is_rejected(self) -> None:
        # Arrange
        waivers = (waiver_record(expires_on="2026-09-19"),)
        findings = (finding_record(),)

        # Act
        errors = self.evaluate(waivers, findings)

        # Assert
        self.assertTrue(any("must expire within 30 days of review" in item for item in errors))

    def test_a_waiver_for_another_version_does_not_cover_the_advisory(self) -> None:
        # Arrange
        waivers = (waiver_record(version="0.49.0"),)
        findings = (finding_record(version="0.49.1"),)

        # Act
        errors = self.evaluate(waivers, findings)

        # Assert
        self.assertTrue(any("unwaived advisory" in item for item in errors))
        self.assertTrue(any("stale waiver" in item for item in errors))

    def test_a_waiver_for_another_domain_is_not_evaluated(self) -> None:
        # Arrange
        waivers = (waiver_record(domain="root"),)
        findings = (finding_record(domain="agent-mesh"),)

        # Act
        errors = self.evaluate(waivers, findings, "agent-mesh")

        # Assert
        self.assertEqual(1, len(errors))
        self.assertIn("unwaived advisory", errors[0])


class DependencyWaiverCommandTests(QualityGateTestCase):
    def invoke(self, registry_body: str, report_body: str) -> tuple[int, str]:
        directory = self.temporary_directory()
        registry = directory / "dependency-waivers.toml"
        registry.write_text(registry_body, encoding="utf-8")
        report = directory / "audit.json"
        report.write_text(report_body, encoding="utf-8")
        stream = io.StringIO()
        with contextlib.redirect_stderr(stream):
            status = dependency_waiver_gate.main(
                [
                    "--domain",
                    "agent-mesh",
                    "--report",
                    str(report),
                    "--registry",
                    str(registry),
                    "--today",
                    "2026-08-19",
                ]
            )
        return status, stream.getvalue()

    def test_an_audit_with_no_advisories_exits_zero(self) -> None:
        # Arrange
        report = report_text(dependency_entry("anyio", "4.14.2"))

        # Act
        status, output = self.invoke("format = 1\n", report)

        # Assert
        self.assertEqual(0, status)
        self.assertEqual("", output)

    def test_an_unwaived_advisory_exits_non_zero_with_a_prefixed_diagnostic(self) -> None:
        # Arrange
        report = report_text(dependency_entry("starlette", "0.49.1", "PYSEC-2026-161"))

        # Act
        status, output = self.invoke("format = 1\n", report)

        # Assert
        self.assertEqual(1, status)
        self.assertIn("DEPENDENCY: unwaived advisory", output)

    def test_a_reviewed_advisory_exits_zero(self) -> None:
        # Arrange
        report = report_text(dependency_entry("starlette", "0.49.1", "PYSEC-2026-161"))

        # Act
        status, output = self.invoke(registry_text(waiver()), report)

        # Assert
        self.assertEqual(0, status)
        self.assertEqual("", output)


class MalformedDocumentTests(QualityGateTestCase):
    def waivers_from(self, body: str) -> list[str]:
        path = self.temporary_directory() / "dependency-waivers.toml"
        path.write_text(body, encoding="utf-8")
        errors: list[str] = []
        dependency_waiver_gate.load_waivers(path, errors)
        return errors

    def findings_from(
        self,
        body: str,
    ) -> tuple[tuple[dependency_waiver_gate.Finding, ...], list[str]]:
        path = self.temporary_directory() / "audit.json"
        path.write_text(body, encoding="utf-8")
        errors: list[str] = []
        findings = dependency_waiver_gate.load_findings(path, "root", errors)
        return findings, errors

    def test_a_non_string_waiver_field_is_rejected(self) -> None:
        # Arrange
        body = "format = 1\n\n[[waivers]]\npackage = 5\n"

        # Act
        errors = self.waivers_from(body)

        # Assert
        self.assertTrue(any("must be a non-empty string" in item for item in errors))

    def test_a_waiver_array_of_the_wrong_type_is_rejected(self) -> None:
        # Arrange
        body = 'format = 1\nwaivers = "everything"\n'

        # Act
        errors = self.waivers_from(body)

        # Assert
        self.assertTrue(any("waivers must be an array of tables" in item for item in errors))

    def test_a_waiver_entry_that_is_not_a_table_is_rejected(self) -> None:
        # Arrange
        body = "format = 1\nwaivers = [1]\n"

        # Act
        errors = self.waivers_from(body)

        # Assert
        self.assertTrue(any("must be a table" in item for item in errors))

    def test_an_unparsable_registry_is_rejected(self) -> None:
        # Arrange
        body = "format = = 1\n"

        # Act
        errors = self.waivers_from(body)

        # Assert
        self.assertTrue(any("cannot read the waiver registry" in item for item in errors))

    def test_a_report_that_is_not_an_object_is_rejected(self) -> None:
        # Arrange
        body = "[]"

        # Act
        _, errors = self.findings_from(body)

        # Assert
        self.assertTrue(any("must be an object" in item for item in errors))

    def test_a_dependencies_value_of_the_wrong_type_is_rejected(self) -> None:
        # Arrange
        body = json.dumps({"dependencies": "everything"})

        # Act
        _, errors = self.findings_from(body)

        # Assert
        self.assertTrue(any("dependencies must be an array" in item for item in errors))

    def test_a_dependency_entry_that_is_not_an_object_is_rejected(self) -> None:
        # Arrange
        body = json.dumps({"dependencies": [1]})

        # Act
        _, errors = self.findings_from(body)

        # Assert
        self.assertTrue(any("must be an object" in item for item in errors))

    def test_a_dependency_without_a_name_yields_no_finding(self) -> None:
        # Arrange
        body = json.dumps({"dependencies": [{"version": "1.0.0", "vulns": []}]})

        # Act
        findings, errors = self.findings_from(body)

        # Assert
        self.assertEqual((), findings)
        self.assertTrue(any("name must be a non-empty string" in item for item in errors))

    def test_a_vulnerability_list_of_the_wrong_type_is_rejected(self) -> None:
        # Arrange
        body = json.dumps({"dependencies": [{"name": "a", "version": "1", "vulns": "x"}]})

        # Act
        _, errors = self.findings_from(body)

        # Assert
        self.assertTrue(any("vulns must be an array" in item for item in errors))

    def test_a_vulnerability_entry_that_is_not_an_object_is_rejected(self) -> None:
        # Arrange
        body = json.dumps({"dependencies": [{"name": "a", "version": "1", "vulns": [1]}]})

        # Act
        _, errors = self.findings_from(body)

        # Assert
        self.assertTrue(any("vulns[1] must be an object" in item for item in errors))

    def test_a_vulnerability_without_an_identifier_yields_no_finding(self) -> None:
        # Arrange
        body = json.dumps({"dependencies": [{"name": "a", "version": "1", "vulns": [{}]}]})

        # Act
        findings, errors = self.findings_from(body)

        # Assert
        self.assertEqual((), findings)
        self.assertTrue(any("id must be a non-empty string" in item for item in errors))

    def test_a_non_list_fix_version_field_is_ignored(self) -> None:
        # Arrange
        vulnerability = {"id": "PYSEC-1", "fix_versions": "1.0.1"}
        body = json.dumps(
            {"dependencies": [{"name": "a", "version": "1", "vulns": [vulnerability]}]}
        )

        # Act
        findings, errors = self.findings_from(body)

        # Assert
        self.assertEqual([], errors)
        self.assertEqual((), findings[0].fix_versions)

    def test_a_waiver_missing_a_review_date_is_rejected(self) -> None:
        # Arrange
        body = registry_text(
            {key: value for key, value in waiver().items() if key != "reviewed_on"}
        )

        # Act
        errors = self.waivers_from(body)

        # Assert
        self.assertTrue(any("reviewed_on must be a non-empty string" in item for item in errors))


class DependencyAuditHookTests(QualityGateTestCase):
    def audit_repository(self) -> tuple[Path, Path, dict[str, str]]:
        repository = self.temporary_repository()
        (repository / "pyproject.toml").write_text(
            '[project]\nname = "example"\n', encoding="utf-8"
        )
        (repository / "uv.lock").write_text("version = 1\n", encoding="utf-8")
        arguments_file, environment = self.install_argument_recorder(
            repository,
            "uv",
            "uv-arguments.txt",
        )
        return repository, arguments_file, environment

    def test_the_audit_requests_machine_readable_findings(self) -> None:
        # Arrange
        repository, arguments_file, environment = self.audit_repository()

        # Act
        result = self.run_hook("dependency-audit.sh", repository, environment=environment)

        # Assert
        self.assert_hook_succeeded(result)
        self.assertIn("--format json", arguments_file.read_text(encoding="utf-8"))

    def test_the_audit_adjudicates_its_findings_against_the_waiver_registry(self) -> None:
        # Arrange
        repository, arguments_file, environment = self.audit_repository()

        # Act
        result = self.run_hook("dependency-audit.sh", repository, environment=environment)

        # Assert
        self.assert_hook_succeeded(result)
        self.assertIn("tools.dependency_waiver_gate", arguments_file.read_text(encoding="utf-8"))


class PipAuditSourceTests(QualityGateTestCase):
    def test_every_pip_audit_finding_blocks_regardless_of_fix_availability(self) -> None:
        # Arrange
        path = self.temporary_directory() / "audit.json"
        path.write_text(
            json.dumps(
                {"dependencies": [{"name": "pkg", "version": "1.0", "vulns": [{"id": "PYSEC-1"}]}]}
            ),
            encoding="utf-8",
        )
        errors: list[str] = []

        # Act
        findings = dependency_waiver_gate.load_findings(path, "root", errors)

        # Assert
        self.assertEqual([], errors)
        self.assertEqual((True,), tuple(finding.blocking for finding in findings))

    def test_a_report_without_a_dependencies_array_is_rejected(self) -> None:
        # Arrange
        path = self.temporary_directory() / "audit.json"
        path.write_text("{}", encoding="utf-8")
        errors: list[str] = []

        # Act
        findings = dependency_waiver_gate.load_findings(path, "root", errors)

        # Assert
        self.assertEqual((), findings)
        self.assertIn("audit.json: dependencies is required by a pip-audit report", errors)


if __name__ == "__main__":
    unittest.main()
