# ADR-0214: Keep the operator-provisioned SEMP identity out of the hosted suite

- **Status:** Accepted
- **Date:** 2026-08-30
- **Deciders:** Alex Anglin
- **Supersedes in part:** none

## Context

The hosted `PubSub+ and PostgreSQL integration` job has been red on `main`, reporting only

```text
FAILED: tests/security/test_broker_authorization.py
```

followed by SolOS boot narration. The runner captured the failing test's output and never printed it,
so the reason was unavailable; printing it is a separate change, and the first run that did print it
gave the answer immediately:

```text
................FF
E   SempError: the broker refused the SEMP request: "GET msgVpns/default/queues
      ?select=queueName,msgs.count&count=100 {} status=401 code=72
      description='Authorization failed'"
```

Sixteen cases pass. The two that fail are `SempMonitorAuthorizationTests`, and they fail because the
identity they authenticate as does not exist.

That is not a defect. [ADR-0181](0181-gate-continuous-semp-monitoring-on-vpn-scoped-operator-provisioning.md)
decides that `aerialrescuemonitor` is created by an **interactive operator procedure** at the broker's
own CLI: never by `python -m aerial_rescue_broker`, never by a Compose profile, and with its generated
password never placed in a host command argument or a piped stdin. A disposable broker created by a
hosted job therefore does not have that identity, and cannot be given it without contradicting the
decision that makes it safe.

So the blocking job contained two cases that could never pass in it. A permanently red job is not a
control: it trains its reader to ignore the one signal it exists to give.

## Decision

The two operator-provisioned cases move to `tests/security/test_semp_monitor_authorization.py`, which
is deliberately **not** in [ADR-0147](0147-admit-pubsub-integration-to-blocking-ci.md)'s
ordered allowlist and is therefore not run by the hosted job.

They are moved, not weakened. Both assertions are unchanged: the identity reads parent depth and active
flow aggregates, and the same identity is refused a same-value configuration write. An operator runs the
file on a workstation where ADR-0181's procedure has been completed, which is the only environment in
which the assertions mean anything.

`tests/security/test_broker_authorization.py` keeps its sixteen cases — every denial, every positive
control, the factory-identity case, and the subscription cases — and stays in the hosted allowlist,
where all sixteen already pass.

## Consequences

- The hosted job can be green, so its red is worth reading again. This is the point of the change.
- The SEMP monitor's authorization is no longer asserted by any blocking gate. That is a real
  reduction in what the pushed stages prove, and it is honest: the gate was not proving it before
  either, it was failing to.
- Negative: the moved file is run only when an operator remembers to run it. Nothing schedules it and
  no gate notices if it rots. ADR-0181's procedure is where a reader would look, and it does not yet
  name this file.
- Negative: the split puts two cases about the broker's authorization answers in a second file, so a
  reader asking "what does the broker refuse?" now has two places to look rather than one. The
  docstrings say which is which and why.
- The failure was diagnosable only because the runner now reports the failing test's own output. That
  change and this one are separable but were found together, and neither is much use without the other.

## Alternatives considered

- **Provision the identity in the hosted job.** Rejected: it reverses ADR-0181's decision, which turns
  on the password never reaching a command argument, a shell history, or a recorded terminal. A
  disposable runner weakens the consequence but not the rule, and reversing a safety decision to make
  a job green is the wrong direction. A superseding ADR could decide otherwise on its own evidence.
- **Skip the two cases unless an environment variable declares the identity provisioned.** Rejected:
  it puts a conditional skip into a suite that fails closed everywhere else, and a variable that is
  unset by default means the cases are skipped in every environment nobody deliberately configured —
  which is the same coverage as moving them, with a mechanism that can also silently skip on a
  workstation where they should have run.
- **Catch the `401` and skip.** Rejected outright: it would also swallow a genuine loss of the
  identity's access on a workstation where the procedure had been completed, which is exactly the
  regression these cases exist to catch.
- **Delete the two cases.** Rejected: the assertions are correct and the capability is real. What was
  wrong was where they ran.
