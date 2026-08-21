"""Tests for adjudicating Trivy image and configuration findings under the waiver registry."""

from __future__ import annotations

import contextlib
import io
import json
import re
import unittest
from datetime import date
from pathlib import Path
from typing import Final

import pytest

from tools import compose_policy_gate, dependency_waiver_gate
from tools.quality_gate_tests.analysis.test_dependency_waivers import registry_text, waiver
from tools.quality_gate_tests.support import REPOSITORY_ROOT, QualityGateTestCase

TODAY: Final = date(2026, 8, 20)
IMAGE_DOMAIN: Final = "image:postgres"
CONFIG_DOMAIN: Final = "deploy-config"
TARGET: Final = "postgres:17.11-trixie (debian 13.0)"
DOCKERFILE: Final = "deploy/agent-mesh/Dockerfile"
FROM_PATTERN: Final = re.compile(r"^FROM\s+(\S+)", re.MULTILINE)
EXPECTED_REPOSITORIES: Final = 7
"""Five pulled images and two built ones in the committed stack."""


def _committed_repositories() -> list[str]:
    """Return every image repository the committed deploy/ stack names, without tag or digest."""
    errors: list[str] = []
    compose = compose_policy_gate.load_compose(REPOSITORY_ROOT / "deploy" / "compose.yaml", errors)
    if compose is None:
        message = f"deploy/compose.yaml did not load: {errors}"
        raise RuntimeError(message)
    references = [
        str(service["image"]) for service in compose.services.values() if "image" in service
    ]
    for dockerfile in sorted((REPOSITORY_ROOT / "deploy").glob("**/Dockerfile")):
        references.extend(FROM_PATTERN.findall(dockerfile.read_text(encoding="utf-8")))
    return sorted({reference.split("@", 1)[0].rsplit(":", 1)[0] for reference in references})


def vulnerability(**overrides: object) -> dict[str, object]:
    """Return one Trivy vulnerability entry with the given fields replaced or removed."""
    entry: dict[str, object] = {
        "VulnerabilityID": "CVE-2026-1000",
        "PkgName": "libssl3t64",
        "InstalledVersion": "3.5.6-1",
        "FixedVersion": "3.5.7-1",
        "Status": "fixed",
        "Severity": "HIGH",
    }
    for key, value in overrides.items():
        if value is None:
            entry.pop(key, None)
        else:
            entry[key] = value
    return entry


def misconfiguration(**overrides: object) -> dict[str, object]:
    """Return one Trivy misconfiguration entry with the given fields replaced or removed."""
    entry: dict[str, object] = {
        "ID": "DS-0002",
        "AVDID": "AVD-DS-0002",
        "Title": "Image user should not be 'root'",
        "Severity": "HIGH",
        "Status": "FAIL",
    }
    for key, value in overrides.items():
        if value is None:
            entry.pop(key, None)
        else:
            entry[key] = value
    return entry


def result(
    target: str = TARGET,
    vulnerabilities: list[object] | None = None,
    misconfigurations: list[object] | None = None,
) -> dict[str, object]:
    """Return one Trivy result for ``target`` carrying the given entries."""
    entry: dict[str, object] = {"Target": target, "Class": "os-pkgs", "Type": "debian"}
    if vulnerabilities is not None:
        entry["Vulnerabilities"] = vulnerabilities
    if misconfigurations is not None:
        entry["Misconfigurations"] = misconfigurations
    return entry


def trivy_report(*results: object, schema_version: object = 2) -> str:
    """Render a Trivy JSON report holding ``results``."""
    document: dict[str, object] = {"SchemaVersion": schema_version, "ArtifactName": TARGET}
    if results:
        document["Results"] = list(results)
    return json.dumps(document)


