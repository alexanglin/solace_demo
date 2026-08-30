import { execFile } from "node:child_process";
import { randomUUID } from "node:crypto";
import { mkdir, mkdtemp, readFile, rm } from "node:fs/promises";
import { request, type ClientRequest, type IncomingMessage } from "node:http";
import { join, resolve } from "node:path";
import { tmpdir } from "node:os";
import { promisify } from "node:util";

import {
  parseDashboardProcessProbe,
  type DashboardProcessSample,
} from "../../soak/support/soak-policy";
import {
  isApplicationIdentifier,
  parseFleetCompletionEvidence,
  parseRecorderTelemetryReceiptCount,
  parseResetHistoryEvidence,
  type FleetCompletionEvidence,
  type ResetHistoryEvidence,
} from "./live-evidence";
import {
  assertSafeDashboardComposeOperation,
  sampleSharedDependencyContainers,
  sharedComposeProject,
  type SharedDependencyContainers,
} from "./shared-project-guard";

const execFileAsync = promisify(execFile);
const dashboardOrigin = "http://127.0.0.1:8080";
const repositoryRoot = resolve(import.meta.dirname, "../../../../../");
const containerPattern = /^[a-f0-9]{12,64}$/u;
const imagePattern = /^(?:sha256:)?[a-f0-9]{64}$/u;
const databaseIdentifierPattern = /^[a-z_][a-z0-9_]{0,62}$/u;
const sha256Pattern = /^[0-9a-f]{64}$/u;
const pressureIdentityPattern =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/u;
const sseCapacity = 8;
const pressureBatchCount = 2;
const pressureEventCount = 512;
const pressureReceiptDeadlineMilliseconds = 30_000;
const pressureTrustStorePath = "/etc/aerial-rescue/certs";
const pressureCredentialPath = "/run/secrets/fleet-broker-password";
const postgresCredentialPath = "/run/secrets/postgres-password";
const normalizedRecordingFilename = "wilderness-missing-person.r1.ndjson";
const validatedReplayFilename = "wilderness-missing-person.r1.replay.json";
const processProbe =
  "from pathlib import Path; lines=Path('/proc/1/status').read_text(encoding='utf-8').splitlines(); rss=next(line for line in lines if line.startswith('VmRSS:')); print(int(rss.split()[1])*1024, len(tuple(Path('/proc/1/fd').iterdir())))";
// ADR-0197 left one composition in the scenario service, so the probe reads its settings
// through the same entry point the deployed console does rather than reconstructing them.
// Mission identity is not re-checked here: `status` no longer takes an expected mission,
// and `parseFleetCompletionEvidence` already refuses a document whose identities disagree.
const fleetStatusProbe = [
  "import asyncio",
  "import os",
  "import sys",
  "from aerial_rescue_scenario_service.fleet_http import FleetHttpClient",
  "from aerial_rescue_scenario_service.service import settings_from_environment",
  "",
  "async def _status() -> None:",
  "    client = FleetHttpClient(settings_from_environment(os.environ).fleet)",
  "    await client.startup()",
  "    try:",
  "        status = await client.status(sys.argv[1])",
  "    finally:",
  "        await client.shutdown()",
  "    print(status.model_dump_json(by_alias=True))",
  "",
  "asyncio.run(_status())",
].join("\n");

export function buildTelemetryReceiptQuery(missionId: string, runId: string): string {
  if (!isApplicationIdentifier(missionId) || !isApplicationIdentifier(runId)) {
    throw new Error("live evidence identity was malformed");
  }
  return [
    "SELECT count(*)",
    "FROM dashboard_broker_event AS broker_event",
    "JOIN audit_record AS audit",
    "ON audit.mission_id = broker_event.audit_mission_id",
    "AND audit.ordinal = broker_event.audit_ordinal",
    "JOIN dashboard_run AS live_run",
    "ON live_run.mission_id = broker_event.audit_mission_id",
    `AND live_run.run_id = '${runId}'`,
    `WHERE broker_event.audit_mission_id = '${missionId}'`,
    // ADR-0205 fixes `audit_record.kind` as the committed envelope's own type. The browser
    // sees `droneTelemetry` only after `project()` turns that envelope into an event.
    "AND audit.kind = 'aerial-rescue.v1.drone.telemetry'",
  ].join(" ");
}

