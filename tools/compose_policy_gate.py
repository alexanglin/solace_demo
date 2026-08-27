"""Fail-closed policy gate for the Docker Compose stack under ``deploy/``.

``docs/adr/0044`` fixes what the stack must look like and ``docs/adr/0045`` records this gate:
every pulled image pinned by tag and digest, every published port bound to loopback, secrets
as files or environment indirection rather than literals, a healthcheck on every long-running
service, an enumerated completion policy for one-shot jobs, the
broker and Agent Mesh services shaped as the records require, and every Dockerfile built from
a digest-pinned base with hashed ``pip`` installs.

This module is pure. It parses the files it is handed and never launches a process: the hook
script enumerates ``deploy/`` because ``docs/adr/0025`` confines ``subprocess`` to four
reviewed owners, and Docker never enters the commit path. The interpolation scan is
deliberately conservative -- a nested default such as ``${A:-${B}}`` is checked for ``A`` only.
"""

from __future__ import annotations

import argparse
import posixpath
import re
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, cast

import yaml

SECRET_NAME_PATTERN: Final = re.compile(
    r"(^|_)(API_?KEY|KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIALS?|AUTH|AUTHORIZATION|BEARER"
    r"|SIGNATURE|SALT)(_|$)",
    re.IGNORECASE,
)
"""Byte-identical to ``SECRET_NAME`` in ``scripts/hooks/check-env-template.sh``; a test asserts it.

The hook and the gate must recognise the same names as credentials.
"""

URL_USERINFO_PATTERN: Final = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^/@\s]+:[^/@\s]+@")
INTERPOLATION_PATTERN: Final = re.compile(
    r"(?<!\$)\$(?:\{([A-Za-z_][A-Za-z0-9_]*)[^}]*\}|([A-Za-z_][A-Za-z0-9_]*))"
)
INDIRECTION_PATTERN: Final = re.compile(r"^\$\{[A-Za-z_][A-Za-z0-9_]*(:?[-+?][^}]*)?\}$")
PINNED_IMAGE_PATTERN: Final = re.compile(
    r"^[a-z0-9][a-z0-9._/-]*:([A-Za-z0-9._-]+)@sha256:[0-9a-f]{64}$"
)
LOOPBACK_SHORT_PORT_PATTERN: Final = re.compile(
    r"^127\.0\.0\.1:(?:\d{1,5}|\$\{[A-Za-z_][A-Za-z0-9_]*:-\d{1,5}\})"
    r":(?P<container>\d{1,5})(?:/(?:tcp|udp))?$"
)
PIP_INSTALL_PATTERN: Final = re.compile(
    r"(?:^|[\s/])(?:pip3?|python3?(?:\.\d+)?\s+-m\s+pip|uv\s+pip)\s+install(?:\s|$)"
)

ASSIGNMENT_PATTERN: Final = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=", re.MULTILINE)
DECLARATION_PATTERN: Final = re.compile(r"(?:^|\s)([A-Za-z_][A-Za-z0-9_]*)=")

BROKER_SERVICE: Final = "broker"
AGENT_MESH_SERVICE: Final = "agent-mesh"
BROKER_TLS_SMF_PORT: Final = 55443
BROKER_FORBIDDEN_PORTS: Final = frozenset({55555, 8080})
"""Plaintext SMF and SEMP; published neither to the host nor anywhere else."""

PLATFORM_ALLOWLIST: Final[Mapping[str, str]] = {"event-management-agent": "linux/amd64"}
"""The one image published for amd64 only; everything else runs native arm64."""

KNOWN_PROFILES: Final = frozenset({"mesh", "services", "event-portal", "mission-control"})
ONE_SHOT_SERVICES: Final = frozenset({"migration", "replay-validator"})
COMPOSE_FILE_SOURCE_ROOT: Final = "./secrets/"
CONTAINER_MOUNT_PREFIX: Final = "/run/secrets/"
LOOPBACK: Final = "127.0.0.1"
LATEST_TAG: Final = "latest"
HOST_NETWORK: Final = "host"
DEV_MODE_OFF: Final = "false"
NO_COMPOSE: Final = "no compose file was given; the gate cannot admit an empty stack"
PINNED_IMAGE_FORM: Final = "name:tag@sha256:<64 hex digits>"
PORT_FORM: Final = "127.0.0.1:<host>:<container> with single integer ports"