class TrivyTestCase(QualityGateTestCase):
    """Fixtures shared by the Trivy adjudication tests."""

    def report(self, text: str) -> Path:
        path = self.temporary_directory() / "trivy.json"
        path.write_text(text, encoding="utf-8")
        return path

    def findings(
        self, text: str, domain: str = IMAGE_DOMAIN
    ) -> tuple[tuple[dependency_waiver_gate.Finding, ...], list[str]]:
        errors: list[str] = []
        loaded = dependency_waiver_gate.load_trivy_findings(self.report(text), domain, errors)
        return loaded, errors


class TrivyVulnerabilityParsingTests(TrivyTestCase):
    def test_a_fixed_high_vulnerability_blocks(self) -> None:
        # Arrange
        text = trivy_report(result(vulnerabilities=[vulnerability()]))

        # Act
        loaded, errors = self.findings(text, CONFIG_DOMAIN)

        # Assert
        self.assertEqual([], errors)
        self.assertEqual((True,), tuple(finding.blocking for finding in loaded))

    def test_a_fixed_critical_vulnerability_blocks(self) -> None:
        # Arrange
        text = trivy_report(result(vulnerabilities=[vulnerability(Severity="CRITICAL")]))

        # Act
        loaded, _ = self.findings(text, CONFIG_DOMAIN)

        # Assert
        self.assertTrue(loaded[0].blocking)

    def test_a_fixed_medium_vulnerability_is_informational(self) -> None:
        # Arrange
        text = trivy_report(result(vulnerabilities=[vulnerability(Severity="MEDIUM")]))

        # Act
        loaded, _ = self.findings(text)

        # Assert
        self.assertFalse(loaded[0].blocking)

    def test_an_unfixed_high_vulnerability_is_informational(self) -> None:
        # Arrange
        text = trivy_report(result(vulnerabilities=[vulnerability(FixedVersion="")]))

        # Act
        loaded, _ = self.findings(text)

        # Assert
        self.assertFalse(loaded[0].blocking)
        self.assertEqual((), loaded[0].fix_versions)

    def test_an_absent_fixed_version_is_informational(self) -> None:
        # Arrange
        text = trivy_report(result(vulnerabilities=[vulnerability(FixedVersion=None)]))

        # Act
        loaded, errors = self.findings(text)

        # Assert
        self.assertEqual([], errors)
        self.assertFalse(loaded[0].blocking)

    def test_a_non_string_fixed_version_is_informational(self) -> None:
        # Arrange
        text = trivy_report(result(vulnerabilities=[vulnerability(FixedVersion=3)]))

        # Act
        loaded, _ = self.findings(text)

        # Assert
        self.assertFalse(loaded[0].blocking)

    def test_severity_is_compared_case_insensitively(self) -> None:
        # Arrange
        text = trivy_report(result(vulnerabilities=[vulnerability(Severity="high")]))

        # Act
        loaded, _ = self.findings(text, CONFIG_DOMAIN)

        # Assert
        self.assertTrue(loaded[0].blocking)
        self.assertEqual("HIGH", loaded[0].severity)

    def test_fixed_versions_are_split_on_commas(self) -> None:
        # Arrange
        text = trivy_report(
            result(vulnerabilities=[vulnerability(FixedVersion="3.5.7-1, 3.6.0-1")])
        )

        # Act
        loaded, _ = self.findings(text)

        # Assert
        self.assertEqual(("3.5.7-1", "3.6.0-1"), loaded[0].fix_versions)

    def test_a_repeated_vulnerability_across_results_is_reported_once(self) -> None:
        # Arrange
        text = trivy_report(
            result(vulnerabilities=[vulnerability()]),
            result(target="other", vulnerabilities=[vulnerability()]),
        )

        # Act
        loaded, _ = self.findings(text)

        # Assert
        self.assertEqual(1, len(loaded))

    def test_a_result_without_vulnerabilities_yields_no_finding(self) -> None:
        # Arrange
        text = trivy_report(result())

        # Act
        loaded, errors = self.findings(text)

        # Assert
        self.assertEqual((), loaded)
        self.assertEqual([], errors)

    def test_a_report_without_results_is_clean(self) -> None:
        # Arrange
        text = trivy_report()

        # Act
        loaded, errors = self.findings(text)

        # Assert
        self.assertEqual((), loaded)
        self.assertEqual([], errors)

    def test_every_finding_carries_the_domain_package_version_and_identifier(self) -> None:
        # Arrange
        text = trivy_report(result(vulnerabilities=[vulnerability()]))

        # Act
        loaded, _ = self.findings(text)

        # Assert
        self.assertEqual(
            (IMAGE_DOMAIN, "libssl3t64", "3.5.6-1", "CVE-2026-1000"), loaded[0].identity
        )


