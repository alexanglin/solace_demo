"""The private scenario-control runtime has no broker identity or projection API."""

from __future__ import annotations

from inspect import signature

import pytest
from aerial_rescue_broker.deployment import provision
from aerial_rescue_domain.principals import Principal

pytestmark = [pytest.mark.unit]


def test_scenario_control_is_absent_from_the_broker_roles_and_provisioning_api() -> None:
    # Arrange
    scenario_member = "SCENARIO_SERVICE"
    retired_parameters = {"selection", "projection", "queue_projection"}

    # Act
    role = Principal.__members__.get(scenario_member)
    parameters = frozenset(signature(provision).parameters)

    # Assert
    assert role is None
    assert retired_parameters.isdisjoint(parameters)