export function buildResetHistoryQuery(
  predecessorMissionId: string,
  successorMissionId: string,
  successorRunId: string,
  retainedAuditOrdinal: number,
): string {
  if (
    !isApplicationIdentifier(predecessorMissionId) ||
    !isApplicationIdentifier(successorMissionId) ||
    !isApplicationIdentifier(successorRunId) ||
    !Number.isSafeInteger(retainedAuditOrdinal) ||
    retainedAuditOrdinal < 1
  ) {
    throw new Error("reset history evidence input was malformed");
  }
  const predecessor = `'${predecessorMissionId}'`;
  const successor = `'${successorMissionId}'`;
  return [
    "SELECT jsonb_build_object(",
    `'auditEventCount', (SELECT count(*) FROM audit_record WHERE mission_id = ${predecessor}),`,
    "'currentMissionId', (SELECT current_run.mission_id FROM dashboard_current_run AS current_pointer",
    "JOIN dashboard_run AS current_run ON current_run.run_identity = current_pointer.run_identity",
    "WHERE current_pointer.singleton_key = 1),",
    "'currentRunId', (SELECT current_run.run_id FROM dashboard_current_run AS current_pointer",
    "JOIN dashboard_run AS current_run ON current_run.run_identity = current_pointer.run_identity",
    "WHERE current_pointer.singleton_key = 1),",
    `'latestAuditOrdinal', coalesce((SELECT max(ordinal) FROM audit_record WHERE mission_id = ${predecessor}), 0),`,
    `'predecessorLifecycle', (SELECT lifecycle FROM dashboard_mission WHERE mission_id = ${predecessor}),`,
    `'predecessorMissionId', (SELECT mission_id FROM dashboard_mission WHERE mission_id = ${predecessor}),`,
    `'predecessorRunCount', (SELECT count(*) FROM dashboard_run WHERE mission_id = ${predecessor}),`,
    `'retainedAuditOrdinal', (SELECT ordinal FROM audit_record WHERE mission_id = ${predecessor} AND ordinal = ${String(retainedAuditOrdinal)}),`,
    `'successorLifecycle', (SELECT lifecycle FROM dashboard_mission WHERE mission_id = ${successor}),`,
    `'successorMissionId', (SELECT mission_id FROM dashboard_mission WHERE mission_id = ${successor}),`,
    `'successorPredecessorMissionId', (SELECT predecessor_mission_id FROM dashboard_mission WHERE mission_id = ${successor}),`,
    `'successorRunId', (SELECT run_id FROM dashboard_run WHERE mission_id = ${successor} AND run_id = '${successorRunId}')`,
    ")",
  ].join(" ");
}

interface HeldSseConnection {
  readonly request: ClientRequest;
  readonly response: IncomingMessage;
}

class SseCapacityRefusal extends Error {}

export interface SseCapacityLease {
  release(): void;
}

export interface StreamPressureTarget {
  readonly droneId: string;
  readonly missionId: string;
  readonly pressureId: string;
  readonly runId: string;
}

export interface StreamPressureReceipt {
  readonly distinctEventCount: number;
  readonly eventCount: number;
  readonly maximumSequence: number;
  readonly minimumSequence: number;
}

export function pressureBatchTargets(
  target: StreamPressureTarget,
): readonly StreamPressureTarget[] {
  if (!pressureIdentityPattern.test(target.pressureId)) {
    throw new Error("pressure identity was malformed");
  }
  const finalDigit = Number.parseInt(target.pressureId.at(-1) ?? "", 16);
  return Array.from({ length: pressureBatchCount }, (_, index) =>
    index === 0
      ? target
      : {
          ...target,
          pressureId: `${target.pressureId.slice(0, -1)}${((finalDigit + index) % 16).toString(16)}`,
        },
  );
}

