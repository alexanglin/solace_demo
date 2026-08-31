# ADR-0221: Terminate the Agent Mesh namespace with a separator

- **Status:** Accepted
- **Date:** 2026-08-31
- **Deciders:** Alex Anglin

## Context

Agent Mesh builds nearly every topic through helpers that normalise the namespace. The base is
`get_a2a_base_topic`, which is `f"{namespace.rstrip('/')}/{A2A_BASE_PATH}"`: it strips a trailing
separator and then adds its own, so `aerial-rescue-mesh` and `aerial-rescue-mesh/` produce the
identical topic. Discovery, agent request, and response topics all descend from it.

Two families do not. `agent/adk/models/dynamic_model_provider_topics.py` declares its four
constants as direct concatenations:

```python
BOOTSTRAP_SUBSCRIBE_TOPIC = "{namespace}configuration/model/bootstrap/>"
BOOTSTRAP_REQUEST_TOPIC = "{namespace}configuration/model/bootstrap/{model_id}"
BOOTSTRAP_RESPONSE_TOPIC = "{namespace}configuration/model/response/{model_id}/{component_id}"
MODEL_CONFIG_UPDATE_TOPIC = "{namespace}configuration/model/{model_id}"
```

The Web UI's scheduler topics are built the same way, as is the endpoint name each component
creates for its own model-configuration queue.

With `NAMESPACE=aerial-rescue-mesh` and an unterminated declaration, those render as
`aerial-rescue-meshconfiguration/model/...`. The first topic level is the namespace glued to a
word. [ADR-0061](0061-least-privilege-broker-principals-and-topic-authorization.md)'s matrix grants the three Agent Mesh roles
exactly `a2a_subscription(NAMESPACE)`, which is `aerial-rescue-mesh/>`, in both directions. A glued
first level is a different level, so every message in those families would be refused by the broker
rather than delivered — and a denied Direct subscription is silent to the client, so the symptom
would be a component that starts, reports healthy, and never receives anything.

Fixing this in `.env` is not available. `NAMESPACE` feeds `just provision --namespace` and
`a2a_subscription`, and `subscriptions.py` refuses a value with an empty level, which is what a
trailing separator produces once the value is split. The declaration that must change is therefore
the one inside the mesh's own configuration, where the value is consumed rather than validated.

## Decision

**Every app under `agent-mesh/configs/` declares `namespace: ${NAMESPACE}/`.** The environment
template, the provisioning argument, and the authorization matrix keep the unterminated value.

The separator is load-bearing rather than cosmetic, so it carries a comment at each declaration
naming this record, and a gate test asserts all three halves of the reasoning: that every committed
app terminates the value, that each upstream format rendered with the terminated namespace falls
inside `a2a_subscription(NAMESPACE)`, and that each one rendered *without* it falls outside. The
third assertion is what keeps the record honest — it fails if upstream ever normalises these
families, at which point the separator is redundant and this decision can be revisited.

## Consequences

**No broker change is required, and that is the point.** Terminating the namespace moves the
model-configuration families inside a grant the matrix already writes, so no new topic exception, no
new principal, and no change to the published exception totals. The alternative — granting the glued
first level explicitly — would have added an exception whose topic name a reviewer could not
recognise as belonging to this system.

**Every A2A topic that carries traffic is byte-identical before and after.** The normalising
helpers strip the separator, so discovery, agent requests, agent responses, and the gateway's own
topics do not move. Three sites in the pinned wheel interpolate the namespace against an explicit
separator and so gain an empty second level. Each was checked rather than assumed, and none is
load-bearing:

- `agent/sac/component.py:4055` publishes an agent-deregistration event. It is the only producer of
  that topic in the wheel, and there is no subscriber to it in the wheel or in this repository. The
  grant still admits it, because `aerial-rescue-mesh/>` matches a topic whose second level is empty.
- `gateway/base/component.py:2463` builds the `url` field of a gateway discovery card. It is a
  descriptive identifier rather than a subscription or a publication topic.
- `evaluation/subscriber.py:64` belongs to the evaluation tooling, which no committed configuration
  loads.

A gate in the Agent Mesh domain pins that set, so an upgrade that adds a fourth site — or that moves
one of these three onto a topic carrying traffic — fails rather than drifts.

**The Web UI's scheduler topics are fixed as a side effect.** They are inert today because the
scheduler feature flag is off, so this closes a defect that had not yet been reached rather than one
that was failing.

**A future upgrade must re-check the search.** A new upstream family built by concatenation would be
covered automatically, but one built with its own separator would now produce a doubled separator.
Nothing in the pinned wheel does that today, and the gate test would not catch it, so it belongs on
the Agent Mesh upgrade checklist rather than in a gate.
