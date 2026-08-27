"""Shared, bounded operations for the explicitly selected local-broker probes."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Final

from aerial_rescue_broker.deployment import (
    ADMIN_CREDENTIAL,
    ADMIN_USERNAME,
    CERTIFICATE_AUTHORITY,
    DEFAULT_HOST,
    DEFAULT_PORT,
    DEFAULT_VPN,
    read_credential,
)
from aerial_rescue_broker.messaging import (
    BrokerEndpoint,
    Outcome,
    SolacePersistentReceiver,
    build_service,
)
from aerial_rescue_broker.provisioning import message_count
from aerial_rescue_broker.semp import SempEndpoint, SempSession, connect
from aerial_rescue_domain.principals import Principal
from solace.messaging.config.solace_properties import authentication_properties as auth
from solace.messaging.config.solace_properties import service_properties as service_property
from solace.messaging.config.solace_properties import transport_layer_properties as transport
from solace.messaging.config.transport_security_strategy import TLS
from solace.messaging.messaging_service import MessagingService

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[1]
DEPLOY_ROOT: Final = REPOSITORY_ROOT / "deploy"
LOCAL_BROKER_ENDPOINT: Final = BrokerEndpoint(
    url="tcps://localhost:55443",
    vpn=DEFAULT_VPN,
    trust_store=str(DEPLOY_ROOT / "certs"),
)
SHARED_PROBE_DRONES: Final = (
    "drone-delivery-probe",
    "drone-dispatch-probe",
    "drone-vision-01",
    "drone-thermal-02",
    "drone-audio-03",
)


def native_service(username: str, credential: str) -> MessagingService:
    """Build the zero-retry native client used by black-box denial and compatibility probes."""
    properties = {
        transport.HOST: LOCAL_BROKER_ENDPOINT.url,
        service_property.VPN_NAME: LOCAL_BROKER_ENDPOINT.vpn,
        auth.SCHEME_BASIC_USER_NAME: username,
        auth.SCHEME_BASIC_PASSWORD: credential,
        transport.CONNECTION_RETRIES: 0,
        transport.RECONNECTION_ATTEMPTS: 0,
    }
    return (
        MessagingService.builder()
        .from_properties(properties)
        .with_transport_security_strategy(
            TLS.create().with_certificate_validation(
                False,
                validate_server_name=True,
                trust_store_file_path=LOCAL_BROKER_ENDPOINT.trust_store,
            )
        )
        .build()
    )


def role_credential(role: Principal) -> str:
    """Read one generated role credential without exposing it to diagnostics."""
    return read_credential(DEPLOY_ROOT, role)


def native_role_service(role: Principal) -> MessagingService:
    """Build the zero-retry native client under one generated project role."""
    return native_service(role.value, role_credential(role))


def connected_native_role_service(role: Principal) -> MessagingService:
    """Connect the zero-retry native compatibility client for one generated role."""
    service = native_role_service(role)
    service.connect()
    return service


def connected_service(role: Principal) -> MessagingService:
    """Connect one production Solace service under its generated role credential."""
    service = build_service(LOCAL_BROKER_ENDPOINT, role, role_credential(role))
    service.connect()
    return service


def administrator_semp_endpoint() -> SempEndpoint:
    """Return the local administrator endpoint over the per-checkout authority."""
    return SempEndpoint(
        host=DEFAULT_HOST,
        port=DEFAULT_PORT,
        username=ADMIN_USERNAME,
        password=(DEPLOY_ROOT / ADMIN_CREDENTIAL).read_text(encoding="utf-8").strip(),
        certificate_authority=str(DEPLOY_ROOT / CERTIFICATE_AUTHORITY),
    )


def queue_depth(queue: str) -> int:
    """Count the messages currently spooled on one queue through bounded SEMP paging."""
    endpoint = administrator_semp_endpoint()
    connection = connect(endpoint)
    try:
        return message_count(SempSession(connection, endpoint), DEFAULT_VPN, queue)
    finally:
        connection.close()


def settled_queue_depth(
    queue: str,
    expected: int,
    *,
    polls: int,
    interval_seconds: float,
) -> int:
    """Poll until a settlement is visible, returning the last bounded observation."""
    depth = queue_depth(queue)
    for _ in range(polls):
        if depth == expected:
            return depth
        time.sleep(interval_seconds)
        depth = queue_depth(queue)
    return depth


def drain_queue(
    role: Principal,
    queue: str,
    *,
    first_window_milliseconds: int,
    subsequent_window_milliseconds: int,
) -> int:
    """Accept every available message from an owned queue and return the count."""
    service = connected_service(role)
    receiver = SolacePersistentReceiver(service, queue)
    taken = 0
    window = first_window_milliseconds
    try:
        message = receiver.receive(window)
        while message is not None:
            receiver.settle(message, Outcome.ACCEPTED)
            taken += 1
            window = subsequent_window_milliseconds
            message = receiver.receive(window)
    finally:
        receiver.close()
        service.disconnect()
    return taken
