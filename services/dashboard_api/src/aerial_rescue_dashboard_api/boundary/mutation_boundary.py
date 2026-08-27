"""Authenticated, schema-validated public HTTP mutation input."""

from __future__ import annotations

from dataclasses import dataclass

from aerial_rescue_dashboard_api.boundary.ingress import MutationIngress


@dataclass(frozen=True)
class AuthorizedMutation:
    """One canonical request plus the identity derived from its accepted bearer."""

    ingress: MutationIngress
    operator_id: str
