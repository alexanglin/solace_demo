"""Fail-closed, per-member mutation-score enforcement for tier-one code.

The runner executes ``mutmut`` independently from each tier-one workspace member.
That gives every safety-critical member its own cache, test selection, and score, so
one well-tested member cannot hide another member's surviving mutants.
"""

from __future__ import annotations

import argparse
import ast
import io
import json
import sys
import tokenize
import tomllib
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import cast

from tools.member_scaffold import SCAFFOLD_DETAIL, SCAFFOLD_OUTCOME, is_scaffold

MUTATION_SCORE_PERCENT = 90
MAX_SURVIVOR_REVIEW_DAYS = 30
MIN_SURVIVOR_REASON_CHARACTERS = 20
KILLED_EXIT_CODES = frozenset({1, 3})
SURVIVED_EXIT_CODE = 0
STATUS_BY_EXIT_CODE: dict[int | None, str] = {
    None: "not checked",
    0: "survived",
    1: "killed",
    2: "check interrupted",
    3: "killed",
    5: "no tests",
    24: "timeout",
    33: "no tests",
    34: "skipped",
    35: "suspicious",
    36: "timeout",
    37: "caught by type check",
    -9: "segfault",
    -11: "segfault",
    -24: "timeout",
    152: "timeout",
    255: "timeout",
}


@dataclass(frozen=True)
class SurvivorRecord:
    """One time-bounded human review of an exact surviving mutant."""

    member: str
    mutant: str
    reason: str
    reviewed_by: str
    reviewed_on: date
    expires_on: date


@dataclass(frozen=True)
class MutationVerdict:
    """The independently evaluated mutation outcome for one tier-one member."""

    member: str
    killed: int
    survived: int
    outcome: str
    detail: str
    errors: tuple[str, ...]


class MutationConfigurationError(ValueError):
    """The workspace or survivor registry cannot be evaluated safely."""


