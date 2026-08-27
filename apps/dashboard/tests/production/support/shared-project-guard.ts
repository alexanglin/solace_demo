import { execFile } from "node:child_process";
import { resolve } from "node:path";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);
const repositoryRoot = resolve(import.meta.dirname, "../../../../../");
const containerPattern = /^[a-f0-9]{12,64}$/u;
const dashboardLifecycleServices = new Set([
  "caddy",
  "dashboard-api",
  "fleet-simulator",
  "migration",
  "recorder",
  "replay-validator",
  "scenario-service",
]);
const lifecycleOperations = new Set(["pause", "restart", "start", "stop", "unpause"]);
const readOnlyOperations = new Set(["exec", "images", "ps"]);
const forbiddenOperations = new Set(["create", "down", "kill", "rm", "run", "up"]);
const forbiddenOptions = new Set(["--remove-orphans", "--volumes", "-v"]);

export const sharedComposeProject = "aerial-rescue-mesh";

export interface SharedDependencyContainers {
  readonly broker: string;
  readonly postgres: string;
}

function composeArguments(operation: readonly string[]): string[] {
  return [
    "compose",
    "--project-name",
    sharedComposeProject,
    "--env-file",
    ".env",
    "--env-file",
    "deploy/secrets/.env.roles",
    "-f",
    "deploy/compose.yaml",
    "--profile",
    "mission-control",
    ...operation,
  ];
}

export function assertSafeDashboardComposeOperation(operation: readonly string[]): void {
  const verb = operation[0];
  if (verb === undefined || forbiddenOperations.has(verb)) {
    throw new Error("production E2E may not create or remove Compose resources");
  }
  if (operation.some((value) => forbiddenOptions.has(value))) {
    throw new Error("production E2E may not remove shared Compose volumes or orphans");
  }
  if (!lifecycleOperations.has(verb)) {
    if (!readOnlyOperations.has(verb)) {
      throw new Error("production E2E Compose operation is not allowlisted");
    }
    return;
  }
  const target =
    (verb === "start" || verb === "pause" || verb === "unpause") && operation.length === 2
      ? operation[1]
      : (verb === "stop" || verb === "restart") &&
          operation.length === 4 &&
          operation[1] === "--timeout" &&
          /^\d+$/u.test(operation[2] ?? "")
        ? operation[3]
        : undefined;
  if (target === undefined || !dashboardLifecycleServices.has(target)) {
    throw new Error("production E2E lifecycle controls are limited to dashboard services");
  }
}

async function composeOutput(...operation: readonly string[]): Promise<string> {
  const result = await execFileAsync("docker", composeArguments(operation), {
    cwd: repositoryRoot,
    maxBuffer: 1024 * 1024,
    timeout: 60_000,
  });
  return result.stdout;
}

export async function sampleSharedDependencyContainers(): Promise<SharedDependencyContainers> {
  const [broker, postgres] = await Promise.all([
    composeOutput("ps", "--quiet", "broker").then((output) => output.trim()),
    composeOutput("ps", "--quiet", "postgres").then((output) => output.trim()),
  ]);
  if (!containerPattern.test(broker) || !containerPattern.test(postgres) || broker === postgres) {
    throw new Error("shared broker/PostgreSQL containers are not running in aerial-rescue-mesh");
  }
  return { broker, postgres };
}

export default async function guardSharedProject(): Promise<() => Promise<void>> {
  const initial = await sampleSharedDependencyContainers();
  return async () => {
    const final = await sampleSharedDependencyContainers();
    if (final.broker !== initial.broker || final.postgres !== initial.postgres) {
      throw new Error("production E2E replaced a shared broker or PostgreSQL container");
    }
  };
}