export interface RecordingValidationEvidence {
  readonly eventCount: number;
  readonly expectedFinalDigest: string;
  readonly recordingChecksum: string;
  readonly replayChecksum: string;
}

export interface LiveMissionEvidence {
  readonly bestEffortTelemetryReceiptCount: number;
  readonly fleet: FleetCompletionEvidence;
}

function objectDocument(value: unknown, label: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(`${label} was not an object`);
  }
  return value as Record<string, unknown>;
}

function lowercaseSha256(value: unknown, label: string): string {
  if (typeof value !== "string" || !sha256Pattern.test(value)) {
    throw new Error(`${label} was not a lowercase SHA-256 digest`);
  }
  return value;
}

function parseRecordingEvidence(
  normalizedRecording: string,
  validatedReplay: string,
): RecordingValidationEvidence {
  const firstLineEnd = normalizedRecording.indexOf("\n");
  if (firstLineEnd <= 0 || !normalizedRecording.endsWith("\n")) {
    throw new Error("normalized recording framing was malformed");
  }
  let headerValue: unknown;
  let replayValue: unknown;
  try {
    headerValue = JSON.parse(normalizedRecording.slice(0, firstLineEnd)) as unknown;
    replayValue = JSON.parse(validatedReplay) as unknown;
  } catch (error) {
    throw new Error("validated recording evidence was not JSON", { cause: error });
  }
  const header = objectDocument(headerValue, "normalized recording header");
  const replay = objectDocument(replayValue, "validated replay bundle");
  const integrity = objectDocument(replay["integrity"], "validated replay integrity");
  const events = replay["events"];
  const eventCount = header["eventCount"];
  if (
    !Number.isSafeInteger(eventCount) ||
    typeof eventCount !== "number" ||
    eventCount < 1 ||
    eventCount > 512 ||
    !Array.isArray(events) ||
    events.length !== eventCount
  ) {
    throw new Error("validated recording event count was malformed");
  }
  if (integrity["algorithm"] !== "sha256") {
    throw new Error("validated replay integrity algorithm was malformed");
  }
  const expectedFinalDigest = lowercaseSha256(
    header["expectedFinalDigest"],
    "recording final digest",
  );
  if (
    lowercaseSha256(integrity["expectedFinalDigest"], "replay final digest") !== expectedFinalDigest
  ) {
    throw new Error("recording and replay final digests diverged");
  }
  return {
    eventCount,
    expectedFinalDigest,
    recordingChecksum: lowercaseSha256(header["checksum"], "recording checksum"),
    replayChecksum: lowercaseSha256(integrity["checksum"], "replay checksum"),
  };
}

export class MissionControlRuntime {
  private readonly capacityLeases = new Set<SseCapacityLease>();
  private readonly project = sharedComposeProject;
  private readonly temporaryRoots = new Set<string>();
  private fleetSimulatorStopped = false;
  private publisherPaused = false;
  private publisherStopped = false;
  private recorderStopped = false;

  async holdSseCapacity(): Promise<SseCapacityLease> {
    const held: HeldSseConnection[] = [];
    let consecutiveRefusals = 0;
    while (consecutiveRefusals < 2) {
      try {
        held.push(await this.openSseConnection());
        consecutiveRefusals = 0;
        if (held.length > sseCapacity) {
          throw new Error("SSE capacity exceeded its configured bound");
        }
      } catch (error) {
        if (!(error instanceof SseCapacityRefusal)) {
          for (const connection of held) {
            connection.response.destroy();
            connection.request.destroy();
          }
          throw error;
        }
        consecutiveRefusals += 1;
        if (consecutiveRefusals < 2) {
          await new Promise((resolveDelay) => globalThis.setTimeout(resolveDelay, 100));
        }
      }
    }
    let released = false;
    const lease: SseCapacityLease = {
      release: () => {
        if (released) return;
        released = true;
        this.capacityLeases.delete(lease);
        for (const connection of held) {
          connection.response.destroy();
          connection.request.destroy();
        }
      },
    };
    this.capacityLeases.add(lease);
    return lease;
  }

