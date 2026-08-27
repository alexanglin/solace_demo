"""Dashboard production settings, broker bindings, and Unix-listener tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from aerial_rescue_contracts.view import MAX_BUFFERED_EVENTS
from aerial_rescue_dashboard_api.console import (
    SettingsError,
    dashboard_bindings,
    listener_options,
    settings_from_environment,
)
from aerial_rescue_domain.principals import Access, Principal, grants


def _environment(tmp_path: Path) -> dict[str, str]:
    secret = tmp_path / "scenario-bearer"
    secret.write_text("s" * 43, encoding="ascii")
    for name in ("assets", "replays", "scenarios", "schemas", "deploy"):
        (tmp_path / name).mkdir()
    return {
        "DASHBOARD_ALLOWED_HOSTS": "localhost:8080",
        "DASHBOARD_ALLOWED_ORIGIN": "http://localhost:8080",
        "DASHBOARD_OPERATOR_ID": "local-operator",
        "DASHBOARD_ASSET_ROOT": str(tmp_path / "assets"),
        "DASHBOARD_REPLAY_ROOT": str(tmp_path / "replays"),
        "DASHBOARD_SOCKET_PATH": str(tmp_path / "dashboard-api.sock"),
        "SCENARIO_CATALOG_ROOT": str(tmp_path / "scenarios"),
        "SCENARIO_CONTROL_URL": "http://scenario-service:8081/",
        "SCENARIO_CONTROL_HOST": "scenario-service:8081",
        "SCENARIO_CONTROL_BEARER_FILE": str(secret),
        "AERIAL_RESCUE_SCHEMA_DIR": str(tmp_path / "schemas"),
        "AERIAL_RESCUE_DEPLOY_DIR": str(tmp_path / "deploy"),
        "SOLACE_BROKER_URL": "tcps://broker:55443",
        "SOLACE_BROKER_VPN": "default",
        "TRUST_STORE": "/etc/aerial-rescue/certs/ca.pem",
        "POSTGRES_USER": "aerial_rescue",
        "POSTGRES_DB": "aerial_rescue",
    }


def test_settings_select_one_unix_socket_and_keep_private_bearer_out_of_repr(
    tmp_path: Path,
) -> None:
    # Arrange
    environment = _environment(tmp_path)

    # Act
    settings = settings_from_environment(environment)
    listener = listener_options(settings)

    # Assert
    assert listener.uds == tmp_path / "dashboard-api.sock"
    assert listener.access_log is False
    assert listener.proxy_headers is False
    assert listener.server_header is False
    assert "s" * 43 not in repr(settings)


def test_dashboard_bindings_are_derived_only_from_the_role_grant_table() -> None:
    # Arrange
    subscribed = grants(Principal.DASHBOARD_API, Access.SUBSCRIBE)

    # Act
    bindings = dashboard_bindings()

    # Assert
    assert set(bindings.queues).issubset({family.literal_suffix for family in subscribed})
    assert bindings.direct_subscriptions
    assert bindings.direct_receiver_capacity == MAX_BUFFERED_EVENTS


def test_missing_required_runtime_setting_fails_without_echoing_other_values(
    tmp_path: Path,
) -> None:
    # Arrange
    environment = _environment(tmp_path)
    environment.pop("DASHBOARD_ALLOWED_ORIGIN")

    # Act
    with pytest.raises(SettingsError) as captured:
        settings_from_environment(environment)

    # Assert
    assert str(captured.value) == "dashboard runtime setting is missing"
    assert "localhost" not in str(captured.value)
