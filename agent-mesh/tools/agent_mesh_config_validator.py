"""Offline semantic validation for pinned Solace Agent Mesh configuration."""

from __future__ import annotations

import contextlib
import importlib
import importlib.metadata
import io
import os
import re
import sys
import tempfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from importlib.machinery import PathFinder
from pathlib import Path
from typing import TextIO, cast
from urllib.parse import urlsplit

EXPECTED_VERSIONS: Mapping[str, str] = {
    "solace-agent-mesh": "1.28.7",
    "solace-ai-connector": "3.3.12",
    "sam-event-mesh-gateway": "1.1.0",
    "sam-event-mesh-tool": "0.1.1",
}
AGENT_MODULE = "solace_agent_mesh.agent.sac.app"
WORKFLOW_MODULE = "solace_agent_mesh.workflow.app"
GATEWAY_MODULE = "sam_event_mesh_gateway.app"
TOOL_MODULE = "sam_event_mesh_tool.tools"
TOOL_CLASS = "EventMeshTool"
ALTERNATE_TOOL_LOADER_FIELDS = (
    "component_base_path",
    "function_name",
    "init_function",
    "cleanup_function",
)
SUPPORTED_MODULES = frozenset((AGENT_MODULE, WORKFLOW_MODULE, GATEWAY_MODULE))
BROKER_FIELDS = ("broker_url", "broker_username", "broker_password", "broker_vpn")
ENV_REFERENCE_FIELD_NAMES = frozenset(BROKER_FIELDS)
INCLUDE_PATTERN = re.compile(
    r'^[ \t]*!include\s+(["\']?[^"\s\']+)["\']?',
    re.MULTILINE,
)
ENV_PATTERN = re.compile(r"\$\{([^}:\s]+)(?:\s*,\s*[^}]*)?\}")
SECRET_NAME_PATTERN = re.compile(
    r"(^|_)(API_?KEY|ACCESS_TOKEN|REFRESH_TOKEN|BEARER_TOKEN|TOKEN|SECRET|"
    r"PASSWORD|PASSWD|CLIENT_SECRET|PRIVATE_KEY|SIGNING_KEY|AUTHORIZATION|"
    r"SIGNATURE|SALT)(_|$)",
    re.IGNORECASE,
)
WHOLE_ENV_PATTERN = re.compile(r'^["\']?\$\{[A-Za-z_][A-Za-z0-9_]*\}["\']?$')
URL_USERINFO_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^/@\s]+@")
SAFE_TOOL_TOPIC_PATTERN = re.compile(
    r"^aerial-rescue/v1/\{\{\s*missionId\s*\}\}/gateway/request/"
    r"\{\{\s*operation\s*\}\}$"
)
PINNED_MODEL_PATTERN = re.compile(
    r"(?:[-:@](?:v?\d+\.\d+(?:\.\d+)?|\d{4}-\d{2}-\d{2}|\d{8})|"
    r"@sha256:[0-9a-f]{64})$",
    re.IGNORECASE,
)
TOPIC_IDENTIFIER_PARAMETERS = frozenset(("missionId", "operation"))
TOPIC_IDENTIFIER_FORBIDDEN_PATTERN = re.compile(r"[/+*#>]")
GATEWAY_TARGET_FIELDS = (
    "target_agent_name",
    "target_agent_name_expression",
    "target_workflow_name",
    "target_workflow_name_expression",
)
MINIMUM_CONFIGS_TO_MERGE = 2
YAML_MAPPING_ENTRY_SIZE = 2


@dataclass(frozen=True, order=True)
class ValidationIssue:
    """One deterministic, value-redacted configuration diagnostic."""

    path: Path
    location: str
    rule: str
    message: str


@dataclass(frozen=True)
class ValidationResult:
    """The validation outcome for one configuration file."""

    path: Path
    app_count: int
    issues: tuple[ValidationIssue, ...]

    @property
    def valid(self) -> bool:
        """Return whether the file has no validation issues."""
        return not self.issues


@dataclass(frozen=True)
class _Runtime:
    """Typed references to the exact upstream validation primitives."""

    load_config: Callable[[str], object]
    merge_config: Callable[[object, object], object]
    process_includes: Callable[[str, str], str]
    compose_yaml: Callable[[str], object]
    agent_model: object
    workflow_model: object
    gateway_schema: tuple[dict[str, object], ...]


class _RuntimeBoundaryError(RuntimeError):
    """The exact pinned upstream validation boundary is unavailable."""

    def __init__(self) -> None:
        super().__init__("the exact pinned upstream validation boundary is unavailable")


class _EnvironmentTemplateError(ValueError):
    """The tracked environment template cannot safely seed validation."""

    def __init__(self) -> None:
        super().__init__("the tracked environment template is invalid")


def _issue(path: Path, location: str, rule: str, message: str) -> ValidationIssue:
    return ValidationIssue(path, location, rule, message)