  async restartDashboardApi(): Promise<void> {
    await this.compose("restart", "--timeout", "5", "dashboard-api");
    await this.waitForHealth();
  }

  async stopFleetSimulator(): Promise<void> {
    await this.compose("stop", "--timeout", "5", "fleet-simulator");
    this.fleetSimulatorStopped = true;
  }

  async startFleetSimulator(): Promise<void> {
    await this.compose("start", "fleet-simulator");
    await this.waitForServiceHealth("fleet-simulator");
    this.fleetSimulatorStopped = false;
  }

  async publishStreamPressure(target: StreamPressureTarget): Promise<StreamPressureReceipt> {
    this.validatePressureTarget(target);
    if (!this.publisherPaused || !this.fleetSimulatorStopped) {
      throw new Error("stream pressure requires paused Caddy and a stopped normal fleet publisher");
    }
    const image = await this.applicationImage("fleet-simulator");
    const trustStore = resolve(repositoryRoot, "deploy/certs");
    const credential = resolve(repositoryRoot, "deploy/secrets/broker-fleet-simulator-password");
    const batches = pressureBatchTargets(target);
    for (const batch of batches) {
      await execFileAsync(
        "docker",
        [
          "run",
          "--rm",
          "--name",
          `${this.project}-sse-pressure-${batch.pressureId.replaceAll("-", "")}`,
          "--network",
          `${this.project}_event-mesh`,
          "--read-only",
          "--user",
          this.runnerUser(),
          "--cap-drop",
          "ALL",
          "--security-opt",
          "no-new-privileges:true",
          "--tmpfs",
          "/tmp:rw,noexec,nosuid,size=16m,mode=1777",
          "--mount",
          `type=bind,src=${trustStore},dst=${pressureTrustStorePath},readonly`,
          "--mount",
          `type=bind,src=${credential},dst=${pressureCredentialPath},readonly`,
          "--env",
          "SOLACE_BROKER_URL=tcps://broker:55443",
          "--env",
          "SOLACE_BROKER_VPN=default",
          "--env",
          `TRUST_STORE=${pressureTrustStorePath}`,
          "--env",
          `SOLACE_BROKER_PASSWORD_FILE=${pressureCredentialPath}`,
          image,
          "/app/.venv/bin/python",
          "-m",
          "aerial_rescue_fleet_simulator.pressure",
          "--mission-id",
          batch.missionId,
          "--run-id",
          batch.runId,
          "--drone-id",
          batch.droneId,
          "--pressure-id",
          batch.pressureId,
          "--event-count",
          String(pressureEventCount),
        ],
        { cwd: repositoryRoot, maxBuffer: 1024, timeout: 120_000 },
      );
    }
    const receipts = await Promise.all(
      batches.map(async (batch) => this.waitForPressureReceipt(batch)),
    );
    const receipt = receipts[0];
    if (
      receipt === undefined ||
      receipts.some(
        (candidate) =>
          candidate.distinctEventCount !== receipt.distinctEventCount ||
          candidate.eventCount !== receipt.eventCount ||
          candidate.maximumSequence !== receipt.maximumSequence ||
          candidate.minimumSequence !== receipt.minimumSequence,
      )
    ) {
      throw new Error("stream pressure batch receipts disagreed");
    }
    return receipt;
  }

