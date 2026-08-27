"""Owned app seam for the pinned official Event Mesh Gateway."""

from __future__ import annotations

from typing import TYPE_CHECKING, override

if TYPE_CHECKING:

    class _EventMeshGatewayAppBase:
        def _get_gateway_component_class(self) -> type[object]: ...

else:
    from sam_event_mesh_gateway.app import EventMeshGatewayApp as _EventMeshGatewayAppBase

from .component import AerialRescueEventMeshGatewayComponent

info = {
    "class_name": "AerialRescueEventMeshGatewayApp",
    "description": "Pinned Event Mesh Gateway with project-owned Direct application output.",
}


class AerialRescueEventMeshGatewayApp(_EventMeshGatewayAppBase):
    """Select the owned component through the upstream supported class seam."""

    @override
    def _get_gateway_component_class(self) -> type[object]:
        return AerialRescueEventMeshGatewayComponent
