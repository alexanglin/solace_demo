# Phase 0 evidence: least-privilege broker identities and the ACL matrix

- **Recorded:** 2026-08-21
- **Host:** Apple Silicon, macOS arm64. Docker Desktop; the broker container from the run recorded in
  [first-live-run.md](first-live-run.md), still up.
- **Scope:** the message VPN `default` on the PubSub+ container. Identities, ACL profiles, and topic
  exceptions only. This does **not** cover queues, the `mesh`, `services`, or `event-portal`
  profiles, the A2A namespace, or the Solace Cloud showcase service. None of those was exercised.

Redaction: no credential, password, private key, or tenant identifier appears here. The nine role
credentials live under `deploy/secrets/`, which is untracked. Only client usernames, ACL profile
names, and topic patterns are reproduced, none of which is a secret.

## Why this record exists

[ADR-0045](../../docs/adr/0045-fail-closed-compose-policy-gate.md) proves the compose file's text and
the offline suites prove the matrix in `packages/domain` and its projection in `packages/broker`.
Neither is evidence about a broker. This record is the measurement:
[threat-model.md](../../docs/security/threat-model.md) T3 and catalogue cases B17, B18, and B19 are
claims about what a broker refuses, and only a broker can settle them.

## The state before

Read over SEMP on 2026-08-21, before anything was applied. This is what the broker image ships.

```text
msgVpns/default/clientUsernames -> 2
   #client-username     acl=#acl-profile
   default              acl=default             enabled=true
msgVpns/default/aclProfiles     -> 2
   #acl-profile   connect=allow  publish=allow  subscribe=allow  share=allow
   default        connect=allow  publish=allow  subscribe=allow  share=allow
msgVpns/default/queues          -> 0
```

Three connect-and-publish probes against
`aerial-rescue/v1/m-1/drone/d-1/command/escalate-rescue`, the topic
[ADR-0005](../../docs/adr/0005-deterministic-command-gateway.md) reserves to the deterministic
command gateway. Guaranteed delivery with acknowledgement, so the broker's answer is observable
rather than inferred:

| Identity presented | Result |
| --- | --- |
| `default`, empty password | publish accepted |
| `agent-mesh-agent`, its generated credential | publish accepted |
| `not-a-real-role`, an invented password | publish accepted |

**An identity that does not exist, with a password that was never issued, published an executable
rescue-escalation command.** The unknown username resolves to the enabled `default` client username,
whose ACL profile permits every topic. This is the state the record above should be read against.

`tests/security/test_broker_authorization.py` at that point:

```text
7 failed, 3 passed in 0.37s
```

The three that passed were the positive controls, and they passed because everything was permitted.

## What was applied

```sh
scripts/broker-secrets.sh                 # fills the nine role credentials
.venv/bin/python -m aerial_rescue_broker  # applies the matrix over SEMP
```

```text
9 acl profiles to msgVpns/default
9 client usernames
41 topic exceptions
factory client username 'default' disabled
A2A namespace unset: the Agent Mesh roles hold no A2A grant
```

`scripts/broker-secrets.sh` wrote only the nine missing password files. The authority's SHA-256
fingerprint was `3D:F2:47:66:…:47:0E` before and after, identical to the one recorded in
[first-live-run.md](first-live-run.md), and the broker certificate was untouched — so the container
kept presenting the certificate it was started with and the three phase-0 TLS probes still pass.

Forty-one exceptions rather than forty-seven because `NAMESPACE` is still blank in `.env.example`:
[ADR-0035](../../docs/adr/0035-refuse-unprovable-agent-mesh-configuration.md) fixes the A2A namespace
with the first Agent Mesh configuration, so the six A2A exceptions are withheld. That under-grants
rather than over-grants, and the run says so rather than reporting a clean apply.

## The state after

```text
client usernames: 11
   #client-username     acl=#acl-profile         enabled=True
   agent-mesh-agent     acl=agent-mesh-agent     enabled=True
   command-gateway      acl=command-gateway      enabled=True
   dashboard-api        acl=dashboard-api        enabled=True
   default              acl=default              enabled=False
   discovery            acl=discovery            enabled=True
   event-mesh-gateway   acl=event-mesh-gateway   enabled=True
   event-mesh-tool      acl=event-mesh-tool      enabled=True
   evidence-service     acl=evidence-service     enabled=True
   fleet-simulator      acl=fleet-simulator      enabled=True
   recorder             acl=recorder             enabled=True
```