  async collectLiveMissionEvidence(missionId: string, runId: string): Promise<LiveMissionEvidence> {
    this.validateApplicationIdentities(missionId, runId);
    const fleetOutput = await this.composeOutput(
      "exec",
      "-T",
      "scenario-service",
      "/app/.venv/bin/python",
      "-c",
      fleetStatusProbe,
      runId,
    );
    const fleet = parseFleetCompletionEvidence(fleetOutput, missionId, runId);
    const telemetryReceiptQuery = buildTelemetryReceiptQuery(missionId, runId);
    const receiptOutput = await this.composeOutput(
      "exec",
      "-T",
      "postgres",
      "sh",
      "-ceu",
      'psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -c "$1"',
      "live-mission-receipt",
      telemetryReceiptQuery,
    );
    return {
      bestEffortTelemetryReceiptCount: parseRecorderTelemetryReceiptCount(receiptOutput),
      fleet,
    };
  }

  async collectResetHistoryEvidence(
    predecessorMissionId: string,
    successorMissionId: string,
    successorRunId: string,
    retainedAuditOrdinal: number,
  ): Promise<ResetHistoryEvidence> {
    const query = buildResetHistoryQuery(
      predecessorMissionId,
      successorMissionId,
      successorRunId,
      retainedAuditOrdinal,
    );
    const output = await this.composeOutput(
      "exec",
      "-T",
      "postgres",
      "sh",
      "-ceu",
      'psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -c "$1"',
      "reset-history",
      query,
    );
    return parseResetHistoryEvidence(
      output,
      predecessorMissionId,
      successorMissionId,
      successorRunId,
      retainedAuditOrdinal,
    );
  }

  async exportAndValidateRecording(
    missionId: string,
    runId: string,
  ): Promise<RecordingValidationEvidence> {
    this.validateApplicationIdentities(missionId, runId);
    const temporaryRoot = await mkdtemp(join(tmpdir(), "aerial-rescue-recording-e2e-"));
    this.temporaryRoots.add(temporaryRoot);
    const recordingOutput = join(temporaryRoot, "recording");
    const replayOutput = join(temporaryRoot, "replay");
    try {
      await Promise.all([
        mkdir(recordingOutput, { mode: 0o700 }),
        mkdir(replayOutput, { mode: 0o700 }),
      ]);
      const [image, database] = await Promise.all([
        this.applicationImage("recorder"),
        this.postgresIdentity(),
      ]);
      const postgresCredential = resolve(repositoryRoot, "deploy/secrets/postgres-password");
      const suffix = randomUUID().replaceAll("-", "");
      const exportResult = await execFileAsync(
        "docker",
        [
          "run",
          "--rm",
          "--name",
          `${this.project}-recording-export-${suffix}`,
          "--network",
          `${this.project}_store`,
          "--read-only",
          "--user",
          this.runnerUser(),
          "--cap-drop",
          "ALL",
          "--security-opt",
          "no-new-privileges:true",
          "--tmpfs",
          "/tmp:rw,noexec,nosuid,size=16m,mode=1777",
          "--mount",
          `type=bind,src=${postgresCredential},dst=${postgresCredentialPath},readonly`,
          "--mount",
          `type=bind,src=${recordingOutput},dst=/output`,
          "--env",
          `POSTGRES_USER=${database.user}`,
          "--env",
          `POSTGRES_DB=${database.database}`,
          "--env",
          `POSTGRES_PASSWORD_FILE=${postgresCredentialPath}`,
          image,
          "/app/.venv/bin/python",
          "-m",
          "aerial_rescue_recorder.exporter",
          "--mission-id",
          missionId,
          "--run-id",
          runId,
          "--output-directory",
          "/output",
        ],
        { cwd: repositoryRoot, maxBuffer: 1024, timeout: 120_000 },
      );
      if (exportResult.stdout.trim() !== "normalized recording ready") {
        throw new Error("recording exporter returned an unexpected success response");
      }
      const recordingPath = join(recordingOutput, normalizedRecordingFilename);
      const validatorResult = await execFileAsync(
        "docker",
        [
          "run",
          "--rm",
          "--name",
          `${this.project}-recording-validator-${suffix}`,
          "--network",
          "none",
          "--read-only",
          "--user",
          this.runnerUser(),
          "--cap-drop",
          "ALL",
          "--security-opt",
          "no-new-privileges:true",
          "--tmpfs",
          "/tmp:rw,noexec,nosuid,size=16m,mode=1777",
          "--mount",
          `type=bind,src=${recordingPath},dst=/input/${normalizedRecordingFilename},readonly`,
          "--mount",
          `type=bind,src=${replayOutput},dst=/output`,
          image,
          "/app/.venv/bin/python",
          "-m",
          "aerial_rescue_recorder.validator",
          "--input",
          `/input/${normalizedRecordingFilename}`,
          "--output-directory",
          "/output",
        ],
        { cwd: repositoryRoot, maxBuffer: 1024, timeout: 120_000 },
      );
      if (validatorResult.stdout.trim() !== "validated replay ready") {
        throw new Error("recording validator returned an unexpected success response");
      }
      const [normalizedRecording, validatedReplay] = await Promise.all([
        readFile(recordingPath, "utf8"),
        readFile(join(replayOutput, validatedReplayFilename), "utf8"),
      ]);
      return parseRecordingEvidence(normalizedRecording, validatedReplay);
    } catch (error) {
      try {
        await this.removeTemporaryRoot(temporaryRoot);
      } catch (cleanupError) {
        throw new AggregateError(
          [error, cleanupError],
          "recording export failed and its temporary artifact could not be removed",
          { cause: cleanupError },
        );
      }
      throw error;
    }
  }