def _mapping(value: object, *, context: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        message = f"{context} must be a string-keyed table"
        raise MutationConfigurationError(message)
    return cast(dict[str, object], value)


def _string(table: dict[str, object], key: str, *, context: str) -> str:
    value = table.get(key)
    if not isinstance(value, str) or not value.strip():
        message = f"{context}.{key} must be a non-empty string"
        raise MutationConfigurationError(message)
    return value.strip()


def _iso_date(table: dict[str, object], key: str, *, context: str) -> date:
    raw = _string(table, key, context=context)
    try:
        return date.fromisoformat(raw)
    except ValueError as error:
        message = f"{context}.{key} must be an ISO-8601 calendar date"
        raise MutationConfigurationError(message) from error


def _parse_survivor_record(
    path: Path,
    index: int,
    raw_record: object,
    seen: set[tuple[str, str]],
) -> SurvivorRecord:
    """Parse one survivor entry and reject unknown or duplicate identity fields."""
    context = f"{path}: survivors[{index}]"
    record = _mapping(raw_record, context=context)
    known = {
        "member",
        "mutant",
        "reason",
        "reviewed_by",
        "reviewed_on",
        "expires_on",
    }
    unknown = set(record) - known
    if unknown:
        names = ", ".join(sorted(unknown))
        message = f"{context} has unknown fields: {names}"
        raise MutationConfigurationError(message)
    reason = _string(record, "reason", context=context)
    if len(reason) < MIN_SURVIVOR_REASON_CHARACTERS:
        message = (
            f"{context}.reason must contain at least {MIN_SURVIVOR_REASON_CHARACTERS} characters"
        )
        raise MutationConfigurationError(message)
    survivor = SurvivorRecord(
        member=_string(record, "member", context=context),
        mutant=_string(record, "mutant", context=context),
        reason=reason,
        reviewed_by=_string(record, "reviewed_by", context=context),
        reviewed_on=_iso_date(record, "reviewed_on", context=context),
        expires_on=_iso_date(record, "expires_on", context=context),
    )
    identity = (survivor.member, survivor.mutant)
    if identity in seen:
        message = f"{context} duplicates survivor record {survivor.member}:{survivor.mutant}"
        raise MutationConfigurationError(message)
    seen.add(identity)
    return survivor


def _load_survivor_records(path: Path) -> tuple[SurvivorRecord, ...]:
    if not path.is_file():
        message = f"missing survivor registry: {path}"
        raise MutationConfigurationError(message)
    try:
        data = _mapping(tomllib.loads(path.read_text(encoding="utf-8")), context=str(path))
    except (OSError, tomllib.TOMLDecodeError) as error:
        message = f"cannot read survivor registry {path}: {error}"
        raise MutationConfigurationError(message) from error
    if type(data.get("format")) is not int or data["format"] != 1:
        message = f"{path}: format must be integer 1"
        raise MutationConfigurationError(message)
    raw_records = data.get("survivors", [])
    if not isinstance(raw_records, list):
        message = f"{path}: survivors must be an array of tables"
        raise MutationConfigurationError(message)

    records: list[SurvivorRecord] = []
    seen: set[tuple[str, str]] = set()
    for index, raw_record in enumerate(raw_records, start=1):
        records.append(_parse_survivor_record(path, index, raw_record, seen))
    return tuple(records)


def _metadata_statuses(
    metadata_root: Path,
) -> tuple[dict[str, dict[str, int | None]], list[str]]:
    statuses_by_module: dict[str, dict[str, int | None]] = {}
    errors: list[str] = []
    for path in sorted(metadata_root.rglob("*.py.meta")) if metadata_root.is_dir() else ():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            metadata = _mapping(raw, context=str(path))
            exit_codes = _mapping(metadata.get("exit_code_by_key"), context=f"{path}: exit codes")
        except (OSError, json.JSONDecodeError, MutationConfigurationError) as error:
            errors.append(str(error))
            continue
        module = path.relative_to(metadata_root).as_posix().removesuffix(".meta")
        module_statuses: dict[str, int | None] = {}
        for mutant, raw_status in sorted(exit_codes.items()):
            if raw_status is not None and type(raw_status) is not int:
                errors.append(f"{path}: {mutant} has a non-integer exit code")
                continue
            module_statuses[mutant] = raw_status
        statuses_by_module[module] = module_statuses
    return statuses_by_module, errors


def _review_errors(
    member: str,
    statuses: dict[str, int | None],
    records: tuple[SurvivorRecord, ...],
    *,
    today: date,
) -> list[str]:
    errors: list[str] = []
    member_records = {record.mutant: record for record in records if record.member == member}
    for mutant, record in sorted(member_records.items()):
        if mutant not in statuses:
            errors.append(f"{member}: stale survivor record for {mutant}")
        elif statuses[mutant] != SURVIVED_EXIT_CODE:
            errors.append(f"{member}: survivor record for {mutant} is not a surviving mutant")
        errors.extend(_record_date_errors(member, mutant, record, today=today))

    valid_records = {
        mutant
        for mutant, record in member_records.items()
        if _record_is_current(record, today=today)
    }
    for mutant, status in sorted(statuses.items()):
        if status == SURVIVED_EXIT_CODE and mutant not in valid_records:
            errors.append(f"{member}: unreviewed survivor {mutant}")
    return errors


def validate_registry_scope(
    root: Path,
    tier_one_members: tuple[str, ...],
    *,
    today: date,
) -> tuple[str, ...]:
    """Reject survivor reviews outside the current Tier 1 evaluation scope."""
    records = _load_survivor_records(root / "mutation-survivors.toml")
    allowed_members = frozenset(tier_one_members)
    errors: list[str] = []
    for record in records:
        if record.member not in allowed_members:
            errors.append(
                f"{record.member}: survivor record for {record.mutant} names a non-tier-one member"
            )
            errors.extend(_record_date_errors(record.member, record.mutant, record, today=today))
    return tuple(errors)


def _record_is_current(record: SurvivorRecord, *, today: date) -> bool:
    """Return whether a survivor review is current and within its maximum lifetime."""
    review_days = (record.expires_on - record.reviewed_on).days
    return record.reviewed_on <= today < record.expires_on and (
        0 < review_days <= MAX_SURVIVOR_REVIEW_DAYS
    )


def _record_date_errors(
    member: str,
    mutant: str,
    record: SurvivorRecord,
    *,
    today: date,
) -> list[str]:
    """Return deterministic diagnostics for one review's temporal bounds."""
    errors: list[str] = []
    if record.reviewed_on > today:
        errors.append(f"{member}: survivor record for {mutant} is reviewed in the future")
    if record.expires_on <= today:
        errors.append(f"{member}: survivor record for {mutant} expired on {record.expires_on}")
    review_days = (record.expires_on - record.reviewed_on).days
    if review_days <= 0 or review_days > MAX_SURVIVOR_REVIEW_DAYS:
        errors.append(
            f"{member}: survivor record for {mutant} must expire within "
            f"{MAX_SURVIVOR_REVIEW_DAYS} days of review"
        )
    return errors


def _flatten_statuses(
    member: str,
    statuses_by_module: dict[str, dict[str, int | None]],
) -> tuple[dict[str, int | None], list[str]]:
    """Flatten module results while rejecting duplicate mutant identities."""
    statuses: dict[str, int | None] = {}
    errors: list[str] = []
    for module_statuses in statuses_by_module.values():
        for mutant, status in module_statuses.items():
            if mutant in statuses:
                errors.append(f"{member}: duplicate mutation result {mutant}")
            else:
                statuses[mutant] = status
    return statuses, errors


def _module_errors(
    member: str,
    module: str,
    statuses: dict[str, int | None],
) -> list[str]:
    """Enforce terminal statuses and the score for one Python module."""
    errors: list[str] = []
    killed = sum(status in KILLED_EXIT_CODES for status in statuses.values())
    survived = sum(status == SURVIVED_EXIT_CODE for status in statuses.values())
    scored = killed + survived
    if scored == 0:
        errors.append(f"{member}/{module}: no scored mutation results")
    elif killed * 100 < MUTATION_SCORE_PERCENT * scored:
        score = killed * 100.0 / scored
        errors.append(
            f"{member}/{module}: mutation score {score:.2f}% is below {MUTATION_SCORE_PERCENT}%"
        )
    for mutant, status in sorted(statuses.items()):
        if status in KILLED_EXIT_CODES or status == SURVIVED_EXIT_CODE:
            continue
        label = STATUS_BY_EXIT_CODE.get(status, f"unknown exit code {status}")
        errors.append(f"{member}: {mutant} is {label}")
    return errors


def evaluate_member(root: Path, member: str, *, today: date) -> MutationVerdict:
    """Evaluate one member's pinned mutmut metadata and survivor reviews."""
    statuses_by_module, errors = _metadata_statuses(root / member / "mutants")
    try:
        records = _load_survivor_records(root / "mutation-survivors.toml")
    except MutationConfigurationError as error:
        records = ()
        errors.append(str(error))

    statuses, duplicate_errors = _flatten_statuses(member, statuses_by_module)
    errors.extend(duplicate_errors)
    killed = sum(status in KILLED_EXIT_CODES for status in statuses.values())
    survived = sum(status == SURVIVED_EXIT_CODE for status in statuses.values())
    scored = killed + survived
    if not statuses_by_module:
        errors.append(f"{member}: no mutation results; an active tier-one member cannot pass")
    for module, module_statuses in sorted(statuses_by_module.items()):
        errors.extend(_module_errors(member, module, module_statuses))
    errors.extend(_review_errors(member, statuses, records, today=today))

    score = 0.0 if scored == 0 else killed * 100.0 / scored
    outcome = "PASS" if not errors else "FAIL"
    detail = f"{score:.2f}% ({killed}/{scored} killed); required {MUTATION_SCORE_PERCENT}%"
    return MutationVerdict(member, killed, survived, outcome, detail, tuple(errors))


def _workspace_members(root: Path) -> tuple[Path, ...]:
    pyproject = root / "pyproject.toml"
    try:
        data = _mapping(
            tomllib.loads(pyproject.read_text(encoding="utf-8")), context=str(pyproject)
        )
    except (OSError, tomllib.TOMLDecodeError) as error:
        message = f"cannot read workspace manifest {pyproject}: {error}"
        raise MutationConfigurationError(message) from error
    tool = _mapping(data.get("tool"), context=f"{pyproject}: tool")
    uv = _mapping(tool.get("uv"), context=f"{pyproject}: tool.uv")
    workspace = _mapping(uv.get("workspace"), context=f"{pyproject}: tool.uv.workspace")
    patterns = workspace.get("members")
    if not isinstance(patterns, list) or not all(isinstance(item, str) for item in patterns):
        message = f"{pyproject}: [tool.uv.workspace].members must be a list of glob strings"
        raise MutationConfigurationError(message)
    members = {
        path
        for pattern in cast(list[str], patterns)
        for path in root.glob(pattern)
        if path.is_dir()
    }
    return tuple(sorted(members))


def discover_tier_one_members(root: Path) -> tuple[str, ...]:
    """Return every explicitly declared tier-one workspace member."""
    members: list[str] = []
    for member in _workspace_members(root):
        manifest = member / "pyproject.toml"
        if not manifest.is_file():
            relative = member.relative_to(root).as_posix()
            message = f"missing workspace member manifest: {relative}/pyproject.toml"
            raise MutationConfigurationError(message)
        data = _mapping(tomllib.loads(manifest.read_text(encoding="utf-8")), context=str(manifest))
        tool = _mapping(data.get("tool"), context=f"{manifest}: tool")
        project = _mapping(tool.get("aerial-rescue"), context=f"{manifest}: tool.aerial-rescue")
        tier = project.get("risk-tier")
        if type(tier) is not int or tier not in {1, 2, 3}:
            message = f"{manifest}: risk-tier must be integer 1, 2, or 3"
            raise MutationConfigurationError(message)
        if tier == 1:
            members.append(member.relative_to(root).as_posix())
    if not members:
        message = "the workspace declares no tier-one members"
        raise MutationConfigurationError(message)
    return tuple(members)


def validate_member_configuration(root: Path, member: str) -> tuple[str, ...]:
    """Validate the per-member configuration needed by a safe mutmut run."""
    errors: list[str] = []
    member_root = root / member
    manifest = member_root / "pyproject.toml"
    data = _mapping(tomllib.loads(manifest.read_text(encoding="utf-8")), context=str(manifest))
    tool = _mapping(data.get("tool"), context=f"{manifest}: tool")
    config = _mapping(tool.get("mutmut"), context=f"{manifest}: tool.mutmut")
    errors.extend(_mutmut_configuration_errors(member, config))
    errors.extend(_member_source_errors(member_root, member))
    return tuple(errors)


def _mutmut_configuration_errors(member: str, config: dict[str, object]) -> list[str]:
    """Validate the exact mutmut settings required for cache-safe member runs."""
    errors: list[str] = []
    if config.get("source_paths") != ["src"]:
        errors.append(f"{member}: [tool.mutmut].source_paths must be exactly ['src']")
    if config.get("pytest_add_cli_args_test_selection") != ["tests"]:
        errors.append(
            f"{member}: [tool.mutmut].pytest_add_cli_args_test_selection must be exactly ['tests']"
        )
    if config.get("on_dependency_change") != "rerun":
        errors.append(f"{member}: [tool.mutmut].on_dependency_change must be 'rerun'")
    expected_invalidation = [
        "tests/**/*.py",
        "pyproject.toml",
        "../../pyproject.toml",
        "../../uv.lock",
        "../../packages/*/pyproject.toml",
        "../../packages/*/src/**/*.py",
        "../../services/*/pyproject.toml",
        "../../services/*/src/**/*.py",
    ]
    if config.get("cache_invalidation_files") != expected_invalidation:
        errors.append(
            f"{member}: [tool.mutmut].cache_invalidation_files must track tests, "
            "the member manifest, and the workspace lock"
        )
    forbidden = {
        "do_not_mutate",
        "do_not_mutate_patterns",
        "mutate_only_covered_lines",
        "only_mutate",
        "type_check_command",
    }
    configured_forbidden = sorted(forbidden & set(config))
    if configured_forbidden:
        errors.append(
            f"{member}: mutation exclusions are prohibited: {', '.join(configured_forbidden)}"
        )
    if "also_copy" in config:
        errors.append(f"{member}: [tool.mutmut].also_copy is prohibited")
    if config.get("use_git_change_detection", True) is not True:
        errors.append(f"{member}: [tool.mutmut].use_git_change_detection must remain true")
    return errors


def _member_source_errors(member_root: Path, member: str) -> list[str]:
    """Reject missing tests, empty safety modules, and mutation suppressions."""
    errors: list[str] = []
    if not (member_root / "tests").is_dir():
        errors.append(f"{member}: co-located tests/ is required for independent mutation scoring")
    elif not any((member_root / "tests").rglob("test_*.py")):
        errors.append(f"{member}: co-located tests/ contains no test_*.py modules")

    functions = 0
    for source in sorted((member_root / "src").rglob("*.py")):
        text = source.read_text(encoding="utf-8")
        try:
            tree = ast.parse(text, filename=str(source))
            comments = (
                token.string.strip().lower()
                for token in tokenize.generate_tokens(io.StringIO(text).readline)
                if token.type == tokenize.COMMENT
            )
        except (SyntaxError, tokenize.TokenError) as error:
            errors.append(f"{member}: cannot inspect {source}: {error}")
            continue
        functions += sum(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) for node in ast.walk(tree)
        )
        if any(comment.startswith("# pragma: no mutate") for comment in comments):
            errors.append(f"{member}: mutation suppression pragma is prohibited in {source}")
    if functions == 0:
        errors.append(f"{member}: tier-one source contains no mutation-eligible functions")
    return errors