class ImageAdvisoriesAreReportedNotEnforcedTests(TrivyTestCase):
    """ADR-0055: the lever on a pinned image is its digest, not a package inside it."""

    def test_a_fixed_critical_advisory_in_an_image_does_not_block(self) -> None:
        # Arrange
        text = trivy_report(result(vulnerabilities=[vulnerability(Severity="CRITICAL")]))

        # Act
        loaded, errors = self.findings(text, IMAGE_DOMAIN)

        # Assert
        self.assertEqual([], errors)
        self.assertEqual((False,), tuple(finding.blocking for finding in loaded))

    def test_a_fixed_high_advisory_in_an_image_does_not_block(self) -> None:
        # Arrange
        text = trivy_report(result(vulnerabilities=[vulnerability(Severity="HIGH")]))

        # Act
        loaded, _ = self.findings(text, IMAGE_DOMAIN)

        # Assert
        self.assertFalse(loaded[0].blocking)

    def test_the_same_advisory_still_blocks_where_the_project_can_act(self) -> None:
        # Arrange
        text = trivy_report(result(vulnerabilities=[vulnerability(Severity="CRITICAL")]))

        # Act
        loaded, _ = self.findings(text, CONFIG_DOMAIN)

        # Assert
        self.assertTrue(loaded[0].blocking)

    def test_every_image_domain_is_recognised_as_an_image(self) -> None:
        # Arrange
        domains = ("image:postgres", "image:solace/solace-agent-mesh", "image:aerial-rescue/app")

        # Act
        recognised = tuple(dependency_waiver_gate.is_domain_image(name) for name in domains)

        # Assert
        self.assertEqual((True, True, True), recognised)

    def test_a_manifest_domain_is_not_an_image(self) -> None:
        # Arrange
        domains = ("root", "agent-mesh", "dashboard", CONFIG_DOMAIN)

        # Act
        recognised = tuple(dependency_waiver_gate.is_domain_image(name) for name in domains)

        # Assert
        self.assertEqual((False, False, False, False), recognised)


class TrivyMisconfigurationParsingTests(TrivyTestCase):
    def test_a_failed_high_misconfiguration_blocks(self) -> None:
        # Arrange
        text = trivy_report(result(target=DOCKERFILE, misconfigurations=[misconfiguration()]))

        # Act
        loaded, errors = self.findings(text, dependency_waiver_gate.CONFIG_DOMAIN)

        # Assert
        self.assertEqual([], errors)
        self.assertEqual(
            (dependency_waiver_gate.CONFIG_DOMAIN, DOCKERFILE, "config", "DS-0002"),
            loaded[0].identity,
        )
        self.assertTrue(loaded[0].blocking)

    def test_a_failed_critical_misconfiguration_blocks(self) -> None:
        # Arrange
        text = trivy_report(
            result(target=DOCKERFILE, misconfigurations=[misconfiguration(Severity="CRITICAL")])
        )

        # Act
        loaded, _ = self.findings(text, dependency_waiver_gate.CONFIG_DOMAIN)

        # Assert
        self.assertTrue(loaded[0].blocking)

    def test_a_failed_medium_misconfiguration_is_informational(self) -> None:
        # Arrange
        text = trivy_report(
            result(target=DOCKERFILE, misconfigurations=[misconfiguration(Severity="MEDIUM")])
        )

        # Act
        loaded, _ = self.findings(text, dependency_waiver_gate.CONFIG_DOMAIN)

        # Assert
        self.assertFalse(loaded[0].blocking)

    def test_a_passed_check_is_not_a_finding(self) -> None:
        # Arrange
        text = trivy_report(
            result(target=DOCKERFILE, misconfigurations=[misconfiguration(Status="PASS")])
        )

        # Act
        loaded, errors = self.findings(text, dependency_waiver_gate.CONFIG_DOMAIN)

        # Assert
        self.assertEqual((), loaded)
        self.assertEqual([], errors)

    def test_the_same_check_on_two_targets_is_two_findings(self) -> None:
        # Arrange
        text = trivy_report(
            result(target=DOCKERFILE, misconfigurations=[misconfiguration()]),
            result(target="deploy/application/Dockerfile", misconfigurations=[misconfiguration()]),
        )

        # Act
        loaded, _ = self.findings(text, dependency_waiver_gate.CONFIG_DOMAIN)

        # Assert
        self.assertEqual(2, len(loaded))