  async sampleDashboardProcess(): Promise<DashboardProcessSample> {
    const containerId = (await this.composeOutput("ps", "--quiet", "dashboard-api")).trim();
    if (!containerPattern.test(containerId)) {
      throw new Error("dashboard API container identity was malformed");
    }
    const [pidResult, probeResult] = await Promise.all([
      execFileAsync("docker", ["inspect", "--format", "{{.State.Pid}}", containerId], {
        maxBuffer: 1024,
        timeout: 10_000,
      }),
      execFileAsync("docker", ["exec", containerId, "/app/.venv/bin/python", "-c", processProbe], {
        maxBuffer: 1024,
        timeout: 10_000,
      }),
    ]);
    const pid = Number(pidResult.stdout.trim());
    if (!Number.isSafeInteger(pid) || pid <= 0) {
      throw new Error("dashboard API process identity was malformed");
    }
    return {
      containerId,
      pid,
      ...parseDashboardProcessProbe(probeResult.stdout),
    };
  }

  async sampleSharedDependencyContainers(): Promise<SharedDependencyContainers> {
    return sampleSharedDependencyContainers();
  }

  async stopRecorder(): Promise<void> {
    await this.compose("stop", "--timeout", "5", "recorder");
    this.recorderStopped = true;
    await this.waitForDashboardReadiness(503);
  }

  async startRecorder(): Promise<void> {
    await this.compose("start", "recorder");
    this.recorderStopped = false;
    await this.waitForDashboardReadiness(200);
  }

  async stopPublisher(): Promise<void> {
    await this.compose("stop", "--timeout", "5", "caddy");
    this.publisherStopped = true;
  }

  async startPublisher(): Promise<void> {
    await this.compose("start", "caddy");
    this.publisherStopped = false;
    await this.waitForHealth();
  }

  async pausePublisher(): Promise<void> {
    await this.compose("pause", "caddy");
    this.publisherPaused = true;
  }

  async resumePublisher(): Promise<void> {
    await this.compose("unpause", "caddy");
    this.publisherPaused = false;
    await this.waitForHealth();
  }

