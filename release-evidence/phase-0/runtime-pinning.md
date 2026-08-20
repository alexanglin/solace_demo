# Phase 0 evidence: runtime pinning and compatibility

- **Recorded:** 2026-08-19
- **Host:** Apple Silicon, macOS arm64. `uv` 0.12.5.
- **Scope:** the two Python runtimes and the pinned Agent Mesh wheels. This does **not** cover the
  Solace Cloud broker, the local PubSub+ container, the Ollama models, or the Agent Mesh runtime
  spike, none of which were exercised.

Redaction: no credential, broker URL, tenant identifier, or model key appears here, and none was
configured during any run below.

## Resolved versions

| Domain | Interpreter | Packages locked | Key pins |
| --- | --- | --- | --- |
| Application workspace | CPython 3.14.7 | 84 | `solace-pubsubplus==1.11.0` |
| Agent Mesh project | CPython 3.13.15 | 251 | `solace-agent-mesh==1.28.7`, `sam-event-mesh-gateway==1.1.0`, `sam-event-mesh-tool==0.1.1` |

Upstream source revision for Agent Mesh 1.28.7: tag `1.28.7`, no `v` prefix, commit
`6344d2b8899a6c326e8b52fce9947c4bf4b56ae2`, confirmed against the GitHub ref API on 2026-08-19.

Both lockfiles resolve for macOS arm64 and Linux aarch64 only.

## Question 1: does `solace-pubsubplus` function on Python 3.14.7?

**Yes.** The wheels are tagged `py36-none-<platform>`, so installation proves nothing; the probes
reach past import to the native boundary. `tests/phase0/test_solace_messaging_runtime.py`, 7 cases,
all passing:

- the bundled native library loads as a `ctypes.CDLL` from inside the installed package, which means
  `solClient_initialize` returned success — it raises otherwise;
- a messaging service builds from explicit properties without connecting, which creates a session and
  passes a structure of `CFUNCTYPE` callbacks across the boundary;
- the API version, the application identifier, and a message payload all read back.

No broker is contacted; the configured host is an unrouted loopback port.

[ADR-0004](../../docs/adr/0004-split-python-runtimes.md)'s split-runtime decision survives its kill
criterion.

## Question 2: are Agent Mesh 1.28.7 and the two Event Mesh plugins compatible?

**Yes.** No upstream artifact attests the combination — the gateway declares no dependency on
`solace-agent-mesh` and the tool declares no dependencies at all — so the probes assert the symbols
each plugin actually imports. `agent-mesh/tests/test_pinned_plugin_compatibility.py`, 6 cases, all
passing from a cold bytecode cache:

- all three distributions report their pinned versions from one environment;
- the gateway is discoverable in the `solace_agent_mesh.plugins` entry-point group and `.load()`
  imports it against the runtime, returning its info mapping;
- `BaseGatewayApp` and `BaseGatewayComponent` are present as classes;
- `sam_event_mesh_tool` declares **no** entry point and is imported by module path, as agent
  configuration wires it;
- every runtime symbol the tool imports resolves and is callable — `DynamicTool`,
  `SamAgentComponent`, `AnyToolConfig`, `ToolContext`, `Message`.

## Upstream defects measured

Four warning classes, all from pinned upstream code, contained by
[ADR-0030](../../docs/adr/0030-contain-upstream-warnings-in-the-agent-mesh-domain.md):
`PydanticDeprecatedSince20` (11 occurrences across four Agent Mesh modules), the Solace client's
invalid escape sequence and its `datetime.utcnow()` default argument, and a `pydub` warning for
absent media tooling.

Two of these made the gate's verdict depend on something other than the code — a warm bytecode cache
in one case, unrelated system packages in the other — and the complete set was obtained only after
deleting every `__pycache__` directory in the 251-package environment.

## Dependency audit

11 distinct advisories across 5 packages in the `agent-mesh` domain; none in the application
workspace. All five packages are pinned exactly by Agent Mesh 1.28.7, and 1.28.7 is the latest
upstream release, so no safe upgrade exists for any of them.

The `google-adk` override required by the open-questions register was attempted and is
unsatisfiable; uv's verbatim output is quoted in
[ADR-0031](../../docs/adr/0031-reject-the-google-adk-version-override.md). The advisory is reported
as `PYSEC-2026-344`, not under the CVE alias the register named.

The accepted risk, its severity, and what would clear it are in
[TECH_DEBT.md](../../TECH_DEBT.md). All waivers expire 2026-09-18.

## Not established

- Whether Agent Mesh **runs**: no Orchestrator, agent, workflow, or Web UI was started. That needs
  owned configuration, which [ADR-0032](../../docs/adr/0032-agent-mesh-semantic-configuration-validator.md)
  blocks until the semantic validator exists.
- Any broker behaviour. No Solace Cloud service and no local PubSub+ container were contacted.
- Any model behaviour, capability, or cost. Ollama holds only one of the three named models.
- Resource use of the whole stack on one workstation.
