"""OpenAPI 3.1 projection of the framework-free accepted public route registry."""

from __future__ import annotations

import re
from typing import cast

from fastapi import FastAPI

from aerial_rescue_dashboard_api.http_contract import ROUTE_EXPECTATIONS, BodyExpectation

type QueryExpectation = tuple[str, bool, tuple[str, ...]]
type ResponseExpectation = tuple[int | str, BodyExpectation]
type RouteExpectation = tuple[
    str,
    str,
    tuple[QueryExpectation, ...],
    BodyExpectation | None,
    tuple[ResponseExpectation, ...],
]

_PATH_PARAMETER = re.compile(r"{([^}]+)}")


def install_openapi(app: FastAPI) -> None:
    """Install a deterministic schema projection without publishing a documentation route."""
    routes = cast(tuple[RouteExpectation, ...], cast(object, ROUTE_EXPECTATIONS))
    app.openapi()
    app.openapi_schema = _document(routes)


def _document(routes: tuple[RouteExpectation, ...]) -> dict[str, object]:
    """Build the closed public route surface and external normative schema references."""
    paths: dict[str, object] = {}
    for method, path, queries, request_body, responses in routes:
        operation = _operation(method, path, queries, request_body, responses)
        path_item = cast(dict[str, object], paths.setdefault(path, {}))
        path_item[method.lower()] = operation
    return {
        "components": {
            "securitySchemes": {
                "bearerAuth": {"scheme": "bearer", "type": "http"},
            }
        },
        "info": {
            "title": "Aerial Rescue Wilderness Dashboard API",
            "version": "1.0.0",
        },
        "openapi": "3.1.0",
        "paths": paths,
    }


def _operation(
    method: str,
    path: str,
    queries: tuple[QueryExpectation, ...],
    request_body: BodyExpectation | None,
    responses: tuple[ResponseExpectation, ...],
) -> dict[str, object]:
    """Project one framework-free registry row into an OpenAPI operation."""
    operation: dict[str, object] = {
        "operationId": _operation_id(method, path),
        "responses": {str(status): _response(body) for status, body in responses},
    }
    parameters = _parameters(path, queries)
    if method == "POST":
        parameters.extend(_mutation_headers())
        operation["security"] = [{"bearerAuth": []}]
    if parameters:
        operation["parameters"] = parameters
    if request_body is not None:
        media_type, _kind, schema_ids = request_body
        if media_type is None:
            message = "request body registry entry must declare a media type"
            raise ValueError(message)
        operation["requestBody"] = {
            "content": {media_type: {"schema": _schema_reference(schema_ids)}},
            "required": True,
        }
    return operation


def _response(body: BodyExpectation) -> dict[str, object]:
    """Project one status/body expectation with only normative schema references."""
    media_type, kind, schema_ids = body
    response: dict[str, object] = {"description": "Closed dashboard response"}
    if media_type is None:
        return response
    schema = _schema_reference(schema_ids)
    if kind == "html-embed":
        schema = {"type": "string", "x-embeddedSchemas": list(schema_ids)}
    response["content"] = {media_type: {"schema": schema}}
    return response


def _schema_reference(schema_ids: tuple[str, ...]) -> dict[str, object]:
    """Use one external normative reference or a closed union for SSE frame shapes."""
    if len(schema_ids) == 1:
        return {"$ref": schema_ids[0]}
    return {"oneOf": [{"$ref": schema_id} for schema_id in schema_ids]}


def _parameters(path: str, queries: tuple[QueryExpectation, ...]) -> list[dict[str, object]]:
    """Return exact path and closed query parameter descriptions."""
    parameters = [
        {
            "in": "path",
            "name": name,
            "required": True,
            "schema": {"type": "string"},
        }
        for name in _PATH_PARAMETER.findall(path)
    ]
    parameters.extend(
        {
            "in": "query",
            "name": name,
            "required": required,
            "schema": {"enum": list(values), "type": "string"},
        }
        for name, required, values in queries
    )
    return parameters


def _mutation_headers() -> list[dict[str, object]]:
    """Document explicit Origin and idempotency fields in addition to bearer security."""
    return [
        {
            "in": "header",
            "name": "Origin",
            "required": True,
            "schema": {"type": "string"},
        },
        {
            "in": "header",
            "name": "Idempotency-Key",
            "required": True,
            "schema": {
                "format": "uuid",
                "pattern": (
                    "^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
                ),
                "type": "string",
            },
        },
    ]


def _operation_id(method: str, path: str) -> str:
    """Return a deterministic generated-operation identifier."""
    words = re.sub(r"[^A-Za-z0-9]+", "_", path).strip("_")
    return f"{method.lower()}_{words}"