  async restore(): Promise<void> {
    for (const lease of [...this.capacityLeases]) lease.release();
    const restorations: Promise<void>[] = [];
    if (this.fleetSimulatorStopped) restorations.push(this.startFleetSimulator());
    if (this.publisherPaused) restorations.push(this.resumePublisher());
    if (this.recorderStopped) restorations.push(this.startRecorder());
    if (this.publisherStopped) restorations.push(this.startPublisher());
    for (const temporaryRoot of this.temporaryRoots) {
      restorations.push(this.removeTemporaryRoot(temporaryRoot));
    }
    const outcomes = await Promise.allSettled(restorations);
    const failures: Error[] = [];
    for (const outcome of outcomes) {
      if (outcome.status !== "rejected") continue;
      failures.push(
        outcome.reason instanceof Error
          ? outcome.reason
          : new Error("mission-control restore failed without an Error"),
      );
    }
    if (failures.length > 0) throw new AggregateError(failures, "mission-control restore failed");
  }

  async waitForDashboardReadiness(expectedStatus: 200 | 503): Promise<void> {
    const deadline = Date.now() + 30_000;
    while (Date.now() < deadline) {
      try {
        const response = await fetch(`${dashboardOrigin}/api/v1/readiness?mode=degradedLive`, {
          cache: "no-store",
        });
        if (response.status === expectedStatus) return;
      } catch {
        // The selected dependency is changing state; the bounded deadline remains authoritative.
      }
      await new Promise((resolve) => globalThis.setTimeout(resolve, 250));
    }
    throw new Error(
      `mission-control dashboard readiness did not reach ${String(expectedStatus)} within 30 seconds`,
    );
  }

  private async compose(...operation: readonly string[]): Promise<void> {
    await this.composeOutput(...operation);
  }

  private async composeOutput(...operation: readonly string[]): Promise<string> {
    assertSafeDashboardComposeOperation(operation);
    const result = await execFileAsync(
      "docker",
      [
        "compose",
        "--project-name",
        this.project,
        "--env-file",
        ".env",
        "--env-file",
        "deploy/secrets/.env.roles",
        "-f",
        "deploy/compose.yaml",
        "--profile",
        "mission-control",
        ...operation,
      ],
      { cwd: repositoryRoot, maxBuffer: 1024 * 1024, timeout: 60_000 },
    );
    return result.stdout;
  }

  private validatePressureTarget(target: StreamPressureTarget): void {
    if (
      !isApplicationIdentifier(target.missionId) ||
      !isApplicationIdentifier(target.runId) ||
      !isApplicationIdentifier(target.droneId) ||
      !pressureIdentityPattern.test(target.pressureId)
    ) {
      throw new Error("stream pressure target identity was malformed");
    }
  }

  private validateApplicationIdentities(...identifiers: readonly string[]): void {
    if (identifiers.some((identifier) => !isApplicationIdentifier(identifier))) {
      throw new Error("application identity was malformed");
    }
  }

  private async applicationImage(service: string): Promise<string> {
    const image = (await this.composeOutput("images", "--quiet", service)).trim();
    if (!imagePattern.test(image)) throw new Error("application image identity was malformed");
    return image;
  }

  private runnerUser(): string {
    const userId = process.getuid?.();
    const groupId = process.getgid?.();
    if (
      !Number.isSafeInteger(userId) ||
      !Number.isSafeInteger(groupId) ||
      userId === undefined ||
      groupId === undefined ||
      userId <= 0 ||
      groupId < 0
    ) {
      throw new Error("production E2E runner must have a non-root POSIX identity");
    }
    return `${String(userId)}:${String(groupId)}`;
  }

  private async postgresIdentity(): Promise<{ database: string; user: string }> {
    const output = await this.composeOutput(
      "exec",
      "-T",
      "postgres",
      "sh",
      "-ceu",
      'printf "%s|%s\\n" "$POSTGRES_USER" "$POSTGRES_DB"',
    );
    const [user, database, ...rest] = output.trim().split("|");
    if (
      rest.length > 0 ||
      user === undefined ||
      database === undefined ||
      !databaseIdentifierPattern.test(user) ||
      !databaseIdentifierPattern.test(database)
    ) {
      throw new Error("PostgreSQL application identity was malformed");
    }
    return { database, user };
  }