def _print_verdict(verdict: MutationVerdict) -> None:
    stream = sys.stderr if verdict.outcome == "FAIL" else sys.stdout
    print(f"{verdict.outcome:6} {verdict.member:28} {verdict.detail}", file=stream)
    for error in verdict.errors:
        print(f"       {error}", file=sys.stderr)


def partition_tier_one_members(root: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split the declared tier-one members into active and scaffolded (``docs/adr/0053``)."""
    members = discover_tier_one_members(root)
    scaffolded = tuple(member for member in members if is_scaffold(root / member))
    active = tuple(member for member in members if member not in scaffolded)
    return active, scaffolded


def _scaffold_line(member: str) -> str:
    return f"{SCAFFOLD_OUTCOME:6} {member:28} {SCAFFOLD_DETAIL}"


def _preflight(
    root: Path,
    member: str,
    active: tuple[str, ...],
    scaffolded: tuple[str, ...],
) -> int:
    if member in scaffolded:
        print(_scaffold_line(member))
        return 0
    if member not in active:
        print(f"FAIL: {member} is not a tier-one member", file=sys.stderr)
        return 1
    errors = validate_member_configuration(root, member)
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


def _evaluate(root: Path, active: tuple[str, ...], scaffolded: tuple[str, ...]) -> int:
    today = date.today()
    registry_errors = validate_registry_scope(root, active + scaffolded, today=today)
    verdicts = tuple(evaluate_member(root, member, today=today) for member in active)
    for member in scaffolded:
        print(_scaffold_line(member))
    for verdict in verdicts:
        _print_verdict(verdict)
    for registry_error in registry_errors:
        print(registry_error, file=sys.stderr)
    failed = bool(registry_errors) or any(verdict.outcome == "FAIL" for verdict in verdicts)
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    """List, preflight, or evaluate tier-one mutation results."""
    parser = argparse.ArgumentParser()
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--list-tier-one", action="store_true")
    action.add_argument("--preflight", metavar="MEMBER")
    action.add_argument("--evaluate", action="store_true")
    args = parser.parse_args(argv)
    root = Path.cwd()
    try:
        active, scaffolded = partition_tier_one_members(root)
        if args.list_tier_one:
            for member in scaffolded:
                print(_scaffold_line(member), file=sys.stderr)
            print("\n".join(active))
            return 0
        if args.preflight:
            return _preflight(root, args.preflight, active, scaffolded)
        return _evaluate(root, active, scaffolded)
    except (MutationConfigurationError, OSError, tomllib.TOMLDecodeError) as exception:
        print(f"FAIL: mutation gate configuration: {exception}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