class TrivyMalformedReportTests(TrivyTestCase):
    def test_a_missing_report_is_an_error(self) -> None:
        # Arrange
        missing = self.temporary_directory() / "trivy.json"
        errors: list[str] = []

        # Act
        loaded = dependency_waiver_gate.load_trivy_findings(missing, IMAGE_DOMAIN, errors)

        # Assert
        self.assertEqual((), loaded)
        self.assertIn("missing trivy report: trivy.json", errors)

    def test_an_unparsable_report_is_an_error(self) -> None:
        # Arrange
        text = "{not json"

        # Act
        loaded, errors = self.findings(text)

        # Assert
        self.assertEqual((), loaded)
        self.assertTrue(any("cannot read the trivy report" in error for error in errors))

    def test_a_report_that_is_not_an_object_is_rejected(self) -> None:
        # Arrange
        text = "[]"

        # Act
        loaded, errors = self.findings(text)

        # Assert
        self.assertEqual((), loaded)
        self.assertIn("trivy.json: the trivy report must be an object", errors)

    def test_a_missing_schema_version_is_rejected(self) -> None:
        # Arrange
        text = json.dumps({"Results": []})

        # Act
        loaded, errors = self.findings(text)

        # Assert
        self.assertEqual((), loaded)
        self.assertIn("trivy.json: SchemaVersion must be integer 2", errors)

    def test_an_unsupported_schema_version_is_rejected(self) -> None:
        # Arrange
        text = trivy_report(schema_version=1)

        # Act
        loaded, errors = self.findings(text)

        # Assert
        self.assertEqual((), loaded)
        self.assertIn("trivy.json: SchemaVersion must be integer 2", errors)

    def test_a_results_value_of_the_wrong_type_is_rejected(self) -> None:
        # Arrange
        text = json.dumps({"SchemaVersion": 2, "Results": {}})

        # Act
        loaded, errors = self.findings(text)

        # Assert
        self.assertEqual((), loaded)
        self.assertIn("trivy.json: Results must be an array", errors)

    def test_a_result_that_is_not_an_object_is_rejected(self) -> None:
        # Arrange
        text = trivy_report("not-an-object")

        # Act
        loaded, errors = self.findings(text)

        # Assert
        self.assertEqual((), loaded)
        self.assertIn("trivy.json: Results[1] must be an object", errors)

    def test_a_result_without_a_target_yields_no_finding(self) -> None:
        # Arrange
        entry = result(vulnerabilities=[vulnerability()])
        del entry["Target"]
        text = trivy_report(entry)

        # Act
        loaded, errors = self.findings(text)

        # Assert
        self.assertEqual((), loaded)
        self.assertIn("trivy.json: Results[1].Target must be a non-empty string", errors)

    def test_a_vulnerability_list_of_the_wrong_type_is_rejected(self) -> None:
        # Arrange
        entry = result()
        entry["Vulnerabilities"] = {}
        text = trivy_report(entry)

        # Act
        loaded, errors = self.findings(text)

        # Assert
        self.assertEqual((), loaded)
        self.assertIn("trivy.json: Results[1].Vulnerabilities must be an array", errors)

    def test_a_vulnerability_that_is_not_an_object_is_rejected(self) -> None:
        # Arrange
        text = trivy_report(result(vulnerabilities=["CVE-2026-1000"]))

        # Act
        loaded, errors = self.findings(text)

        # Assert
        self.assertEqual((), loaded)
        self.assertIn("trivy.json: Results[1].Vulnerabilities[1] must be an object", errors)

    def test_a_vulnerability_without_an_identifier_yields_no_finding(self) -> None:
        # Arrange
        text = trivy_report(result(vulnerabilities=[vulnerability(VulnerabilityID=None)]))

        # Act
        loaded, errors = self.findings(text)

        # Assert
        self.assertEqual((), loaded)
        self.assertIn(
            "trivy.json: Results[1].Vulnerabilities[1].VulnerabilityID must be a non-empty string",
            errors,
        )

    def test_a_vulnerability_without_a_severity_yields_no_finding(self) -> None:
        # Arrange
        text = trivy_report(result(vulnerabilities=[vulnerability(Severity=None)]))

        # Act
        loaded, errors = self.findings(text)

        # Assert
        self.assertEqual((), loaded)
        self.assertIn(
            "trivy.json: Results[1].Vulnerabilities[1].Severity must be a non-empty string", errors
        )

    def test_a_misconfiguration_without_a_status_yields_no_finding(self) -> None:
        # Arrange
        text = trivy_report(
            result(target=DOCKERFILE, misconfigurations=[misconfiguration(Status=None)])
        )

        # Act
        loaded, errors = self.findings(text, dependency_waiver_gate.CONFIG_DOMAIN)

        # Assert
        self.assertEqual((), loaded)
        self.assertIn(
            "trivy.json: Results[1].Misconfigurations[1].Status must be a non-empty string", errors
        )