FROM_INSTRUCTION: Final = "FROM"
RUN_INSTRUCTION: Final = "RUN"
STAGE_KEYWORD: Final = "AS"
DECLARING_INSTRUCTIONS: Final = frozenset({"ARG", "ENV"})
SUBSTITUTED_INSTRUCTIONS: Final = frozenset(
    {
        "ADD",
        "COPY",
        "ENV",
        "EXPOSE",
        "FROM",
        "LABEL",
        "STOPSIGNAL",
        "USER",
        "VOLUME",
        "WORKDIR",
        "ONBUILD",
    }
)
BUILDKIT_PREDECLARED_ARGS: Final = frozenset(
    {
        "TARGETPLATFORM",
        "TARGETOS",
        "TARGETARCH",
        "TARGETVARIANT",
        "BUILDPLATFORM",
        "BUILDOS",
        "BUILDARCH",
        "BUILDVARIANT",
    }
)


@dataclass(frozen=True)
class ComposeFile:
    """One parsed compose document together with the text it was parsed from."""

    path: str
    document: Mapping[str, object]
    text: str

    @property
    def services(self) -> Mapping[str, Mapping[str, object]]:
        """Return the services that are mappings, keyed by name."""
        raw = self.document.get("services")
        if not isinstance(raw, Mapping):
            return {}
        return {str(name): spec for name, spec in raw.items() if isinstance(spec, Mapping)}


@dataclass(frozen=True)
class Dockerfile:
    """One Dockerfile's text."""

    path: str
    text: str


Instruction = tuple[int, str, str]
ServiceRule = Callable[[str, Mapping[str, object]], list[str]]


def declared_names(template_text: str) -> frozenset[str]:
    """Return every name ``.env.example`` assigns, with or without an ``export`` prefix."""
    return frozenset(ASSIGNMENT_PATTERN.findall(template_text))


def load_template(path: Path, errors: list[str]) -> frozenset[str]:
    """Return the names the template declares, recording a missing or unreadable file."""
    if not path.is_file():
        errors.append(f"missing environment template: {path}")
        return frozenset()
    text = _read(path, errors)
    return frozenset() if text is None else declared_names(text)


def parse_compose(path: str, text: str, errors: list[str]) -> ComposeFile | None:
    """Parse one compose document, recording every shape defect instead of raising."""
    try:
        loaded = cast("object", yaml.safe_load(text))
    except yaml.YAMLError as error:
        errors.append(f"{path}: invalid YAML: {error}")
        return None
    if not isinstance(loaded, Mapping):
        errors.append(f"{path}: the compose document must be a mapping")
        return None
    document = {str(key): value for key, value in loaded.items()}
    return ComposeFile(path, document, text) if _services_valid(path, document, errors) else None


def load_compose(path: Path, errors: list[str]) -> ComposeFile | None:
    """Read and parse one compose file."""
    text = _read(path, errors)
    return None if text is None else parse_compose(str(path), text, errors)


def load_dockerfile(path: Path, errors: list[str]) -> Dockerfile | None:
    """Read one Dockerfile."""
    text = _read(path, errors)
    return None if text is None else Dockerfile(str(path), text)


def _read(path: Path, errors: list[str]) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        errors.append(f"{path}: cannot read: {error}")
        return None


def _services_valid(path: str, document: Mapping[str, object], errors: list[str]) -> bool:
    services = document.get("services")
    if not isinstance(services, Mapping) or not all(isinstance(name, str) for name in services):
        errors.append(f"{path}: services must be a mapping of string names to mappings")
        return False
    malformed = [str(name) for name, spec in services.items() if not isinstance(spec, Mapping)]
    errors.extend(f"{path}: service {name!r} must be a mapping" for name in malformed)
    return not malformed


def evaluate(
    composes: Sequence[ComposeFile],
    dockerfiles: Sequence[Dockerfile],
    names: frozenset[str],
) -> list[str]:
    """Return every finding across the stack, sorted and unique."""
    issues: list[str] = []
    for compose in composes:
        issues.extend(evaluate_compose(compose, names))
    for dockerfile in dockerfiles:
        issues.extend(evaluate_dockerfile(dockerfile, names))
    issues.extend(_presence_issues(composes))
    issues.extend(_build_reference_issues(composes, dockerfiles))
    return sorted(set(issues))


