# ADR-0181: Gate continuous SEMP monitoring on VPN-scoped operator provisioning

- **Status:** Accepted
- **Date:** 2026-08-26
- **Deciders:** Alex Anglin
- **Extends:** ADR-0157 and ADR-0173

## Context

ADR-0157 fixes the routine queue-monitor identity at `aerialrescuemonitor`, global access `none`,
default Message VPN access `none`, and one selected-VPN `read-only` exception. It also refuses to
invent a SEMP v2 management-user endpoint or substitute a broader global grant. The repository now
has the bounded `ReadOnlySempMonitor` adapter, but no continuous process consumes it. ADR-0173's
credentialless retained-log follower covers broker events, not the aggregate queue state that SEMP
owns; neither monitor replaces the other.

The pinned broker's served SEMP v2 configuration specification contains no management-user writer or
readback schema for this complete scope. Solace's container configuration keys can create an internal
user with a password file and global access, but the keys are bootstrap configuration and expose no
selected-VPN access-level exception. Solace documents the container's interactive CLI as the supported
way to create internal users and configure global, default-VPN, and per-VPN exception access. See
[Configuring CLI users in software broker containers](https://docs.solace.com/Software-Broker/Container-Tasks/Config-CLI-Users-for-Software-Brokers.htm),
[Configuring internal CLI user accounts](https://docs.solace.com/Admin/Configuring-Internal-CLI-User-Accounts.htm),
[Displaying user authentication details](https://docs.solace.com/Admin/Displaying-User-Authentication-Info.htm),
and [CLI user access levels](https://docs.solace.com/Admin/CLI-User-Access-Levels.htm).

The official container CLI entry is interactive. No documented non-interactive flag or stdin/batch
contract can take the generated password file while preserving the selected-VPN exception and a
supported readback. Piping commands, copying a secret-bearing script into broker storage, widening the
user to global `read-only`, or pretending the startup keys reconcile an existing retained volume would
all weaken the boundary.

## Decision

Compose adds an explicit `semp-monitor` profile and never enables it through the default, `services`,
`mission-control`, or `event-portal` profiles. Local integration keeps ADR-0173's credentialless
retained-event follower as its always-on broker monitor. Enabling continuous SEMP queue health is a
fail-closed release prerequisite that requires the following interactive operator procedure first.

1. Run `just secrets`. It creates `deploy/secrets/semp-monitor-password` as a private generated file,
   never exports it through `.env.roles`, and never prints it.
2. Resolve the exact Compose project and broker container before mutation. Refuse if more than one
   local broker owns the fixed ports or the target container is ambiguous.
3. Open the official anonymous container CLI interactively; do not pipe stdin, copy a command file, or
   put the password in a host shell argument or history:

   ```console
   docker compose --env-file .env --env-file deploy/secrets/.env.roles \
     -f deploy/compose.yaml exec -it broker /usr/sw/loads/currentload/bin/cli -A
   ```

4. At the broker CLI prompt, enter `enable`, then `configure`. If the internal user does not exist,
   enter `create username aerialrescuemonitor`. Enter `username aerialrescuemonitor`, then enter the
   following settings. Paste the generated value only as `<generated-password>` at the broker CLI's
   `change-password` command; never substitute it into the host command above or record the resulting
   terminal:

   ```text
   change-password <generated-password>
   global-access-level none
   message-vpn default-access-level none
   message-vpn
   create access-level-exception default access-level read-only
   ```

   On a repeat run, enter the existing exception and set its value instead:

   ```text
   access-level-exception default access-level read-only
   ```

   `default` in these commands is the selected `SOLACE_BROKER_VPN`; an operator targeting another VPN
   must substitute that exact reviewed name in the exception and runtime environment.
5. Exit to User EXEC and run `show username aerialrescuemonitor detail`. Readback must show global
   `none`, default Message VPN `none`, exactly the selected VPN exception `read-only`, and no other VPN
   exception. Any broader or additional access is a refusal, not a warning.
6. Run the two live probes in `tests/security/test_broker_authorization.py` serially. The positive probe
   must read the narrow aggregate queue monitor collection. The negative probe reads the selected VPN's
   current `enabled` value and attempts to PATCH that same value; it must receive a SEMP authorization
   refusal, so even an unexpected authorization bug has no intended state delta. A denial without the
   positive read is not evidence.
7. Only after both probes pass, start the explicit profile:

   ```console
   docker compose --env-file .env --env-file deploy/secrets/.env.roles \
     -f deploy/compose.yaml --profile semp-monitor up --detach --wait semp-monitor
   ```

The service receives only `semp-monitor-password` and the public trust store. It has no administrator
credential, application broker credential, database credential, published port, or configuration
writer. Its Python composition fixes `aerialrescuemonitor`, uses certificate- and hostname-validating
SEMP TLS, constructs a `ReadOnlySempMonitor`, and exposes only monitor reads plus connection close. It
polls immediately and then at ADR-0157's interval, emits aggregate counts without queue names, and
closes its HTTPS connection on SIGINT or SIGTERM. Missing/blank/oversize material, an invalid roster,
TLS failure, authentication failure, paging failure, or incomplete monitor response exits nonzero;
Compose retries at most three times. A valid but unhealthy queue snapshot remains visible and polling
continues because backlog or a nonempty DMQ is an observed broker condition, not loss of monitoring.

Password rotation repeats the interactive password and access-level readback steps and the positive
and negative probes before the profile is recreated. The project does not claim that generated secret
material alone provisions broker authority.

## Consequences

- Routine queue health now has a concrete continuous composition with no application or management
  write capability.
- The default local stack remains automatable and continuously watches broker events without silently
  granting a management identity.
- Global `none`, VPN default `none`, the single read-only exception, positive access, and negative write
  are separate evidence; a permissive or unreachable broker cannot pass through one ambiguous result.
- Secret values stay out of Compose environment interpolation, host shell arguments, tracked files,
  runtime summaries, and live-test diagnostics.
- Negative: first deployment and every monitor-password rotation require an interactive broker
  operator. This is deliberate until the pinned supported broker exposes a complete non-interactive
  management-user contract.
- Negative: the liveness healthcheck proves only that the process has not exited. Queue health is the
  structured snapshot; read continuity is enforced by fail-closed process exit.
- This profile does not qualify a production broker host or Agent Mesh deployment. ADR-0167,
  ADR-0178, and ADR-0179 keep those claims separately gated.

## Alternatives considered

- **Grant global `read-only` through container startup keys.** Rejected because it exposes every global
  and VPN-scoped read instead of one selected VPN, and bootstrap keys do not reconcile the retained
  broker state safely.
- **Pipe CLI commands or source a secret-bearing broker script.** Rejected because no documented
  non-interactive contract proves input, exit, redaction, or cleanup, and the script would create a
  second credential lifecycle inside broker storage.
- **Use the administrator credential in the continuous process.** Rejected because the adapter's lack
  of `send` would be only an application convention while the credential itself authorizes writes.
- **Enable the SEMP monitor in every local profile.** Rejected because a clean automated stack cannot
  satisfy the manual least-privilege prerequisite; implicit activation would become either a crash loop
  or pressure to widen the grant.
- **Treat retained event logs as queue health.** Rejected because event continuity and current aggregate
  queue state are distinct broker-native instruments.