def _read_environment_template(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.removeprefix("export ").partition("=")
        if not separator or not name.isidentifier():
            raise _EnvironmentTemplateError
        placeholder = not value or value.startswith("<") or value == f"${{{name}}}"
        values[name] = "offline-placeholder" if placeholder else value
    return values


@contextlib.contextmanager
def _isolated_runtime_environment(values: Mapping[str, str]) -> Iterator[None]:
    previous_environment = os.environ.copy()
    previous_directory = Path.cwd()
    with tempfile.TemporaryDirectory(prefix="arm-agent-mesh-validation-") as temporary:
        try:
            os.environ.clear()
            os.environ.update(values)
            os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
            os.environ["HOME"] = temporary
            os.environ["PATH"] = os.defpath
            os.environ["TMPDIR"] = temporary
            os.environ["XDG_CACHE_HOME"] = temporary
            os.chdir(temporary)
            yield
        finally:
            os.chdir(previous_directory)
            os.environ.clear()
            os.environ.update(previous_environment)


def _attribute(module_name: str, attribute_name: str) -> object:
    try:
        module = importlib.import_module(module_name)
    except Exception as error:
        raise _RuntimeBoundaryError from error
    if not hasattr(module, attribute_name):
        raise _RuntimeBoundaryError
    return getattr(module, attribute_name)


def _distribution_files(distribution_name: str) -> frozenset[Path]:
    try:
        distribution = importlib.metadata.distribution(distribution_name)
    except Exception as error:
        raise _RuntimeBoundaryError from error
    files = distribution.files
    if files is None:
        raise _RuntimeBoundaryError
    try:
        return frozenset(Path(str(distribution.locate_file(file))).resolve() for file in files)
    except Exception as error:
        raise _RuntimeBoundaryError from error


def _distribution_attribute(
    distribution_name: str,
    module_name: str,
    attribute_name: str,
) -> object:
    installed_files = _distribution_files(distribution_name)
    top_level_name = module_name.partition(".")[0]
    top_level_spec = PathFinder.find_spec(top_level_name, sys.path)
    top_level_origin = getattr(top_level_spec, "origin", None)
    if (
        not isinstance(top_level_origin, str)
        or Path(top_level_origin).resolve() not in installed_files
    ):
        raise _RuntimeBoundaryError
    value = _attribute(module_name, attribute_name)
    module = sys.modules.get(module_name)
    module_file = getattr(module, "__file__", None)
    if not isinstance(module_file, str) or Path(module_file).resolve() not in installed_files:
        raise _RuntimeBoundaryError
    return value


def _gateway_schema() -> tuple[dict[str, object], ...]:
    gateway_class = _distribution_attribute(
        "sam-event-mesh-gateway",
        GATEWAY_MODULE,
        "EventMeshGatewayApp",
    )
    raw_schema = getattr(gateway_class, "app_schema", None)
    if not isinstance(raw_schema, dict):
        raise _RuntimeBoundaryError
    parameters = raw_schema.get("config_parameters")
    if not isinstance(parameters, list) or not all(isinstance(item, dict) for item in parameters):
        raise _RuntimeBoundaryError
    return tuple(cast(dict[str, object], item) for item in parameters)


def _verify_gateway_entry_point() -> None:
    try:
        entries = importlib.metadata.entry_points(group="solace_agent_mesh.plugins")
    except Exception as error:
        raise _RuntimeBoundaryError from error
    matching = tuple(entry for entry in entries if entry.name == "sam_event_mesh_gateway")
    if len(matching) != 1 or matching[0].value != "sam_event_mesh_gateway.app:info":
        raise _RuntimeBoundaryError
    installed_info = _distribution_attribute(
        "sam-event-mesh-gateway",
        GATEWAY_MODULE,
        "info",
    )
    try:
        info = matching[0].load()
    except Exception as error:
        raise _RuntimeBoundaryError from error
    if (
        info is not installed_info
        or not isinstance(info, dict)
        or info.get("class_name") != "EventMeshGatewayApp"
    ):
        raise _RuntimeBoundaryError


def _load_runtime() -> _Runtime:
    for distribution, expected in EXPECTED_VERSIONS.items():
        try:
            installed = importlib.metadata.version(distribution)
        except Exception as error:
            raise _RuntimeBoundaryError from error
        if installed != expected:
            raise _RuntimeBoundaryError
    _verify_gateway_entry_point()
    tool_class = _distribution_attribute("sam-event-mesh-tool", TOOL_MODULE, TOOL_CLASS)
    load_config = _distribution_attribute(
        "solace-ai-connector",
        "solace_ai_connector.main",
        "load_config",
    )
    merge_config = _distribution_attribute(
        "solace-ai-connector",
        "solace_ai_connector.main",
        "merge_config",
    )
    process_includes = _distribution_attribute(
        "solace-ai-connector",
        "solace_ai_connector.main",
        "process_includes",
    )
    compose_yaml = _distribution_attribute("PyYAML", "yaml", "compose")
    if (
        not isinstance(tool_class, type)
        or not callable(load_config)
        or not callable(merge_config)
        or not callable(process_includes)
        or not callable(compose_yaml)
    ):
        raise _RuntimeBoundaryError
    return _Runtime(
        load_config=cast(Callable[[str], object], load_config),
        merge_config=cast(Callable[[object, object], object], merge_config),
        process_includes=cast(Callable[[str, str], str], process_includes),
        compose_yaml=cast(Callable[[str], object], compose_yaml),
        agent_model=_distribution_attribute(
            "solace-agent-mesh",
            AGENT_MODULE,
            "SamAgentAppConfig",
        ),
        workflow_model=_distribution_attribute(
            "solace-agent-mesh",
            WORKFLOW_MODULE,
            "WorkflowAppConfig",
        ),
        gateway_schema=_gateway_schema(),
    )


def _contained(path: Path, root: Path) -> bool:
    return path == root or path.is_relative_to(root)


def _read_include_tree(
    path: Path,
    *,
    root: Path,
    stack: tuple[Path, ...] = (),
) -> tuple[tuple[str, ...], tuple[ValidationIssue, ...]]:
    resolved = path.resolve()
    if not _contained(resolved, root):
        issue = _issue(path, "include", "INCLUDE_ESCAPE", "escaping include denied")
        return (), (issue,)
    if resolved in stack:
        return (), (_issue(path, "include", "INCLUDE_CYCLE", "include cycle detected"),)
    try:
        source = resolved.read_text(encoding="utf-8")
    except OSError:
        return (), (_issue(path, "include", "INCLUDE_MISSING", "include is not readable"),)
    sources = [source]
    issues: list[ValidationIssue] = []
    for match in INCLUDE_PATTERN.finditer(source):
        raw_include = match.group(1).strip("'\"")
        include = Path(raw_include)
        if include.is_absolute():
            issues.append(_issue(path, "include", "INCLUDE_ABSOLUTE", "absolute include denied"))
            continue
        target = (resolved.parent / include).resolve()
        if not _contained(target, root):
            issues.append(_issue(path, "include", "INCLUDE_ESCAPE", "escaping include denied"))
            continue
        nested_sources, nested_issues = _read_include_tree(
            target,
            root=root,
            stack=(*stack, resolved),
        )
        sources.extend(nested_sources)
        issues.extend(nested_issues)
    return tuple(sources), tuple(issues)


def _whole_environment_node(node: object) -> bool:
    value = getattr(node, "value", None)
    return (
        getattr(node, "id", None) == "scalar"
        and isinstance(value, str)
        and WHOLE_ENV_PATTERN.fullmatch(value) is not None
    )


def _environment_reference_required(name: str) -> bool:
    return (
        name.casefold() in ENV_REFERENCE_FIELD_NAMES or SECRET_NAME_PATTERN.search(name) is not None
    )


def _blank_scalar_node(node: object) -> bool:
    value = getattr(node, "value", None)
    return getattr(node, "id", None) == "scalar" and (
        value is None or (isinstance(value, str) and not value.strip())
    )


def _secret_mapping_entry_issues(
    path: Path,
    entry: object,
    location: str,
    seen: set[int],
) -> tuple[ValidationIssue, ...]:
    if not isinstance(entry, tuple) or len(entry) != YAML_MAPPING_ENTRY_SIZE:
        return ()
    key_node, child = entry
    key = getattr(key_node, "value", None)
    child_location = f"{location}.{key}" if isinstance(key, str) else location
    issues: list[ValidationIssue] = []
    if (
        isinstance(key, str)
        and _environment_reference_required(key)
        and getattr(child, "id", None) == "scalar"
        and not _blank_scalar_node(child)
        and not _whole_environment_node(child)
    ):
        issues.append(
            _issue(
                path,
                child_location,
                "SECRET_LITERAL",
                "secret must use environment indirection",
            )
        )
    issues.extend(_secret_node_issues(path, child, child_location, seen))
    return tuple(issues)


def _secret_node_issues(
    path: Path,
    node: object,
    location: str = "configuration",
    seen: set[int] | None = None,
) -> tuple[ValidationIssue, ...]:
    visited = set() if seen is None else seen
    identity = id(node)
    if identity in visited:
        return ()
    visited.add(identity)
    kind = getattr(node, "id", None)
    value = getattr(node, "value", None)
    issues: list[ValidationIssue] = []
    if kind == "mapping" and isinstance(value, list):
        for entry in value:
            issues.extend(_secret_mapping_entry_issues(path, entry, location, visited))
    elif kind == "sequence" and isinstance(value, list):
        for index, child in enumerate(value):
            issues.extend(_secret_node_issues(path, child, f"{location}[{index}]", visited))
    return tuple(issues)


def _source_policy_issues(
    path: Path,
    source: str,
    declared_environment: frozenset[str],
    compose_yaml: Callable[[str], object],
) -> tuple[ValidationIssue, ...]:
    issues: list[ValidationIssue] = []
    references = frozenset(ENV_PATTERN.findall(source))
    if references - declared_environment:
        issues.append(
            _issue(path, "environment", "ENV_UNDECLARED", "undeclared environment reference")
        )
    if URL_USERINFO_PATTERN.search(source):
        issues.append(_issue(path, "configuration", "URL_USERINFO", "URL userinfo is forbidden"))
    try:
        root = compose_yaml(source)
    except Exception:
        return tuple(issues)
    if root is not None:
        issues.extend(_secret_node_issues(path, root))
    return tuple(issues)


def _expanded_source(runtime: _Runtime, path: Path) -> str | None:
    resolved = path.resolve()
    try:
        return runtime.process_includes(str(resolved), str(resolved.parent))
    except Exception:
        return None


def _parsed_config(runtime: _Runtime, path: Path) -> object | None:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        try:
            return runtime.load_config(str(path.resolve()))
        except (SystemExit, Exception):
            return None


def _model_fields(model: object) -> frozenset[str]:
    fields = getattr(model, "model_fields", None)
    if not isinstance(fields, dict):
        raise _RuntimeBoundaryError
    return frozenset(str(name) for name in fields)


def _invoke_model(model: object, value: Mapping[str, object]) -> bool:
    validator = getattr(model, "model_validate_and_clean", None)
    if not callable(validator):
        raise _RuntimeBoundaryError
    try:
        validator(dict(value))
    except Exception:
        return False
    return True


def _broker_issues(path: Path, broker: object, location: str) -> tuple[ValidationIssue, ...]:
    if not isinstance(broker, dict):
        return (_issue(path, location, "BROKER_CONFIG", "broker configuration must be a mapping"),)
    missing = tuple(
        field
        for field in BROKER_FIELDS
        if not isinstance(broker.get(field), str) or not broker[field].strip()
    )
    issues = [
        _issue(path, f"{location}.{field}", "BROKER_CONFIG", "required broker field is blank")
        for field in missing
    ]
    broker_url = broker.get("broker_url")
    if isinstance(broker_url, str) and broker_url.strip():
        try:
            parsed_url = urlsplit(broker_url)
            secure_transport = parsed_url.scheme.casefold() == "tcps" or (
                parsed_url.scheme.casefold() == "wss" and parsed_url.port in (None, 443)
            )
            no_userinfo = parsed_url.username is None and parsed_url.password is None
            valid_host = parsed_url.hostname is not None
        except ValueError:
            secure_transport = no_userinfo = valid_host = False
        if not secure_transport or not no_userinfo or not valid_host:
            issues.append(
                _issue(
                    path,
                    f"{location}.broker_url",
                    "BROKER_TRANSPORT",
                    "broker URL must use tcps or WSS on port 443 without userinfo",
                )
            )
    return tuple(issues)


def _model_identifier(model: object) -> str | None:
    if isinstance(model, str):
        return model.strip()
    if isinstance(model, dict) and isinstance(model.get("model"), str):
        return cast(str, model["model"]).strip()
    return None


def _model_policy_issues(
    path: Path,
    app_config: Mapping[str, object],
    location: str,
    *,
    required: bool,
) -> tuple[ValidationIssue, ...]:
    issues: list[ValidationIssue] = []
    if "model_provider" in app_config:
        issues.append(
            _issue(
                path, f"{location}.model_provider", "MODEL_PROVIDER", "model_provider is forbidden"
            )
        )
    identifier = _model_identifier(app_config.get("model"))
    if not identifier:
        if required or app_config.get("model") is not None:
            issues.append(
                _issue(
                    path,
                    f"{location}.model",
                    "MODEL_IDENTIFIER",
                    "model identifier must be nonblank",
                )
            )
        return tuple(issues)
    if identifier.casefold().startswith("ollama"):
        issues.append(
            _issue(
                path,
                f"{location}.model",
                "MODEL_LOCK_REQUIRED",
                "local model lock is not yet defined",
            )
        )
    elif not PINNED_MODEL_PATTERN.search(identifier):
        issues.append(
            _issue(
                path,
                f"{location}.model",
                "MODEL_FLOATING",
                "floating model identifier is forbidden",
            )
        )
    return tuple(issues)


def _model_issues(
    path: Path,
    app_config: Mapping[str, object],
    model: object,
    location: str,
    *,
    model_required: bool,
) -> tuple[ValidationIssue, ...]:
    unknown = sorted(set(app_config) - _model_fields(model))
    issues = [
        _issue(path, f"{location}.{name}", "APP_CONFIG_UNKNOWN", "unknown app configuration field")
        for name in unknown
    ]
    issues.extend(_model_policy_issues(path, app_config, location, required=model_required))
    if not _invoke_model(model, app_config):
        issues.append(_issue(path, location, "APP_CONFIG", "upstream app configuration rejected"))
    return tuple(issues)


def _matches_schema_type(value: object, expected: object) -> bool:
    predicates: Mapping[str, Callable[[object], bool]] = {
        "object": lambda item: isinstance(item, dict),
        "list": lambda item: isinstance(item, list),
        "string": lambda item: isinstance(item, str),
        "integer": lambda item: type(item) is int,
        "number": lambda item: type(item) in (int, float),
        "boolean": lambda item: type(item) is bool,
    }
    return (
        not isinstance(expected, str) or expected not in predicates or predicates[expected](value)
    )


def _gateway_shape_issue(
    path: Path,
    value: object,
    descriptor: Mapping[str, object],
    location: str,
) -> ValidationIssue | None:
    wrong_type = not _matches_schema_type(value, descriptor.get("type"))
    blank_required_string = (
        descriptor.get("required") is True
        and descriptor.get("type") == "string"
        and isinstance(value, str)
        and not value.strip()
    )
    if not wrong_type and not blank_required_string:
        return None
    message = (
        "required gateway string is blank"
        if blank_required_string
        else "gateway value has the wrong type"
    )
    return _issue(path, location, "GATEWAY_SCHEMA", message)


def _nested_schema_issues(
    path: Path,
    value: object,
    descriptor: Mapping[str, object],
    location: str,
) -> tuple[ValidationIssue, ...]:
    properties = descriptor.get("properties")
    if isinstance(value, dict) and isinstance(properties, dict):
        descriptors = tuple(
            {"name": name, **cast(dict[str, object], raw_descriptor)}
            for name, raw_descriptor in properties.items()
            if isinstance(raw_descriptor, dict)
        )
        return _schema_mapping_issues(path, value, descriptors, location)
    item_schema = descriptor.get("items")
    if isinstance(value, list) and isinstance(item_schema, dict):
        return tuple(
            issue
            for index, item in enumerate(value)
            for issue in _schema_value_issues(path, item, item_schema, f"{location}[{index}]")
        )
    return ()


def _schema_value_issues(
    path: Path,
    value: object,
    descriptor: Mapping[str, object],
    location: str,
) -> tuple[ValidationIssue, ...]:
    if value is None and descriptor.get("required") is not True:
        return ()
    shape_issue = _gateway_shape_issue(path, value, descriptor, location)
    if shape_issue is not None:
        return (shape_issue,)
    enum = descriptor.get("enum")
    if isinstance(enum, list) and value not in enum:
        return (
            _issue(path, location, "GATEWAY_SCHEMA", "gateway value is outside its enumeration"),
        )
    return _nested_schema_issues(path, value, descriptor, location)


def _schema_mapping_issues(
    path: Path,
    values: Mapping[str, object],
    descriptors: Sequence[object],
    location: str,
) -> tuple[ValidationIssue, ...]:
    issues: list[ValidationIssue] = []
    for raw_descriptor in descriptors:
        if not isinstance(raw_descriptor, dict) or not isinstance(raw_descriptor.get("name"), str):
            continue
        descriptor = cast(dict[str, object], raw_descriptor)
        name = cast(str, descriptor["name"])
        child_location = f"{location}.{name}"
        if name not in values:
            if descriptor.get("required") is True:
                issues.append(
                    _issue(
                        path, child_location, "GATEWAY_SCHEMA", "required gateway field is missing"
                    )
                )
            continue
        issues.extend(_schema_value_issues(path, values[name], descriptor, child_location))
    return tuple(issues)


def _safe_acknowledgment_policy(default: object, override: object) -> bool:
    if not isinstance(default, dict) or not isinstance(override, dict):
        return False
    default_failure = default.get("on_failure")
    override_failure = override.get("on_failure", {})
    if not isinstance(default_failure, dict) or not isinstance(override_failure, dict):
        return False
    return (
        override.get("mode", default.get("mode")) == "on_completion"
        and override_failure.get("action", default_failure.get("action")) == "nack"
        and override_failure.get("nack_outcome", default_failure.get("nack_outcome")) == "rejected"
    )


def _gateway_acknowledgment_issues(
    path: Path,
    app_config: Mapping[str, object],
    location: str,
) -> tuple[ValidationIssue, ...]:
    acknowledgment = app_config.get("acknowledgment_policy")
    issues: list[ValidationIssue] = []
    if not _safe_acknowledgment_policy(acknowledgment, {}):
        issues.append(
            _issue(
                path,
                f"{location}.acknowledgment_policy",
                "GATEWAY_POLICY",
                "gateway must use explicit deferred rejection for redelivery",
            )
        )
    handlers = app_config.get("event_handlers")
    if not isinstance(handlers, list):
        return tuple(issues)
    for index, handler in enumerate(handlers):
        if (
            isinstance(handler, dict)
            and "acknowledgment_policy" in handler
            and not _safe_acknowledgment_policy(
                acknowledgment,
                handler.get("acknowledgment_policy"),
            )
        ):
            issues.append(
                _issue(
                    path,
                    f"{location}.event_handlers[{index}].acknowledgment_policy",
                    "GATEWAY_POLICY",
                    "handler override must preserve deferred rejection",
                )
            )
    return tuple(issues)


def _gateway_handler_entry_issues(
    path: Path,
    handler: object,
    available_outputs: frozenset[object],
    location: str,
) -> tuple[ValidationIssue, ...]:
    if not isinstance(handler, dict):
        return ()
    issues: list[ValidationIssue] = []
    targets = tuple(
        field
        for field in GATEWAY_TARGET_FIELDS
        if isinstance(handler.get(field), str) and cast(str, handler[field]).strip()
    )
    if len(targets) != 1:
        issues.append(
            _issue(
                path,
                location,
                "GATEWAY_POLICY",
                "gateway handler must declare exactly one nonblank target",
            )
        )
    for reference_field in ("on_success", "on_error"):
        reference = handler.get(reference_field)
        if reference is not None and reference not in available_outputs:
            issues.append(
                _issue(
                    path,
                    f"{location}.{reference_field}",
                    "GATEWAY_POLICY",
                    "gateway output handler reference is unavailable",
                )
            )
    return tuple(issues)


def _gateway_handler_policy_issues(
    path: Path,
    app_config: Mapping[str, object],
    location: str,
) -> tuple[ValidationIssue, ...]:
    handlers = app_config.get("event_handlers")
    outputs = app_config.get("output_handlers", [])
    if not isinstance(handlers, list) or not isinstance(outputs, list):
        return ()
    handler_names = tuple(
        handler.get("name")
        for handler in handlers
        if isinstance(handler, dict) and isinstance(handler.get("name"), str)
    )
    output_names = tuple(
        output.get("name")
        for output in outputs
        if isinstance(output, dict) and isinstance(output.get("name"), str)
    )
    issues: list[ValidationIssue] = []
    if len(handler_names) != len(set(handler_names)):
        issues.append(
            _issue(
                path,
                f"{location}.event_handlers.name",
                "GATEWAY_POLICY",
                "gateway event handler names must be unique",
            )
        )
    if len(output_names) != len(set(output_names)):
        issues.append(
            _issue(
                path,
                f"{location}.output_handlers.name",
                "GATEWAY_POLICY",
                "gateway output handler names must be unique",
            )
        )
    available_outputs = frozenset(output_names)
    issues.extend(
        issue
        for index, handler in enumerate(handlers)
        for issue in _gateway_handler_entry_issues(
            path,
            handler,
            available_outputs,
            f"{location}.event_handlers[{index}]",
        )
    )
    return tuple(issues)


def _gateway_issues(
    path: Path,
    app_config: Mapping[str, object],
    runtime: _Runtime,
    location: str,
) -> tuple[ValidationIssue, ...]:
    issues = list(_schema_mapping_issues(path, app_config, runtime.gateway_schema, location))
    broker_location = f"{location}.event_mesh_broker_config"
    issues.extend(_broker_issues(path, app_config.get("event_mesh_broker_config"), broker_location))
    handlers = app_config.get("event_handlers")
    if isinstance(handlers, list) and not handlers:
        issues.append(
            _issue(
                path,
                f"{location}.event_handlers",
                "GATEWAY_SCHEMA",
                "gateway must declare at least one event handler",
            )
        )
    issues.extend(_gateway_acknowledgment_issues(path, app_config, location))
    issues.extend(_gateway_handler_policy_issues(path, app_config, location))
    return tuple(issues)


def _tool_default_matches(parameter_type: str, value: object) -> bool:
    predicates: Mapping[str, Callable[[object], bool]] = {
        "string": lambda item: isinstance(item, str),
        "integer": lambda item: type(item) is int,
        "number": lambda item: type(item) in (int, float),
        "boolean": lambda item: type(item) is bool,
    }
    return parameter_type in predicates and predicates[parameter_type](value)


def _tool_parameter_shape_issues(
    path: Path,
    parameter: object,
    location: str,
) -> tuple[ValidationIssue, ...]:
    if (
        not isinstance(parameter, dict)
        or not isinstance(parameter.get("name"), str)
        or not cast(str, parameter["name"]).strip()
    ):
        issue = _issue(
            path,
            location,
            "TOOL_CONFIG",
            "Event Mesh Tool parameter name is required",
        )
        return (issue,)
    issues: list[ValidationIssue] = []
    parameter_type = parameter.get("type", "string")
    normalized_type = parameter_type.casefold() if isinstance(parameter_type, str) else ""
    if normalized_type not in {"string", "integer", "number", "boolean"}:
        issues.append(
            _issue(
                path,
                f"{location}.type",
                "TOOL_CONFIG",
                "Event Mesh Tool parameter type is unsupported",
            )
        )
    if "required" in parameter and type(parameter["required"]) is not bool:
        issues.append(
            _issue(
                path,
                f"{location}.required",
                "TOOL_CONFIG",
                "Event Mesh Tool parameter required flag must be boolean",
            )
        )
    if "default" in parameter and not _tool_default_matches(normalized_type, parameter["default"]):
        issues.append(
            _issue(
                path,
                f"{location}.default",
                "TOOL_CONFIG",
                "Event Mesh Tool parameter default has the wrong type",
            )
        )
    for field in ("context_expression", "payload_path"):
        field_value = parameter.get(field)
        if field in parameter and (not isinstance(field_value, str) or not field_value.strip()):
            issues.append(
                _issue(
                    path,
                    f"{location}.{field}",
                    "TOOL_CONFIG",
                    "Event Mesh Tool parameter expression must be a nonblank string",
                )
            )
    return tuple(issues)


def _tool_parameter_issues(
    path: Path,
    parameters: object,
    location: str,
) -> tuple[ValidationIssue, ...]:
    if not isinstance(parameters, list):
        issue = _issue(
            path,
            location,
            "TOOL_CONFIG",
            "Event Mesh Tool parameters must be a list",
        )
        return (issue,)
    issues = [
        issue
        for index, parameter in enumerate(parameters)
        for issue in _tool_parameter_shape_issues(path, parameter, f"{location}[{index}]")
    ]
    names = tuple(
        cast(str, parameter["name"])
        for parameter in parameters
        if isinstance(parameter, dict)
        and isinstance(parameter.get("name"), str)
        and cast(str, parameter["name"]).strip()
    )
    if len(names) != len(set(names)):
        issues.append(
            _issue(
                path,
                location,
                "TOOL_CONFIG",
                "Event Mesh Tool parameter names must be unique",
            )
        )
    return tuple(issues)


def _topic_parameter_issues(
    path: Path,
    parameters: object,
    location: str,
) -> tuple[ValidationIssue, ...]:
    if not isinstance(parameters, list):
        return ()
    issues: list[ValidationIssue] = []
    for required_name in sorted(TOPIC_IDENTIFIER_PARAMETERS):
        matches = tuple(
            parameter
            for parameter in parameters
            if isinstance(parameter, dict) and parameter.get("name") == required_name
        )
        valid = len(matches) == 1
        if valid:
            parameter = matches[0]
            parameter_type = parameter.get("type", "string")
            valid_type = isinstance(parameter_type, str) and parameter_type.casefold() == "string"
            context_expression = parameter.get("context_expression")
            has_source = parameter.get("required") is True or (
                isinstance(context_expression, str) and bool(context_expression.strip())
            )
            default = parameter.get("default")
            safe_default = not isinstance(
                default, str
            ) or not TOPIC_IDENTIFIER_FORBIDDEN_PATTERN.search(default)
            valid = valid_type and has_source and safe_default
        if not valid:
            issues.append(
                _issue(
                    path,
                    f"{location}.{required_name}",
                    "TOOL_CONFIG",
                    "topic identifier parameter must be unique, string, sourced, and wildcard-free",
                )
            )
    return tuple(issues)


def _event_tool_config_issues(
    path: Path,
    tool_config: Mapping[str, object],
    location: str,
) -> tuple[ValidationIssue, ...]:
    event_mesh_config = tool_config.get("event_mesh_config")
    if not isinstance(event_mesh_config, dict):
        issue = _issue(
            path,
            location,
            "TOOL_CONFIG",
            "Event Mesh Tool configuration is incomplete",
        )
        return (issue,)
    broker_location = f"{location}.event_mesh_config.broker_config"
    issues = list(_broker_issues(path, event_mesh_config.get("broker_config"), broker_location))
    issues.extend(
        _tool_parameter_issues(path, tool_config.get("parameters", []), f"{location}.parameters")
    )
    issues.extend(
        _topic_parameter_issues(path, tool_config.get("parameters", []), f"{location}.parameters")
    )
    if tool_config.get("wait_for_response", True) is not True:
        issues.append(
            _issue(
                path,
                f"{location}.wait_for_response",
                "TOOL_CONFIG",
                "Event Mesh Tool must use request/reply",
            )
        )
    request_expiry = event_mesh_config.get("request_expiry_ms", 30000)
    if type(request_expiry) is not int or request_expiry <= 0:
        issues.append(
            _issue(
                path,
                f"{location}.event_mesh_config.request_expiry_ms",
                "TOOL_CONFIG",
                "Event Mesh Tool request expiry must be a positive integer",
            )
        )
    if event_mesh_config.get("payload_format", "json") != "json":
        issues.append(
            _issue(
                path,
                f"{location}.event_mesh_config.payload_format",
                "TOOL_CONFIG",
                "Event Mesh Tool payload format must be JSON",
            )
        )
    topic = tool_config.get("topic")
    if not isinstance(topic, str) or not SAFE_TOOL_TOPIC_PATTERN.fullmatch(topic):
        issues.append(
            _issue(
                path,
                f"{location}.topic",
                "TOOL_TOPIC",
                "tool topic must target the command-gateway request family",
            )
        )
    return tuple(issues)


def _event_tool_issues(
    path: Path,
    raw_tool: Mapping[str, object],
    location: str,
) -> tuple[ValidationIssue, ...]:
    issues: list[ValidationIssue] = []
    if raw_tool.get("component_module") != TOOL_MODULE or raw_tool.get("class_name") != TOOL_CLASS:
        issues.append(
            _issue(
                path,
                f"{location}.class_name",
                "TOOL_SYMBOL",
                "Event Mesh Tool class is unavailable",
            )
        )
    issues.extend(
        _issue(
            path,
            f"{location}.{field}",
            "TOOL_SYMBOL",
            "alternate Event Mesh Tool loader path is forbidden",
        )
        for field in ALTERNATE_TOOL_LOADER_FIELDS
        if raw_tool.get(field) is not None
    )
    if raw_tool.get("tool_type") != "python":
        issues.append(
            _issue(
                path,
                f"{location}.tool_type",
                "TOOL_CONFIG",
                "Event Mesh Tool must use the Python tool type",
            )
        )
    tool_config = raw_tool.get("tool_config")
    if not isinstance(tool_config, dict):
        issues.append(
            _issue(
                path,
                f"{location}.tool_config",
                "TOOL_CONFIG",
                "Event Mesh Tool configuration is incomplete",
            )
        )
        return tuple(issues)
    issues.extend(_event_tool_config_issues(path, tool_config, f"{location}.tool_config"))
    return tuple(issues)


def _tool_issues(
    path: Path,
    app_config: Mapping[str, object],
    location: str,
) -> tuple[ValidationIssue, ...]:
    tools = app_config.get("tools", [])
    if not isinstance(tools, list):
        return ()
    issues: list[ValidationIssue] = []
    for index, raw_tool in enumerate(tools):
        if not isinstance(raw_tool, dict):
            continue
        component_module = raw_tool.get("component_module")
        class_name = raw_tool.get("class_name")
        if (
            raw_tool.get("tool_type") == "python"
            or component_module == TOOL_MODULE
            or class_name == TOOL_CLASS
        ):
            issues.extend(_event_tool_issues(path, raw_tool, f"{location}.tools[{index}]"))
    return tuple(issues)


def _app_issues(
    path: Path, app: object, runtime: _Runtime, index: int
) -> tuple[ValidationIssue, ...]:
    location = f"apps[{index}]"
    if not isinstance(app, dict):
        return (_issue(path, location, "APP_TYPE", "app entry must be a mapping"),)
    module = app.get("app_module")
    issues = list(_broker_issues(path, app.get("broker"), f"{location}.broker"))
    for field in ("app_package", "app_base_path"):
        if field in app:
            issues.append(
                _issue(
                    path,
                    f"{location}.{field}",
                    "APP_SOURCE",
                    "runtime package installation and filesystem import paths are forbidden",
                )
            )
    if module not in SUPPORTED_MODULES:
        issues.append(
            _issue(path, f"{location}.app_module", "APP_MODULE", "unsupported app module")
        )
        return tuple(issues)
    app_config = app.get("app_config")
    if not isinstance(app_config, dict):
        issues.append(
            _issue(path, f"{location}.app_config", "APP_CONFIG", "app_config must be a mapping")
        )
        return tuple(issues)
    typed_config = cast(dict[str, object], app_config)
    if module == AGENT_MODULE:
        issues.extend(
            _model_issues(
                path,
                typed_config,
                runtime.agent_model,
                f"{location}.app_config",
                model_required=True,
            )
        )
        issues.extend(_tool_issues(path, typed_config, f"{location}.app_config"))
    elif module == WORKFLOW_MODULE:
        issues.extend(
            _model_issues(
                path,
                typed_config,
                runtime.workflow_model,
                f"{location}.app_config",
                model_required=False,
            )
        )
    else:
        issues.extend(_gateway_issues(path, typed_config, runtime, f"{location}.app_config"))
    return tuple(issues)


def _envelope_result(path: Path, parsed: object, runtime: _Runtime) -> ValidationResult:
    if not isinstance(parsed, dict):
        issue = _issue(path, "document", "CONFIG_ROOT", "configuration root must be a mapping")
        return ValidationResult(path, 0, (issue,))
    apps = parsed.get("apps")
    if not isinstance(apps, list):
        issue = _issue(path, "apps", "APPS_TYPE", "apps must be a list")
        return ValidationResult(path, 0, (issue,))
    if not apps:
        issue = _issue(path, "apps", "APPS_EMPTY", "apps must contain at least one entry")
        return ValidationResult(path, 0, (issue,))
    issues = [
        issue for index, app in enumerate(apps) for issue in _app_issues(path, app, runtime, index)
    ]
    names = tuple(app.get("name") for app in apps if isinstance(app, dict))
    valid_names = tuple(name for name in names if isinstance(name, str) and name.strip())
    names_invalid = len(valid_names) != len(apps)
    names_duplicated = len(set(valid_names)) != len(valid_names)
    if names_invalid or names_duplicated:
        issues.append(
            _issue(path, "apps.name", "APP_NAME_UNIQUE", "app names must be nonblank and unique")
        )
    return ValidationResult(path, len(apps), tuple(sorted(issues)))


def _validate_one(
    path: Path,
    *,
    root: Path,
    declared_environment: frozenset[str],
    runtime: _Runtime,
) -> ValidationResult:
    _, include_issues = _read_include_tree(path, root=root)
    if include_issues:
        return ValidationResult(path, 0, tuple(sorted(include_issues)))
    source = _expanded_source(runtime, path)
    if source is None:
        issue = _issue(path, "document", "YAML_PARSE", "configuration could not be parsed")
        return ValidationResult(path, 0, (issue,))
    source_issues = _source_policy_issues(
        path,
        source,
        declared_environment,
        runtime.compose_yaml,
    )
    if source_issues:
        return ValidationResult(path, 0, tuple(sorted(source_issues)))
    parsed = _parsed_config(runtime, path)
    if parsed is None:
        issue = _issue(path, "document", "YAML_PARSE", "configuration could not be parsed")
        return ValidationResult(path, 0, (issue,))
    return _envelope_result(path, parsed, runtime)


def _merge_results(
    paths: Sequence[Path],
    results: tuple[ValidationResult, ...],
    runtime: _Runtime,
) -> tuple[ValidationResult, ...]:
    if len(paths) < MINIMUM_CONFIGS_TO_MERGE or any(not result.valid for result in results):
        return results
    parsed_configs = tuple(_parsed_config(runtime, path) for path in paths)
    merge_failure = ValidationResult(
        paths[0],
        0,
        (_issue(paths[0], "document", "CONFIG_MERGE", "configurations could not merge"),),
    )
    merged: object = None
    if any(parsed is None for parsed in parsed_configs):
        merged_result = merge_failure
    else:
        try:
            for parsed in parsed_configs:
                merged = runtime.merge_config(merged, parsed)
            merged_result = _envelope_result(paths[0], merged, runtime)
        except Exception:
            merged_result = merge_failure
    if merged_result.valid:
        return results
    return tuple(
        ValidationResult(
            result.path,
            result.app_count,
            tuple(
                _issue(result.path, issue.location, issue.rule, issue.message)
                for issue in merged_result.issues
            ),
        )
        for result in results
    )


def validate_paths(
    paths: Sequence[Path],
    *,
    config_root: Path,
    env_template: Path,
) -> tuple[ValidationResult, ...]:
    """Validate configuration paths without starting the Agent Mesh runtime."""
    ordered_paths = tuple(sorted(path.resolve() for path in paths))
    try:
        environment = _read_environment_template(env_template)
        with _isolated_runtime_environment(environment):
            runtime = _load_runtime()
            results = tuple(
                _validate_one(
                    path,
                    root=config_root.resolve(),
                    declared_environment=frozenset(environment),
                    runtime=runtime,
                )
                for path in ordered_paths
            )
            return _merge_results(ordered_paths, results, runtime)
    except (OSError, ValueError, importlib.metadata.PackageNotFoundError, _RuntimeBoundaryError):
        return tuple(
            ValidationResult(
                path,
                0,
                (
                    _issue(
                        path,
                        "runtime",
                        "RUNTIME_PREREQUISITE",
                        "pinned validation runtime is unavailable",
                    ),
                ),
            )
            for path in ordered_paths
        )


def _display_path(path: Path, project_root: Path) -> str:
    try:
        return path.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return path.name


def _selected_paths(arguments: Sequence[str], project_root: Path) -> tuple[Path, ...] | None:
    config_root = (project_root / "configs").resolve()
    if arguments:
        candidates = tuple((project_root / argument).resolve() for argument in arguments)
        candidates_are_configs = all(
            _contained(path, config_root) and path.suffix.casefold() in {".yaml", ".yml"}
            for path in candidates
        )
        return tuple(sorted(candidates)) if candidates_are_configs else None
    if not config_root.is_dir():
        return ()
    return tuple(sorted((*config_root.rglob("*.yaml"), *config_root.rglob("*.yml"))))


def run(
    arguments: Sequence[str],
    *,
    project_root: Path,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    """Run the deterministic command interface against one Agent Mesh project."""
    paths = _selected_paths(arguments, project_root)
    if paths is None:
        print("USAGE config paths must stay under agent-mesh/configs", file=stderr)
        return 2
    if not paths:
        print("SKIP agent-mesh/configs has no configuration files", file=stdout)
        return 0
    results = validate_paths(
        paths,
        config_root=project_root / "configs",
        env_template=project_root.parent / ".env.example",
    )
    for result in results:
        display = _display_path(result.path, project_root)
        if result.valid:
            print(f"VALID {display} ({result.app_count} apps)", file=stdout)
            continue
        for issue in result.issues:
            print(
                f"INVALID {display}: {issue.location} [{issue.rule}] {issue.message}",
                file=stderr,
            )
    return 1 if any(not result.valid for result in results) else 0


def main(arguments: Sequence[str] | None = None) -> int:
    """Run the validator from the current Agent Mesh project directory."""
    selected = tuple(sys.argv[1:]) if arguments is None else arguments
    return run(selected, project_root=Path.cwd(), stdout=sys.stdout, stderr=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