def evaluate_compose(compose: ComposeFile, names: frozenset[str]) -> list[str]:
    """Return the findings for one compose document on its own."""
    issues = _interpolation_issues(compose.path, compose.text, names)
    if "include" in compose.document:
        issues.append(f"{compose.path}: top-level include is not permitted")
    issues.extend(_secret_declaration_issues(compose.document))
    for name, service in compose.services.items():
        for rule in SERVICE_RULES:
            issues.extend(rule(name, service))
    return issues


def _interpolation_issues(path: str, text: str, names: frozenset[str]) -> list[str]:
    return [
        f"{path}: ${{{variable}}} is not declared in .env.example"
        for variable in _references(text)
        if variable not in names
    ]


def _references(text: str) -> list[str]:
    return [match.group(1) or match.group(2) for match in INTERPOLATION_PATTERN.finditer(text)]


def _secret_declaration_issues(document: Mapping[str, object]) -> list[str]:
    secrets = document.get("secrets")
    if not isinstance(secrets, Mapping):
        return []
    return [
        f"secrets.{name} must declare a file under {COMPOSE_FILE_SOURCE_ROOT} "
        "or an environment source"
        for name, spec in secrets.items()
        if not _secret_source_valid(spec)
    ]


def _secret_source_valid(spec: object) -> bool:
    if not isinstance(spec, Mapping):
        return False
    file = spec.get("file")
    if isinstance(file, str):
        return file.startswith(COMPOSE_FILE_SOURCE_ROOT)
    return isinstance(spec.get("environment"), str)


def _image_issues(name: str, service: Mapping[str, object]) -> list[str]:
    image = service.get("image")
    if image is None and service.get("build") is None:
        return [f"services.{name} declares neither image nor build"]
    if not isinstance(image, str):
        return []
    if service.get("build") is None and PINNED_IMAGE_PATTERN.fullmatch(image) is None:
        return [f"services.{name}.image must be pinned as {PINNED_IMAGE_FORM}"]
    if _image_tag(image) == LATEST_TAG:
        return [f"services.{name}.image uses the floating tag {LATEST_TAG}"]
    return []


def _image_tag(image: str) -> str | None:
    reference = image.split("@", 1)[0]
    last = reference.rsplit("/", 1)[-1]
    return last.split(":", 1)[1] if ":" in last else None


def _port_issues(name: str, service: Mapping[str, object]) -> list[str]:
    return [
        f"services.{name}.ports[{index}] must be {PORT_FORM}"
        for index, entry in enumerate(_port_entries(service))
        if _container_port(entry) is None
    ]


def _port_entries(service: Mapping[str, object]) -> list[object]:
    ports = service.get("ports", [])
    return list(ports) if isinstance(ports, list) else [ports]


def _container_port(entry: object) -> int | None:
    if isinstance(entry, str):
        match = LOOPBACK_SHORT_PORT_PATTERN.fullmatch(entry)
        return int(match.group("container")) if match else None
    if isinstance(entry, Mapping):
        return _long_form_container_port(entry)
    return None


def _long_form_container_port(entry: Mapping[str, object]) -> int | None:
    published = entry.get("published")
    target = entry.get("target")
    if entry.get("host_ip") != LOOPBACK or not _is_port(published) or not _is_port(target):
        return None
    return int(str(target))


def _is_port(value: object) -> bool:
    if isinstance(value, bool):
        return False
    return isinstance(value, int) or (isinstance(value, str) and value.isdigit())


def _published_ports(service: Mapping[str, object]) -> set[int]:
    ports = (_container_port(entry) for entry in _port_entries(service))
    return {port for port in ports if port is not None}


def _network_issues(name: str, service: Mapping[str, object]) -> list[str]:
    if service.get("network_mode") == HOST_NETWORK:
        return [f"services.{name}.network_mode must not be {HOST_NETWORK}"]
    return []


