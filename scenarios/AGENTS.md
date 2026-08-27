# Committed Scenario Instructions

## Scope and authority

These instructions apply under `scenarios/`. Read the repository-root [`AGENTS.md`](../AGENTS.md) and
the scenario-service guide before changing a production scenario. [ADR-0100](../docs/adr/0100-commit-a-strict-wilderness-scenario-catalog.md)
fixes the paths, version, prepared workload, and bounds. The normative shapes remain
[`schemas/v1/scenario/`](../schemas/v1/scenario/), and the loader remains owned by
[`services/scenario_service`](../services/scenario_service/AGENTS.md).

The files here are committed but untrusted production inputs, not golden fixtures or captured runtime
data. Keep them anonymous, synthetic, deterministic, and free of credentials, tenant values, real
incident coordinates, people, biometrics, runtime traces, and downloaded assets.

## Catalog and definition rules

- `catalog.v1.json` is the only discovery entry point. Callers select an identifier and revision; they
  never supply a path.
- `v1/wilderness-missing-person.r1.json` is the revision-one definition. A new revision is a new entry
  and file, not an in-place rewrite of an accepted revision.
- `definitionSha256` covers the definition's exact UTF-8 bytes. Write the definition first, compute its
  SHA-256, then update the catalog. Reformatting or changing the final newline changes the digest.
- Use only the integer canonical JSON value space, lower-camel-case keys, explicit values, LF line
  endings, and one final newline. Never add a floating-point value, duplicate key, implicit default,
  generated roster, mission identity, run identity, or seed.
- Polygon closure is explicit: every search and sector polygon repeats its first coordinate as its last
  coordinate and contains at least three distinct boundary coordinates.
- Member declarations use the schema's discriminated `members` array. `SIMULATED_DRONE` entries carry
  every simulator input. `DECLARED_ONLY` entries carry only identity, role, and the literal
  `DECLARED ONLY — NOT EXECUTED`; they never acquire a sector, telemetry, or connectivity.
- Heartbeat loss is a flat list of unique `{droneId, tickOrdinal}` pairs and may name only a simulated
  member.

The prepared wilderness definition contains exactly twenty sectors, `drone-sim-01` through
`drone-sim-20`, and the three declared-only members fixed by ADR-0100. Its tick interval is 1,000 ms,
its uniform sweep is twelve ticks, and `drone-sim-07` misses heartbeat ordinals two through seven.
The geometry is synthetic presentation and assignment input; it does not model terrain, weather,
probability of detection, flight safety, or a real search.

## Verification

Run from the repository root:

```sh
uv run --frozen pytest -q services/scenario_service/tests
pre-commit run check-json --files \
  scenarios/catalog.v1.json \
  scenarios/v1/wilderness-missing-person.r1.json \
  --hook-stage pre-commit
shasum -a 256 scenarios/v1/wilderness-missing-person.r1.json
git diff --check
```

Confirm the printed digest equals the catalog entry, both files remain below 256 KiB, the local guide's
`CLAUDE.md` target is exactly `AGENTS.md`, and no cache, secret, live data, or generated environment was
added.