class ImageDomainTests(QualityGateTestCase):
    def test_image_and_configuration_domains_are_accepted(self) -> None:
        # Arrange
        names = ("image:solace/solace-pubsub-standard", "image:postgres", "deploy-config")

        # Act
        verdicts = tuple(dependency_waiver_gate.is_domain(name) for name in names)

        # Assert
        self.assertEqual((True, True, True), verdicts)

    def test_an_image_domain_with_a_tag_or_digest_is_rejected(self) -> None:
        # Arrange
        names = ("image:postgres:17.11", "image:postgres@sha256:abc")

        # Act
        verdicts = tuple(dependency_waiver_gate.is_domain(name) for name in names)

        # Assert
        self.assertEqual((False, False), verdicts)

    def test_an_empty_or_uppercase_image_domain_is_rejected(self) -> None:
        # Arrange
        names = ("image:", "image:Postgres", "images:postgres")

        # Act
        verdicts = tuple(dependency_waiver_gate.is_domain(name) for name in names)

        # Assert
        self.assertEqual((False, False, False), verdicts)

    def test_the_dependency_domains_are_still_accepted(self) -> None:
        # Arrange
        names = dependency_waiver_gate.DOMAINS

        # Act
        verdicts = tuple(dependency_waiver_gate.is_domain(name) for name in names)

        # Assert
        self.assertEqual((True,) * len(names), verdicts)

    def test_the_registry_accepts_an_image_domain(self) -> None:
        # Arrange
        path = self.temporary_directory() / "dependency-waivers.toml"
        path.write_text(registry_text(waiver(domain=IMAGE_DOMAIN)), encoding="utf-8")
        errors: list[str] = []

        # Act
        records = dependency_waiver_gate.load_waivers(path, errors)

        # Assert
        self.assertEqual([], errors)
        self.assertEqual(IMAGE_DOMAIN, records[0].domain)

    def test_the_registry_rejects_a_tagged_image_domain(self) -> None:
        # Arrange
        path = self.temporary_directory() / "dependency-waivers.toml"
        path.write_text(registry_text(waiver(domain="image:postgres:17.11")), encoding="utf-8")
        errors: list[str] = []

        # Act
        dependency_waiver_gate.load_waivers(path, errors)

        # Assert
        self.assertTrue(any("domain must be one of" in error for error in errors))