def _environment_items(raw: object) -> list[tuple[str, object]]:
    if isinstance(raw, Mapping):
        return [(str(key), value) for key, value in raw.items()]
    if isinstance(raw, list):
        pairs = (str(item).partition("=") for item in raw)
        return [(key, value if separator else None) for key, separator, value in pairs]
    return []


def _secret_value_permitted(value: object) -> bool:
    if value is None or value == "":
        return True
    if not isinstance(value, str):
        return False
    indirected = INDIRECTION_PATTERN.fullmatch(value) is not None
    return indirected or value.startswith(CONTAINER_MOUNT_PREFIX)


def _literal_secret_issues(location: str, items: Iterable[tuple[str, object]]) -> list[str]:
    issues: list[str] = []
    for key, value in items:
        if SECRET_NAME_PATTERN.search(key) and not _secret_value_permitted(value):
            issues.append(
                f"{location}.{key} holds a literal secret; use ${{{key}}} indirection "
                f"or a path under {CONTAINER_MOUNT_PREFIX}"
            )
        if isinstance(value, str) and URL_USERINFO_PATTERN.search(value):
            issues.append(f"{location}.{key} embeds credentials in a URL")
    return issues


def _environment_issues(name: str, service: Mapping[str, object]) -> list[str]:
    items = _environment_items(service.get("environment"))
    return _literal_secret_issues(f"services.{name}.environment", items)


def _build_args_issues(name: str, service: Mapping[str, object]) -> list[str]:
    build = service.get("build")
    if not isinstance(build, Mapping):
        return []
    items = _environment_items(build.get("args"))
    return _literal_secret_issues(f"services.{name}.build.args", items)


def _healthcheck_issues(name: str, service: Mapping[str, object]) -> list[str]:
    if name in ONE_SHOT_SERVICES:
        return []
    healthcheck = service.get("healthcheck")
    if (
        isinstance(healthcheck, Mapping)
        and "test" in healthcheck
        and healthcheck.get("disable") is not True
    ):
        return []
    return [f"services.{name} lacks a healthcheck.test"]


def _one_shot_issues(name: str, service: Mapping[str, object]) -> list[str]:
    if name not in ONE_SHOT_SERVICES:
        return []
    issues: list[str] = []
    if service.get("restart") != "no":
        issues.append(f'services.{name}.restart must be "no" for a one-shot service')
    if "healthcheck" in service:
        issues.append(f"services.{name} is one-shot and must not declare a healthcheck")
    return issues


def _platform_issues(name: str, service: Mapping[str, object]) -> list[str]:
    platform = service.get("platform")
    expected = PLATFORM_ALLOWLIST.get(name)
    if expected is None:
        allowed = ", ".join(sorted(PLATFORM_ALLOWLIST))
        return (
            [] if platform is None else [f"services.{name}.platform may only be set on: {allowed}"]
        )
    return [] if platform == expected else [f"services.{name}.platform must be {expected}"]


def _profile_issues(name: str, service: Mapping[str, object]) -> list[str]:
    profiles = service.get("profiles", [])
    entries = list(profiles) if isinstance(profiles, list) else [profiles]
    known = ", ".join(sorted(KNOWN_PROFILES))
    return [
        f"services.{name}.profiles[{index}] is not a known profile (known: {known})"
        for index, profile in enumerate(entries)
        if profile not in KNOWN_PROFILES
    ]


def _indirection_issues(name: str, service: Mapping[str, object]) -> list[str]:
    issues: list[str] = []
    if "extends" in service:
        issues.append(f"services.{name}.extends is not permitted")
    build = service.get("build")
    if isinstance(build, Mapping) and "dockerfile_inline" in build:
        issues.append(f"services.{name}.build.dockerfile_inline is not permitted")
    return issues


def _broker_issues(name: str, service: Mapping[str, object]) -> list[str]:
    if name != BROKER_SERVICE:
        return []
    issues: list[str] = []
    prefix = f"services.{BROKER_SERVICE}"
    if "shm_size" not in service:
        issues.append(f"{prefix}.shm_size is required")
    if not _nofile_limits_declared(service.get("ulimits")):
        issues.append(f"{prefix}.ulimits.nofile must declare soft and hard limits")
    environment = dict(_environment_items(service.get("environment")))
    if "tls_servercertificate_filepath" not in environment:
        issues.append(f"{prefix}.environment must set tls_servercertificate_filepath")
    issues.extend(_broker_port_issues(prefix, _published_ports(service)))
    return issues


