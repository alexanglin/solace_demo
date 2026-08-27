# Phase 3 evidence: the wilderness dashboard on the shared production-like stack

- **Recorded:** 2026-08-26 EDT. The exact command start and end timestamps were not retained, so this
  record does not invent them.
- **Revision:** `db2b64015a4dc451ae499a4784a8c6379d410835`, with tracked source clean. The
  checkout also contained the required untracked `deploy/certs` and `deploy/secrets` symlinks and the
  ignored `.env`; none was added to Git.
- **Host:** Apple Silicon, macOS 26.6.2 arm64. Docker Server 29.5.3 on aarch64, with 16 CPUs and
  8,216,018,944 bytes allocated to the Linux VM.
- **Toolchains:** Python 3.14.7, uv 0.12.5, Node.js 26.7.0, pnpm 11.23.0, and Playwright 1.62.1.
- **Images:** the locally built dashboard-extension application image was
  `sha256:759ec19852cf434994e7f365daf992cef4660abf0d3e0eedf0464cac91c5b36a`. The
  run retained only the observed image-ID prefixes for the shared broker (`sha256:05d725...`),
  PostgreSQL (`sha256:4e3d114...`), and Caddy (`sha256:6b08c1...`); their missing suffixes are not
  reconstructed here.
- **Prerequisites and pre-existing state:** the broker, PostgreSQL, and Agent Mesh containers in the
  existing `aerial-rescue-mesh` Compose project were already running. Dashboard history already existed
  in the shared PostgreSQL volume. Their three container IDs were sampled before and after the dashboard
  rebuild, production suite, and soak and remained equal; the values themselves are immaterial and are
  omitted.
- **Scope:** the local shared-project mission-control extension, Chromium at 1440x900 in dark and
  reduced-motion mode, the separate 64-case fixture inventory, the eight-case production browser
  inventory, one 61-sample soak, the selected live PostgreSQL store suite, the selected local broker
  authorization suite, and one in-app replay inspection.

Redaction: no password, bearer, private key, certificate content, expanded `.env` value, connection
string, container ID, mission identifier, raw mission payload, raw broker export, prompt, model output,
or tenant value appears here. All mission data was synthetic. The exact soak resource-summary attachment
was not retained by the successful line reporter, so no baseline or maximum is reconstructed from
memory.

## Why this record exists