class TrivyCommandTests(TrivyTestCase):
    def invoke(
        self, report: str, registry: str = registry_text(), *extra: str
    ) -> tuple[int, str, str]:
        """Run the gate on a Trivy report with ``extra`` arguments, capturing both streams."""
        directory = self.temporary_directory()
        report_path = directory / "trivy.json"
        report_path.write_text(report, encoding="utf-8")
        registry_path = directory / "dependency-waivers.toml"
        registry_path.write_text(registry, encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        arguments = [
            "--report",
            str(report_path),
            "--registry",
            str(registry_path),
            "--today",
            TODAY.isoformat(),
            *extra,
        ]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = dependency_waiver_gate.main(arguments)
        return status, out.getvalue(), err.getvalue()

    def test_an_unwaived_image_advisory_exits_zero_and_prints_it(self) -> None:
        # Arrange
        report = trivy_report(result(vulnerabilities=[vulnerability(Severity="CRITICAL")]))

        # Act
        status, out, err = self.invoke(
            report, registry_text(), "--source", "trivy", "--domain", IMAGE_DOMAIN
        )

        # Assert
        self.assertEqual((0, ""), (status, err))
        self.assertIn("INFO: CRITICAL fixed: libssl3t64 3.5.6-1 CVE-2026-1000", out)

    def test_an_unwaived_blocking_finding_exits_non_zero(self) -> None:
        # Arrange
        report = trivy_report(result(vulnerabilities=[vulnerability()]))

        # Act
        status, _, err = self.invoke(
            report, registry_text(), "--source", "trivy", "--domain", CONFIG_DOMAIN
        )

        # Assert
        self.assertEqual(1, status)
        self.assertIn(
            "DEPENDENCY: unwaived advisory: libssl3t64 3.5.6-1 CVE-2026-1000 in deploy-config", err
        )

    def test_a_current_waiver_covers_a_blocking_finding(self) -> None:
        # Arrange
        report = trivy_report(result(vulnerabilities=[vulnerability()]))
        registry = registry_text(
            waiver(
                domain=CONFIG_DOMAIN,
                package="libssl3t64",
                version="3.5.6-1",
                advisory="CVE-2026-1000",
                reviewed_on="2026-08-20",
                expires_on="2026-09-19",
            )
        )

        # Act
        status, out, err = self.invoke(
            report, registry, "--source", "trivy", "--domain", CONFIG_DOMAIN
        )

        # Assert
        self.assertEqual(0, status, err)
        self.assertEqual("", out)

    def test_informational_findings_are_listed_on_stdout_and_do_not_block(self) -> None:
        # Arrange
        report = trivy_report(
            result(
                vulnerabilities=[
                    vulnerability(Severity="MEDIUM"),
                    vulnerability(VulnerabilityID="CVE-2026-2000", FixedVersion=""),
                ]
            )
        )

        # Act
        status, out, err = self.invoke(
            report, registry_text(), "--source", "trivy", "--domain", IMAGE_DOMAIN
        )

        # Assert
        self.assertEqual(0, status)
        self.assertEqual("", err)
        self.assertEqual(
            "INFO: HIGH unfixed: libssl3t64 3.5.6-1 CVE-2026-2000 in image:postgres\n"
            "INFO: MEDIUM fixed: libssl3t64 3.5.6-1 CVE-2026-1000 in image:postgres\n",
            out,
        )

    def test_a_clean_report_prints_nothing(self) -> None:
        # Arrange
        report = trivy_report()

        # Act
        status, out, err = self.invoke(
            report, registry_text(), "--source", "trivy", "--domain", IMAGE_DOMAIN
        )

        # Assert
        self.assertEqual(0, status)
        self.assertEqual(("", ""), (out, err))

    def test_a_waiver_for_an_informational_finding_is_stale(self) -> None:
        # Arrange
        report = trivy_report(result(vulnerabilities=[vulnerability(Severity="LOW")]))
        registry = registry_text(
            waiver(
                domain=IMAGE_DOMAIN,
                package="libssl3t64",
                version="3.5.6-1",
                advisory="CVE-2026-1000",
                reviewed_on="2026-08-20",
                expires_on="2026-09-19",
            )
        )

        # Act
        status, _, err = self.invoke(
            report, registry, "--source", "trivy", "--domain", IMAGE_DOMAIN
        )

        # Assert
        self.assertEqual(1, status)
        self.assertIn("stale waiver", err)

    def test_a_misconfiguration_is_adjudicated_in_the_configuration_domain(self) -> None:
        # Arrange
        report = trivy_report(result(target=DOCKERFILE, misconfigurations=[misconfiguration()]))

        # Act
        status, _, err = self.invoke(
            report, registry_text(), "--source", "trivy", "--domain", "deploy-config"
        )

        # Assert
        self.assertEqual(1, status)
        self.assertIn(f"unwaived advisory: {DOCKERFILE} config DS-0002 in deploy-config", err)

    def test_a_trivy_report_read_as_pip_audit_is_rejected(self) -> None:
        # Arrange
        report = trivy_report(result(vulnerabilities=[vulnerability()]))

        # Act
        status, _, err = self.invoke(report, registry_text(), "--domain", "root")

        # Assert
        self.assertEqual(1, status)
        self.assertIn("dependencies is required by a pip-audit report", err)

    def test_a_pip_audit_report_read_as_trivy_is_rejected(self) -> None:
        # Arrange
        report = json.dumps({"dependencies": []})

        # Act
        status, _, err = self.invoke(
            report, registry_text(), "--source", "trivy", "--domain", IMAGE_DOMAIN
        )

        # Assert
        self.assertEqual(1, status)
        self.assertIn("SchemaVersion must be integer 2", err)

    def test_an_unknown_source_is_refused(self) -> None:
        # Arrange
        report = trivy_report()

        # Act
        with contextlib.redirect_stderr(io.StringIO()), pytest.raises(SystemExit) as raised:
            self.invoke(report, registry_text(), "--source", "grype", "--domain", IMAGE_DOMAIN)

        # Assert
        self.assertEqual(2, raised.value.code)

    def test_a_tagged_image_domain_is_refused_on_the_command_line(self) -> None:
        # Arrange
        report = trivy_report()

        # Act
        with contextlib.redirect_stderr(io.StringIO()), pytest.raises(SystemExit) as raised:
            self.invoke(
                report, registry_text(), "--source", "trivy", "--domain", "image:postgres:17"
            )

        # Assert
        self.assertEqual(2, raised.value.code)


class PolicyConstantTests(QualityGateTestCase):
    def test_the_blocking_severities_are_high_and_critical(self) -> None:
        # Arrange
        expected = frozenset({"HIGH", "CRITICAL"})

        # Act
        severities = dependency_waiver_gate.TRIVY_BLOCKING_SEVERITIES

        # Assert
        self.assertEqual(expected, severities)

    def test_the_configuration_domain_and_version_are_fixed(self) -> None:
        # Arrange
        expected = ("deploy-config", "config", 2)

        # Act
        actual = (
            dependency_waiver_gate.CONFIG_DOMAIN,
            dependency_waiver_gate.CONFIG_VERSION,
            dependency_waiver_gate.TRIVY_SCHEMA_VERSION,
        )

        # Assert
        self.assertEqual(expected, actual)

    def test_the_image_domain_pattern_accepts_every_repository_in_the_committed_stack(self) -> None:
        # Arrange
        repositories = _committed_repositories()

        # Act
        verdicts = {
            repository: dependency_waiver_gate.is_domain(f"image:{repository}")
            for repository in repositories
        }

        # Assert
        self.assertEqual(EXPECTED_REPOSITORIES, len(repositories), repositories)
        self.assertTrue(all(verdicts.values()), verdicts)


if __name__ == "__main__":
    unittest.main()