def _nofile_limits_declared(ulimits: object) -> bool:
    if not isinstance(ulimits, Mapping):
        return False
    nofile = ulimits.get("nofile")
    return isinstance(nofile, Mapping) and "soft" in nofile and "hard" in nofile


def _broker_port_issues(prefix: str, published: set[int]) -> list[str]:
    issues: list[str] = []
    if BROKER_TLS_SMF_PORT not in published:
        issues.append(
            f"{prefix}.ports must publish container port {BROKER_TLS_SMF_PORT} (SMF over TLS)"
        )
    issues.extend(
        f"{prefix}.ports must not publish container port {port} (plaintext SMF or SEMP)"
        for port in sorted(BROKER_FORBIDDEN_PORTS & published)
    )
    return issues


def _agent_mesh_issues(name: str, service: Mapping[str, object]) -> list[str]:
    if name != AGENT_MESH_SERVICE:
        return []
    issues: list[str] = []
    prefix = f"services.{AGENT_MESH_SERVICE}.environment"
    environment = dict(_environment_items(service.get("environment")))
    if not _dev_mode_off(environment.get("SOLACE_DEV_MODE")):
        issues.append(f"{prefix}.SOLACE_DEV_MODE must be explicitly {DEV_MODE_OFF}")
    session = environment.get("SESSION_SECRET_KEY")
    if not isinstance(session, str) or INDIRECTION_PATTERN.fullmatch(session) is None:
        issues.append(
            f"{prefix} must set SESSION_SECRET_KEY by indirection; "
            "the image default is a placeholder"
        )
    return issues


def _dev_mode_off(value: object) -> bool:
    if value is False:
        return True
    return isinstance(value, str) and value.strip().lower() == DEV_MODE_OFF


SERVICE_RULES: Final[tuple[ServiceRule, ...]] = (
    _image_issues,
    _port_issues,
    _network_issues,
    _environment_issues,
    _build_args_issues,
    _healthcheck_issues,
    _one_shot_issues,
    _platform_issues,
    _profile_issues,
    _indirection_issues,
    _broker_issues,
    _agent_mesh_issues,
)
"""Every per-service rule; role rules return nothing for services they do not govern."""


def _presence_issues(composes: Sequence[ComposeFile]) -> list[str]:
    present = {name for compose in composes for name in compose.services}
    issues: list[str] = []
    if BROKER_SERVICE not in present:
        issues.append(f'services must include the broker service "{BROKER_SERVICE}"')
    if AGENT_MESH_SERVICE not in present:
        issues.append(f'services must include the Agent Mesh service "{AGENT_MESH_SERVICE}"')
    return issues


def _build_reference_issues(
    composes: Sequence[ComposeFile],
    dockerfiles: Sequence[Dockerfile],
) -> list[str]:
    reviewed = {posixpath.normpath(dockerfile.path) for dockerfile in dockerfiles}
    built: set[str] = set()
    issues: list[str] = []
    for compose in composes:
        for name, service in compose.services.items():
            build = service.get("build")
            if build is None:
                continue
            expected = _build_dockerfile_path(compose.path, build)
            built.add(expected)
            if expected not in reviewed:
                issues.append(f"services.{name}.build names {expected}, which is not under review")
    issues.extend(
        f"{path} is not built by any compose service" for path in sorted(reviewed - built)
    )
    return issues


def _build_dockerfile_path(compose_path: str, build: object) -> str:
    context = build if isinstance(build, str) else "."
    dockerfile = "Dockerfile"
    if isinstance(build, Mapping):
        context = str(build.get("context", "."))
        dockerfile = str(build.get("dockerfile", "Dockerfile"))
    return posixpath.normpath(posixpath.join(posixpath.dirname(compose_path), context, dockerfile))