  private async removeTemporaryRoot(temporaryRoot: string): Promise<void> {
    await rm(temporaryRoot, { force: true, recursive: true });
    this.temporaryRoots.delete(temporaryRoot);
  }

  private async waitForPressureReceipt(
    target: StreamPressureTarget,
  ): Promise<StreamPressureReceipt> {
    const source = `urn:aerial-rescue:connectivity-lifecycle:pressure-${target.pressureId.replaceAll("-", "")}`;
    const query =
      "SELECT count(*), count(DISTINCT event_id), " +
      "coalesce(min(source_sequence), -1), coalesce(max(source_sequence), -1) " +
      "FROM dashboard_broker_event " +
      `WHERE source = '${source}' AND audit_mission_id = '${target.missionId}'`;
    const deadline = Date.now() + pressureReceiptDeadlineMilliseconds;
    while (Date.now() < deadline) {
      const output = await this.composeOutput(
        "exec",
        "-T",
        "postgres",
        "sh",
        "-ceu",
        'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -F "|" -c "$1"',
        "pressure-receipt",
        query,
      );
      const values = output.trim().split("|").map(Number);
      if (values.length !== 4 || values.some((value) => !Number.isSafeInteger(value))) {
        throw new Error("stream pressure durable receipt was malformed");
      }
      const [eventCount, distinctEventCount, minimumSequence, maximumSequence] = values;
      if (
        eventCount === pressureEventCount &&
        distinctEventCount === pressureEventCount &&
        minimumSequence === 0 &&
        maximumSequence === pressureEventCount - 1
      ) {
        return { distinctEventCount, eventCount, maximumSequence, minimumSequence };
      }
      await new Promise((resolveDelay) => globalThis.setTimeout(resolveDelay, 250));
    }
    throw new Error("stream pressure was not durably recorder-linked within 30 seconds");
  }

  private async waitForServiceHealth(service: string): Promise<void> {
    const deadline = Date.now() + 30_000;
    while (Date.now() < deadline) {
      const containerId = (await this.composeOutput("ps", "--quiet", service)).trim();
      if (containerPattern.test(containerId)) {
        const result = await execFileAsync(
          "docker",
          [
            "inspect",
            "--format",
            "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}",
            containerId,
          ],
          { maxBuffer: 1024, timeout: 10_000 },
        );
        if (result.stdout.trim() === "healthy") return;
      }
      await new Promise((resolveDelay) => globalThis.setTimeout(resolveDelay, 250));
    }
    throw new Error(`${service} did not become healthy within 30 seconds`);
  }

  private openSseConnection(): Promise<HeldSseConnection> {
    return new Promise((resolve, reject) => {
      const connection = request(
        `${dashboardOrigin}/api/v1/events`,
        { headers: { Accept: "text/event-stream" }, method: "GET" },
        (response) => {
          if (response.statusCode !== 200) {
            response.destroy();
            reject(
              response.statusCode === 503
                ? new SseCapacityRefusal("SSE capacity is full")
                : new Error(`SSE capacity holder received ${String(response.statusCode)}`),
            );
            return;
          }
          response.once("data", () => {
            connection.setTimeout(0);
            resolve({ request: connection, response });
          });
        },
      );
      connection.once("error", reject);
      connection.setTimeout(20_000, () => {
        connection.destroy(new Error("SSE capacity holder timed out"));
      });
      connection.end();
    });
  }

  private async waitForHealth(): Promise<void> {
    const deadline = Date.now() + 30_000;
    while (Date.now() < deadline) {
      try {
        const response = await fetch(`${dashboardOrigin}/api/v1/health`, { cache: "no-store" });
        if (response.status === 200) return;
      } catch {
        // The publisher or API is still starting; the bounded deadline remains authoritative.
      }
      await new Promise((resolve) => globalThis.setTimeout(resolve, 250));
    }
    throw new Error("mission-control dashboard health did not recover within 30 seconds");
  }
}