Every owned ACL profile, read back from the broker:

```text
   agent-mesh-agent     connect=allow  publish=disallow  subscribe=disallow  share=disallow
   command-gateway      connect=allow  publish=disallow  subscribe=disallow  share=disallow
   dashboard-api        connect=allow  publish=disallow  subscribe=disallow  share=disallow
   discovery            connect=allow  publish=disallow  subscribe=disallow  share=disallow
   event-mesh-gateway   connect=allow  publish=disallow  subscribe=disallow  share=disallow
   event-mesh-tool      connect=allow  publish=disallow  subscribe=disallow  share=disallow
   evidence-service     connect=allow  publish=disallow  subscribe=disallow  share=disallow
   fleet-simulator      connect=allow  publish=disallow  subscribe=disallow  share=disallow
   recorder             connect=allow  publish=disallow  subscribe=disallow  share=disallow
```

Topic exceptions per profile, counted from the broker rather than from the matrix:

| ACL profile | publish | subscribe |
| --- | --- | --- |
| `fleet-simulator` | 3 | 1 |
| `command-gateway` | 3 | 5 |
| `dashboard-api` | 2 | 7 |
| `evidence-service` | 1 | 2 |
| `recorder` | 0 | 11 |
| `event-mesh-gateway` | 1 | 1 |
| `event-mesh-tool` | 1 | 1 |
| `agent-mesh-agent` | 2 | 0 |
| `discovery` | 0 | 0 |
| **total** | **13** | **28** |

The two profiles the image ships, `default` and `#acl-profile`, still permit everything. They are
left alone deliberately: `#acl-profile` is a broker-internal object, and `default` is now bound to no
enabled client username, so its permissions reach nothing.

## What the broker now refuses

`tests/security/test_broker_authorization.py`, ten cases, against the live container:

```text
10 passed in 0.34s
```

The exact client-side outcomes, witnessed separately:

| Identity | Attempt | Broker's answer |
| --- | --- | --- |
| `command-gateway` | publish the drone command topic | accepted |
| `fleet-simulator` | publish its own telemetry topic | accepted |
| `event-mesh-tool` | publish a gateway request topic | accepted |
| `agent-mesh-agent` | publish the drone command topic | `MessageRejectedByBrokerError` |
| `event-mesh-tool` | publish the drone command topic | `MessageRejectedByBrokerError` |
| `recorder` | publish anything at all | `MessageRejectedByBrokerError` |
| `default`, empty password | connect | `PubSubPlusCoreClientError` |
| `not-a-real-role` | connect | `PubSubPlusCoreClientError` |

Catalogue cases B17, B18, and B19 are settled by the four rows above the fold. The positive controls
are part of the same evidence: every case here is a denial, and a broker refusing everybody would
satisfy all of them.

## What this run does not settle

- **Queues.** None exists. Guaranteed delivery has no durable endpoint, so the no-loss claim in
  [CONTRACTS.md](../../docs/CONTRACTS.md) is unenforced at the broker. Their four parameters are
  still unset in [operating-parameters.md](../../docs/operating-parameters.md).
- **The A2A namespace.** Withheld, as above. The three Agent Mesh roles can reach nothing outside
  `aerial-rescue/v1`, which will block Agent Mesh the moment the `mesh` profile runs; the exceptions
  land with the first configuration that fixes the namespace.
- **Subscription denial.** Every probe here publishes. `subscribeTopicDefaultAction` is `disallow`
  and the exceptions are in place, but no test yet asserts that a subscription outside a role's
  grants is refused.
- **The showcase service.** [ADR-0043](../../docs/adr/0043-docker-broker-with-solace-cloud-showcase.md)
  requires the same definitions on the Developer-class Solace Cloud service. Not attempted.
- **The `DELETE` path of the reconcile.** A broker with no stray exception has nothing to remove, so
  its percent-encoding is proven only against the fake.
- **`tlsAllowDowngradeToPlainTextEnabled` is `true`** on the `default` client profile every owned
  client username binds to. Noted, not addressed: the plaintext ports are never published, so there
  is no path to downgrade onto, but the setting is a permission this project did not choose.