def evaluate_dockerfile(dockerfile: Dockerfile, names: frozenset[str]) -> list[str]:
    """Return the findings for one Dockerfile."""
    issues: list[str] = []
    stages: set[str] = set()
    declared = set(names) | BUILDKIT_PREDECLARED_ARGS
    for line, keyword, rest in dockerfile_instructions(dockerfile.text):
        if keyword == FROM_INSTRUCTION:
            issues.extend(_from_issues(dockerfile.path, line, rest, stages))
        elif keyword == RUN_INSTRUCTION:
            issues.extend(_run_issues(dockerfile.path, line, rest))
        if keyword in DECLARING_INSTRUCTIONS:
            declared.update(_declared_by(rest))
        if keyword in SUBSTITUTED_INSTRUCTIONS:
            issues.extend(_variable_issues(dockerfile.path, line, rest, declared))
    return issues


def dockerfile_instructions(text: str) -> list[Instruction]:
    """Return each Dockerfile instruction as (first line, upper-case keyword, arguments)."""
    instructions: list[Instruction] = []
    buffer: list[str] = []
    start = 0
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not buffer:
            if not line or line.startswith("#"):
                continue
            start = number
        if line.endswith("\\"):
            buffer.append(line[:-1].strip())
            continue
        buffer.append(line)
        keyword, _, rest = " ".join(buffer).partition(" ")
        buffer = []
        instructions.append((start, keyword.upper(), rest.strip()))
    return instructions


def _from_issues(path: str, line: int, rest: str, stages: set[str]) -> list[str]:
    tokens = rest.split()
    issues: list[str] = []
    if any(token.startswith("--platform") for token in tokens):
        issues.append(
            f"{path}:{line}: FROM must not carry --platform; the compose service declares it"
        )
    operands = [token for token in tokens if not token.startswith("--")]
    image = operands[0] if operands else ""
    uppercased = [token.upper() for token in operands]
    if STAGE_KEYWORD in uppercased and uppercased.index(STAGE_KEYWORD) + 1 < len(operands):
        stages.add(operands[uppercased.index(STAGE_KEYWORD) + 1])
    if image in stages:
        return issues
    match = PINNED_IMAGE_PATTERN.fullmatch(image)
    if match is None:
        issues.append(
            f"{path}:{line}: FROM must be pinned as {PINNED_IMAGE_FORM} or name an earlier stage"
        )
    elif match.group(1) == LATEST_TAG:
        issues.append(f"{path}:{line}: FROM uses the floating tag {LATEST_TAG}")
    return issues


def _run_issues(path: str, line: int, rest: str) -> list[str]:
    return [
        f"{path}:{line}: pip install must pass --require-hashes"
        for segment in re.split(r"&&|\|\||;", rest)
        if PIP_INSTALL_PATTERN.search(segment) and "--require-hashes" not in segment
    ]


def _declared_by(rest: str) -> set[str]:
    names = set(DECLARATION_PATTERN.findall(rest))
    if not names and rest:
        names.add(rest.split(maxsplit=1)[0])
    return names


def _variable_issues(path: str, line: int, rest: str, declared: set[str]) -> list[str]:
    return [
        f"{path}:{line}: ${{{variable}}} is not declared by ARG, ENV, or .env.example"
        for variable in _references(rest)
        if variable not in declared
    ]


def _parse_arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="compose-policy-gate",
        description="Hold the deploy/ compose stack to the policy in docs/adr/0044 and 0045.",
    )
    parser.add_argument("--env-template", required=True, type=Path)
    parser.add_argument("--compose", action="append", default=[], type=Path)
    parser.add_argument("--dockerfile", action="append", default=[], type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Print diagnostics and return a blocking status when the stack violates the policy."""
    arguments = _parse_arguments(argv)
    errors: list[str] = []
    names = load_template(arguments.env_template, errors)
    compose_paths = cast("list[Path]", arguments.compose)
    dockerfile_paths = cast("list[Path]", arguments.dockerfile)
    composes = [compose for path in compose_paths if (compose := load_compose(path, errors))]
    dockerfiles = [file for path in dockerfile_paths if (file := load_dockerfile(path, errors))]
    if not compose_paths:
        errors.append(NO_COMPOSE)
    issues = sorted(set(errors + evaluate(composes, dockerfiles, names)))
    for issue in issues:
        print(f"COMPOSE: {issue}", file=sys.stderr)
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