The Phase 3 dashboard foundation in
[`docs/IMPLEMENTATION_PLAN.md`](../../docs/IMPLEMENTATION_PLAN.md#phase-3-simulator-and-operator-dashboard-foundation)
had deterministic fixture evidence and one developmental production run, but it still required a rebuild
and rerun from a committed source revision. The release-qualification plan also keeps fixture acceptance,
production-stack end-to-end execution, live integration, and performance evidence as distinct claims.
This record captures those boundaries without turning the current dashboard slice into the larger
initial-release workflow.

The producer meanings come from [`docs/TESTING.md`](../../docs/TESTING.md), the numerical instruments
from [`docs/operating-parameters.md`](../../docs/operating-parameters.md), and the claim ceiling from
[`docs/LIMITATIONS.md`](../../docs/LIMITATIONS.md). The dashboard extended the existing project under
[ADR-0139](../../docs/adr/0139-reuse-the-aerial-rescue-mesh-runtime-for-the-dashboard.md); it did not
create a disposable broker or database.

## What was run

The ignored local configuration and credentials were already prepared. No expanded value is reproduced.
The supported sequence was:

```sh
just mission-control-up --build --force-recreate
pnpm --dir apps/dashboard run test:e2e
pnpm --dir apps/dashboard run test:e2e:production
pnpm --dir apps/dashboard run test:e2e:soak
POSTGRES_USER="$(sed -n 's/^POSTGRES_USER=//p' .env)" \
  POSTGRES_DB="$(sed -n 's/^POSTGRES_DB=//p' .env)" \
  uv run --frozen pytest -q tests/integration/test_durable_store_live.py
uv run --frozen pytest -q tests/security/test_broker_authorization.py
```

`mission-control-up` rebuilt the seven dashboard-extension targets with `--no-deps`. It reused the
healthy broker and PostgreSQL containers in `aerial-rescue-mesh`; the separate before/after identity
readback also found Agent Mesh unchanged. The production and soak Playwright configurations used one
worker, no retries, blocked service workers, and disabled automatic screenshots, traces, and video.

The first fixture invocation inside the restricted command sandbox failed before browser assertions when
the sandbox refused the preview server's `127.0.0.1:4173` socket bind with `EPERM`. That failed harness
attempt is not counted as a test result. The same test command was rerun with the permitted local-socket
execution boundary; no test, fixture, or expectation was changed. The permitted rerun is the 64-case
result below.

## Results

| Producer | Exact observed result |
| --- | --- |
| Fixture Playwright | 64 passed of 64 in 42.0 s; zero failed, skipped, or retried |
| Curated fixture screenshots | Six masked committed baselines passed and were inspected individually |
| Production Playwright | 8 passed of 8 in 1.6 min; zero failed, skipped, or retried |
| Dashboard soak | 1 passed of 1 in 30.3 min; 61 samples including both endpoints |
| Live PostgreSQL store selector | 43 passed of 43 in 14.24 s: 41 PostgreSQL cases, each with a unique create/drop database, plus two local target-name/refusal cases |
| Live local broker-authorization selector | 16 passed of 16 in 0.57 s |
| Shared base identity | Broker, PostgreSQL, and Agent Mesh container IDs unchanged across rebuild and both browser runs |

The store count describes only `tests/integration/test_durable_store_live.py`. The authorization count
describes only the exact positive and negative controls in
`tests/security/test_broker_authorization.py`; it is not complete ACL, queue, TLS-downgrade, or Solace
Cloud coverage.

## What the eight production browser cases directly established

| Workflow | Direct observation |
| --- | --- |
| Initial command center | The loopback dashboard loaded READY with its search-map region visible, transient bootstrap removed, and `DEGRADED LIVE SIMULATION` explicit. That bounded initial workflow made zero remote-origin requests and opened zero WebSockets. |
| Prepared live mission | Browser Start reached `EXHAUSTED`. Visible timeline order contained PLANNED, SEARCHING, and EXHAUSTED; drone `drone-sim-07` degraded, went offline, and recovered; and the sector presentation showed at-risk, assigned, and searched progression. The Searched filter exposed 20 simulated-member rows. Fleet status reported exactly 14 completed ticks and 280 successful telemetry publications, while the recorder-linked PostgreSQL count was positive and no greater than 280. The table exposed 23 members; each of the three external descriptors said `DECLARED ONLY — NOT EXECUTED` and showed no connectivity label or percentage telemetry in its row. |
| Guarded reset | Two synchronous Confirm activations issued one reset POST. Durable readback retained the predecessor's selected audit ordinal, kept that predecessor EXHAUSTED with one run, linked a fresh PLANNED successor to it, and moved the current mission/run pointers to the accepted successor. |
| API replacement | When SSE capacity prevented a first accepted snapshot, restarting dashboard API produced `Runtime changed · reload required`, disabled Start and Reset, and exposed a document-reload control. Reload restored CONNECTED and READY. |
| Recorder loss | Stopping recorder produced degraded-live readiness 503, the typed `Recorder capture unavailable` blocker, and `Dashboard unavailable`; restarting it restored readiness 200, removed the blocker, and returned the browser to CONNECTED. |
| Caddy outage | Stopping only Caddy moved the browser through RETRYING to `Dashboard offline`; restoring Caddy produced `Connection recovered` and CONNECTED without a reload control. |
| Durable overload | Two bounded pressure producers each produced a durable receipt of 512 distinct events with sequences 0 through 511 while the same dashboard API container and PID stayed running. The browser showed overload/resynchronization, opened exactly one additional events request, and settled on the unchanged current PLANNED successor and audit ordinal. The shared broker and PostgreSQL IDs remained unchanged. |
| Replay | The browser reused one replay session across reload, exercised step, 2x, play, and pause, and produced one identical lowercase SHA-256 state digest across ten end seeks. It displayed `Verified`; a new replay session used a different replay URL and began at cursor zero. |

The overload workflow first exported and validated the retained EXHAUSTED predecessor. The normalized
recording contained between 1 and 512 events; its header and the isolated validator's replay output
reported the same lowercase SHA-256 final digest and well-formed checksum fields. That export preceded
the pressure publications, so those checksums do not cover the later pressure events. The visible
overload state and one added events request establish one observed resynchronization flow; this run did
not retain a raw SSE capture from which to count terminal frames independently.

After the soak, a focused readback of its committed synthetic mission found lifecycle `EXHAUSTED`, 14
completed ticks, 280 fleet-reported successful telemetry publications, 280 recorder telemetry receipts,
and 328 total audit events. The equality between publication and receipt happened in this local run. It
does **not** change telemetry's best-effort contract and is not a completeness guarantee for another run.

## Soak measurement

The soak began its resource window only after the synthetic mission reached EXHAUSTED and the dashboard
reported CONNECTED and READY. It took 61 browser/process samples at fixed 30-second targets, giving a
30-minute first-to-last observation window and 30.3-minute total Playwright duration.

All encoded acceptance relations passed:

- dashboard API container ID and PID were stable at every sample;
- maximum sampled RSS growth from the first post-connect sample was at most 64 MiB;
- maximum sampled open-file-descriptor growth from that baseline was at most 8;
- every browser sample was CONNECTED and READY, the map stayed visible, and the accessible alert count
  stayed zero;
- every sampled audit ordinal was a safe integer and the sequence was nondecreasing;
- the workflow observed zero remote-origin browser requests; and
- the shared broker and PostgreSQL container IDs remained equal before and after the run.

The successful line reporter did not retain the attached baseline/maximum JSON summary. This record
therefore reports the tested bounds, not invented baseline or maximum values. A separate post-soak point
sample observed dashboard API RSS of 114,425,856 bytes and 12 open file descriptors. That one point is
neither the soak baseline nor its maximum and cannot be used to derive either growth value.

This was a post-mission connected-transport soak, not 30 minutes of active fleet publication. The 30-second
cadence can miss shorter transients. It measured dashboard API process RSS and file descriptors only; it
did not measure browser, broker, PostgreSQL, or other-service memory, CPU, heap, response latency, or
throughput.

## Curated visual evidence

The six committed images use synthetic fixtures and mask runtime, mission/session, mutation-outcome, and
presentation-time values. They were inspected for the asserted layouts after the 64-case fixture run:

- [compact 1280x800 command center](../../apps/dashboard/tests/e2e/__screenshots__/responsive.spec.ts/compact-command-center-compact-1280-darwin.png)
- [connection recovered](../../apps/dashboard/tests/e2e/__screenshots__/visual.spec.ts/connection-recovered-command-center-desktop-1440-darwin.png)
- [contract failure](../../apps/dashboard/tests/e2e/__screenshots__/visual.spec.ts/contract-failure-command-center-desktop-1440-darwin.png)
- [degraded live simulation](../../apps/dashboard/tests/e2e/__screenshots__/visual.spec.ts/degraded-live-command-center-desktop-1440-darwin.png)
- [isolated replay](../../apps/dashboard/tests/e2e/__screenshots__/visual.spec.ts/isolated-replay-command-center-desktop-1440-darwin.png)
- [focused reset confirmation](../../apps/dashboard/tests/e2e/__screenshots__/visual.spec.ts/reset-confirmation-focused-desktop-1440-darwin.png)

These are deterministic visual-regression artifacts, not screenshots of the production run. The separate
in-app production inspection showed an EXHAUSTED replay at audit ordinal 48 and the browser's replay
digest status `Verified`; no unreviewed production screenshot was retained.

## What this run establishes

- The committed dashboard slice can extend the already-running `aerial-rescue-mesh` project and use its
  shared broker and PostgreSQL without replacing those containers or the running Agent Mesh container.
- The eight named production workflows crossed the loopback Caddy, Unix-socket dashboard API, shared
  PostgreSQL, private scenario/fleet control, local broker, recorder, and replay boundaries exercised by
  their assertions.
- The fixed synthetic workload completed its 14 ticks and fleet-owned 280-publication target, while the
  recorder count remained a separately interpreted best-effort observation.
- The browser visibly handled live degradation, terminal reset with retained history, stale API runtime,
  recorder readiness loss, relay outage, durable overload resynchronization, and isolated replay.
- The post-exhaustion dashboard transport and API process stayed within the selected 61-sample resource
  envelope on this host.
- The selected live store and broker-authorization suites passed against the same local shared runtime.

## What this run does not establish

- **No operational search-and-rescue claim.** This is a reference simulation over synthetic geometry and
  telemetry. It has no field validation, real aircraft, real sensor, or real-person input.
- **No 23-executable-member claim.** Exactly 20 deterministic simulations execute. The three external
  descriptors remain declared only and receive no executable dashboard control.
- **No complete rescue workflow.** This dashboard slice renders no Agent Mesh orchestration, model,
  evidence, approval, executable command, rescue, or escalation control. It neither invokes Ollama nor a
  paid provider.
- **No telemetry completeness guarantee.** The fleet's 280 count is its successful-publication counter.
  The recorder's observed 280 receipts in the soak mission do not make best-effort telemetry durable or
  lossless.
- **No Cloud or broader deployment claim.** Nothing contacted Solace Cloud, a provider, or another
  profile. Local shared-container identity does not establish Cloud parity, release-scale capacity, or
  complete broker authorization.
- **No full browser matrix from production Playwright.** The production and soak drivers used Chromium at
  1440x900 with reduced motion. The 1280x800 screenshot and other accessibility/visual states are fixture
  evidence; this run does not attribute 200% zoom, normal-motion behavior, or every keyboard/axe assertion
  to the production stack.
- **No cross-language replay claim from the production replay case alone.** That case performs ten
  browser folds. Python/TypeScript parity remains the separate deterministic integration oracle.
- **No active-workload soak.** Its measured window begins after mission exhaustion and samples at
  30-second intervals.

These limits preserve the current-slice boundary in
[`docs/LIMITATIONS.md`](../../docs/LIMITATIONS.md) and leave the full initial-release gaps in
[`docs/IMPLEMENTATION_PLAN.md`](../../docs/IMPLEMENTATION_PLAN.md) open.

## Final external state and cleanup

After the final browser and live probes, cleanup used the supported command:

```sh
just mission-control-down
```

It stopped only fleet simulator, scenario service, recorder, dashboard API, and Caddy. Migration and the
replay validator had already exited successfully. Broker, PostgreSQL, and Agent Mesh remained healthy,
and their container IDs still matched the pre-rebuild samples. No Compose `down`, volume removal, network
removal, or history deletion occurred. A post-cleanup PostgreSQL readback found 20 retained dashboard
missions, 8,921 retained audit events, and the single current-run pointer. Container inspection also
confirmed the `aerial-rescue-mesh` project label on the selected services, empty host-port maps on the
private scenario/fleet controls, and loopback-only broker/PostgreSQL host bindings.
