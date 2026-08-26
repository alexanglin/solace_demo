"""Contract-owned validation and representation adapters for dashboard runtime documents."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Final, cast

from aerial_rescue_contracts import canonical
from pydantic import ValidationError

from aerial_rescue_dashboard_api.errors import ApiError, ErrorCode
from aerial_rescue_dashboard_api.wire import parse_wire_document

SCHEMA_PREFIX: Final = "https://aerial-rescue.invalid/schemas/v1/dashboard/"
CATALOG_SCHEMA: Final = f"{SCHEMA_PREFIX}scenario-catalog.schema.json"
REPLAY_SCHEMA: Final = f"{SCHEMA_PREFIX}replay-bundle.schema.json"
REDUCED_STATE_SCHEMA: Final = f"{SCHEMA_PREFIX}dashboard-reduced-state.schema.json"
ORDERED_EVENT_SCHEMA: Final = f"{SCHEMA_PREFIX}ordered-dashboard-event.schema.json"


def validated_document(schema_id: str, raw: bytes, *, maximum_bytes: int) -> Mapping[str, object]:
    """Validate bounded raw bytes through the canonical decoder and strict schema twin."""
    if len(raw) > maximum_bytes:
        raise ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE)
    try:
        parsed = parse_wire_document(schema_id, raw)
    except (ValueError, ValidationError) as invalid:
        raise ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE) from invalid
    dumped = cast(object, parsed.model_dump(by_alias=True))
    if not isinstance(dumped, Mapping):
        raise ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE)
    return cast(Mapping[str, object], dumped)


def canonical_validated_bytes(schema_id: str, raw: bytes, *, maximum_bytes: int) -> bytes:
    """Return the canonical representation of one validated dependency document."""
    return canonical.canonical_bytes(
        validated_document(schema_id, raw, maximum_bytes=maximum_bytes)
    )


def find_scenario(
    catalog: Mapping[str, object], identifier: str, revision: int
) -> Mapping[str, object]:
    """Select one exact scenario identity from an already validated catalog."""
    scenarios = _sequence(catalog.get("scenarios"))
    matches = [item for item in scenarios if _mapping(item).get("identifier") == identifier]
    if not matches:
        raise ApiError(ErrorCode.SCENARIO_NOT_FOUND)
    scenario = _mapping(matches[0])
    if scenario.get("revision") != revision:
        raise ApiError(ErrorCode.SCENARIO_REVISION_MISMATCH)
    return scenario


def prepare_live_state(
    scenario: Mapping[str, object],
    mission_id: str,
    predecessor_mission_id: str | None,
) -> bytes:
    """Project catalog identities into the stored canonical prepared reduced state."""
    members: list[dict[str, object]] = []
    for item in _sequence(scenario.get("members")):
        member = _mapping(item)
        identifier = _string(member.get("identifier"))
        participation = _string(member.get("participation"))
        if participation == "DECLARED_ONLY":
            members.append({"identifier": identifier, "participation": participation})
        else:
            members.append(
                {
                    "connectivity": "CONNECTED",
                    "identifier": identifier,
                    "participation": "SIMULATED",
                    "telemetry": None,
                }
            )
    sectors = [
        {
            "assignedMemberId": None,
            "identifier": _string(_mapping(item).get("identifier")),
            "state": "UNASSIGNED",
        }
        for item in _sequence(scenario.get("sectors"))
    ]
    document = {
        "canonicalizationVersion": 1,
        "currentMission": {
            "identifier": mission_id,
            "lifecycle": "PLANNED",
            "predecessorIdentifier": predecessor_mission_id,
        },
        "fleet": sorted(members, key=lambda item: _string(item["identifier"]).encode()),
        "latestAuditOrdinal": 0,
        "sectors": sorted(sectors, key=lambda item: _string(item["identifier"]).encode()),
        "stateVersion": 1,
    }
    encoded = canonical.canonical_bytes(document)
    validated_document(REDUCED_STATE_SCHEMA, encoded, maximum_bytes=512 * 1024)
    return encoded


def replay_initial_state(bundle: Mapping[str, object]) -> bytes:
    """Return the exact canonical initial-state member of a validated replay bundle."""
    return canonical.canonical_bytes(_mapping(bundle.get("initialState")))


def _mapping(value: object) -> Mapping[str, object]:
    """Narrow a validated object without accepting a broad dynamic value."""
    if not isinstance(value, Mapping):
        raise ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE)
    return cast(Mapping[str, object], value)


def _sequence(value: object) -> Sequence[object]:
    """Narrow a validated array while refusing strings and byte sequences."""
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE)
    return cast(Sequence[object], value)


def _string(value: object) -> str:
    """Narrow one schema-validated string."""
    if not isinstance(value, str):
        raise ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE)
    return value
