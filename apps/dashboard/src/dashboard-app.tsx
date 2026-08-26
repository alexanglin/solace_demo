import { createPortal } from "react-dom";
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

import { DashboardMutationClient, type DashboardMutationResult } from "./api/mutation-client";
import { decodeCanonicalJson, parseDashboardBootstrap } from "./contracts/bootstrap";
import type {
  DashboardReadiness,
  DashboardReducedState,
  DashboardReplayBundle,
  DashboardScenarioCatalog,
  DashboardSnapshot,
  OrderedDashboardEvent,
} from "./contracts/generated";
import { createDashboardSchemaRegistry, type DashboardSchemaId } from "./contracts/schema-registry";
import { canonicalBytes, replayStateDigest } from "./domain/canonical";
import {
  checkpointFromReplayBundle,
  emptyReducerCheckpoint,
  foldOrderedDashboardEvent,
  type ReducerCheckpoint,
} from "./domain/reducer";
import { appendMeaningfulTimelineEvent, type MissionTimeline } from "./domain/timeline";
import { FleetTable, type FleetFilter } from "./components/fleet-table";
import { SearchMap } from "./components/search-map";
import type { DashboardSourceInput } from "./sources/event-source";
import {
  ProductionDashboardRuntime,
  type ProductionDashboardRuntimeOptions,
  type ProductionRuntimePort,
} from "./sources/production-runtime";
import { DashboardSourceSession, type DashboardSourceSessionState } from "./sources/source-session";

const READINESS_SCHEMA_ID =
  "https://aerial-rescue.invalid/schemas/v1/dashboard/readiness.schema.json";
const SCENARIO_CATALOG_SCHEMA_ID =
  "https://aerial-rescue.invalid/schemas/v1/dashboard/scenario-catalog.schema.json";
const REPLAY_BUNDLE_SCHEMA_ID =
  "https://aerial-rescue.invalid/schemas/v1/dashboard/replay-bundle.schema.json";
const DASHBOARD_OVERLOAD_NOTICE_MILLISECONDS = 1_000;
const registry = createDashboardSchemaRegistry();

export interface DashboardApplicationProps {
  readonly productionBootstrap?: DashboardSourceInput;
  readonly productionRuntimeFactory?: (
    options: ProductionDashboardRuntimeOptions,
  ) => ProductionRuntimePort;
}

function defaultProductionRuntime(
  options: ProductionDashboardRuntimeOptions,
): ProductionRuntimePort {
  return new ProductionDashboardRuntime(options);
}

interface ServerOwnerState {
  readonly alert: string | null;
  readonly catalog: DashboardScenarioCatalog | null;
  readonly mutationOutcome: string | null;
  readonly operationPending: "reset" | "start" | null;
  readonly readiness: DashboardReadiness | null;
  readonly replay: ReplayOwnerState | null;
  readonly replayInvalid: boolean;
}

interface ReplayOwnerState {
  readonly bundle: DashboardReplayBundle;
  readonly checkpoints: readonly ReplayCheckpoint[];
  readonly cursor: number;
  readonly playing: boolean;
  readonly speed: 0.5 | 1 | 2;
  readonly verification: "Pending" | "Refused" | "Verified";
}

interface ReplayCheckpoint {
  readonly checkpoint: ReducerCheckpoint;
  readonly digest: string;
  readonly timeline: MissionTimeline;
}

type SelectedScenario = DashboardScenarioCatalog["scenarios"][number];
type ReplayPreparationResult = ReplayOwnerState | "scenario-mismatch" | null;

class SnapshotRequestDispatcher {
  private handler: (() => void) | null = null;

  connect(handler: () => void): void {
    this.handler = handler;
  }

  disconnect(handler: () => void): void {
    if (this.handler === handler) this.handler = null;
  }

  request(): void {
    this.handler?.();
  }
}

class SnapshotRunDispatcher {
  private handler: ((currentRun: DashboardSnapshot["currentRun"]) => void) | null = null;

  connect(handler: (currentRun: DashboardSnapshot["currentRun"]) => void): void {
    this.handler = handler;
  }

  disconnect(handler: (currentRun: DashboardSnapshot["currentRun"]) => void): void {
    if (this.handler === handler) this.handler = null;
  }

  observe(currentRun: DashboardSnapshot["currentRun"]): void {
    this.handler?.(currentRun);
  }
}

class OverloadNoticeController {
  private readonly publish: (active: boolean) => void;
  private timeout: number | null = null;

  constructor(publish: (active: boolean) => void) {
    this.publish = publish;
  }

  observe(status: DashboardSourceSessionState["server"]["status"]): void {
    if (status !== "resynchronizing") return;
    if (this.timeout !== null) window.clearTimeout(this.timeout);
    this.publish(true);
    this.timeout = window.setTimeout(() => {
      this.timeout = null;
      this.publish(false);
    }, DASHBOARD_OVERLOAD_NOTICE_MILLISECONDS);
  }

  dispose(): void {
    if (this.timeout !== null) window.clearTimeout(this.timeout);
  }
}

class ScenarioCatalogAnchor {
  private catalog: DashboardScenarioCatalog | null = null;

  accept(catalog: DashboardScenarioCatalog): void {
    this.catalog = catalog;
  }

  selected(): SelectedScenario | null {
    return this.catalog?.scenarios[0] ?? null;
  }
}

function initialServerState(): ServerOwnerState {
  return {
    alert: null,
    catalog: null,
    mutationOutcome: null,
    operationPending: null,
    readiness: null,
    replay: null,
    replayInvalid: false,
  };
}

function initialSourceState(): DashboardSourceSessionState {
  return {
    mission: { checkpoint: emptyReducerCheckpoint(), timeline: [] },
    server: {
      currentRun: null,
      refusal: null,
      runtimeId: null,
      status: "idle",
    },
  };
}

function contractAlert(boundary: string): string {
  return `Contract validation failed · ${boundary}`;
}

function readinessReasonLabel(reason: string): string {
  const words = reason.replaceAll("-", " ");
  return `${words[0]?.toUpperCase() ?? ""}${words.slice(1)}`;
}

function sourceAlert(state: DashboardSourceSessionState): string | null {
  if (state.server.status === "modeMismatch") {
    return state.server.currentRun?.mode === "replay"
      ? "Replay session is current · start a live mission to switch"
      : "Live mission is current · start replay to switch";
  }
  if (state.server.status === "runtimeChanged") return "Runtime changed · reload required";
  const refusal = state.server.refusal;
  if (refusal === null) return null;
  if (refusal.code === "REDUCER_REFUSED") {
    if (refusal.reducerCode === "ORDINAL_GAP") return "Audit ordinal gap";
    if (
      refusal.reducerCode === "ORDINAL_REGRESSION" ||
      refusal.reducerCode === "ORDINAL_DIVERGENCE"
    ) {
      return "Audit ordinal regression";
    }
    if (refusal.reducerCode === "SERVER_DIGEST_MISMATCH") return "State digest divergence";
  }
  return contractAlert(refusal.inputName);
}

function validatedDocument<SchemaId extends DashboardSchemaId>(
  input: DashboardSourceInput,
  schemaId: SchemaId,
): ReturnType<typeof registry.validate<SchemaId>> | null {
  const decoded = decodeCanonicalJson(input.raw);
  if (!decoded.ok) return null;
  return registry.validate(schemaId, decoded.value);
}

async function sha256(value: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new Uint8Array(value).buffer);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function checksumMaterial(bundle: DashboardReplayBundle): unknown {
  return {
    ...bundle,
    integrity: {
      algorithm: bundle.integrity.algorithm,
      expectedFinalDigest: bundle.integrity.expectedFinalDigest,
      integrityVersion: bundle.integrity.integrityVersion,
    },
  };
}

async function prepareReplay(
  bundle: DashboardReplayBundle,
  scenario: SelectedScenario | null,
): Promise<ReplayPreparationResult> {
  if (scenario?.identifier !== bundle.scenarioId) {
    return "scenario-mismatch";
  }
  const checksum = await sha256(canonicalBytes(checksumMaterial(bundle)));
  if (checksum !== bundle.integrity.checksum) return null;
  const anchored = checkpointFromReplayBundle(bundle);
  if (!anchored.ok) return null;
  const checkpoints: ReplayCheckpoint[] = [
    {
      checkpoint: anchored.checkpoint,
      digest: await replayStateDigest(anchored.checkpoint.state),
      timeline: [],
    },
  ];
  for (const orderedEvent of bundle.events) {
    const previous = checkpoints.at(-1);
    if (previous === undefined) return null;
    const folded = await foldOrderedDashboardEvent(previous.checkpoint, orderedEvent);
    if (!folded.ok) return null;
    checkpoints.push({
      checkpoint: folded.checkpoint,
      digest: await replayStateDigest(folded.checkpoint.state),
      timeline: appendMeaningfulTimelineEvent(previous.timeline, orderedEvent),
    });
  }
  return {
    bundle,
    checkpoints,
    cursor: 0,
    playing: false,
    speed: 1,
    verification: "Pending",
  };
}

function dashboardStateLabel(
  server: ServerOwnerState,
  source: DashboardSourceSessionState,
  overloadNoticeActive: boolean,
): string {
  if (source.server.status === "modeMismatch") {
    return source.server.currentRun?.mode === "replay"
      ? "Replay session active · start live mission to switch"
      : "Live mission active · start replay to switch";
  }
  if (overloadNoticeActive || source.server.status === "resynchronizing") {
    return "Stream overloaded · resynchronizing";
  }
  if (source.server.status === "runtimeChanged") return "Runtime changed · reload required";
  if (source.server.refusal !== null || server.alert?.startsWith("Contract validation failed")) {
    return "Contract validation failed";
  }
  if (source.server.status === "offline") return "Dashboard offline";
  if (source.server.status === "disconnected") return "Connection interrupted · retrying";
  if (source.server.status === "recovered") return "Connection recovered";
  if (server.operationPending === "start") return "Starting wilderness mission";
  if (server.operationPending === "reset") return "Resetting mission";
  if (server.readiness === null || server.catalog === null) return "Loading scenario catalog";
  if (server.catalog.scenarios.length === 0) return "No scenarios available";
  if (!server.readiness.ready) return "Dashboard unavailable";
  if (server.readiness.mode === "replay")
    return server.replayInvalid ? "Contract validation failed" : "Replay ready";
  const lifecycle = source.mission.checkpoint.state.currentMission?.lifecycle;
  if (lifecycle === "SEARCHING") return "Mission searching";
  if (lifecycle === "EXHAUSTED") return "Mission exhausted";
  if (lifecycle === "ABORTED") return "Mission aborted";
  return "Ready to start";
}

function connectionLabel(source: DashboardSourceSessionState, replayReady: boolean): string {
  if (source.server.status === "offline") return "OFFLINE";
  if (source.server.status === "disconnected") return "RETRYING";
  if (source.server.status === "runtimeChanged") return "STALE RUNTIME";
  if (source.server.status === "modeMismatch") return "AWAITING LIVE SNAPSHOT";
  if (replayReady) return "REPLAY READY";
  return source.server.status === "idle" || source.server.status === "connecting"
    ? "CONNECTING"
    : "CONNECTED";
}

function timelineLabel(ordered: OrderedDashboardEvent): string {
  const event = ordered.event as Exclude<
    OrderedDashboardEvent["event"],
    { kind: "droneTelemetry" }
  >;
  if (event.kind === "missionLifecycle") return `Mission · ${event.data.lifecycle}`;
  if (event.kind === "connectivityChanged") {
    return `${event.data.droneId} · ${event.data.connectivity}`;
  }
  return `${event.data.sectorId} · ${event.data.state.replace("_", " ")}`;
}

function applyReplayCursor(replay: ReplayOwnerState, cursor: number): ReplayOwnerState {
  const bounded = Math.max(0, Math.min(cursor, replay.checkpoints.length - 1));
  const atEnd = bounded === replay.checkpoints.length - 1;
  const digest = replay.checkpoints[bounded]?.digest;
  const verification = atEnd
    ? digest === replay.bundle.integrity.expectedFinalDigest
      ? "Verified"
      : "Refused"
    : "Pending";
  return { ...replay, cursor: bounded, playing: atEnd ? false : replay.playing, verification };
}

function ResetDialog({
  kind,
  onCancel,
  onConfirm,
}: {
  readonly kind: "live" | "replay";
  readonly onCancel: () => void;
  readonly onConfirm: () => void;
}): React.JSX.Element {
  const dialogRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const dialog = dialogRef.current;
    const buttons = dialog?.querySelectorAll<HTMLButtonElement>("button");
    buttons?.[0]?.focus();
    function onKeyDown(event: KeyboardEvent): void {
      if (event.key === "Escape") {
        event.preventDefault();
        onCancel();
        return;
      }
      if (event.key !== "Tab" || buttons === undefined || buttons.length === 0) return;
      const first = buttons[0];
      const last = buttons[buttons.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [onCancel]);
  const replayReset = kind === "replay";
  return createPortal(
    <div className="dialog-backdrop">
      <div
        aria-labelledby="reset-dialog-title"
        aria-modal="true"
        className="reset-dialog"
        ref={dialogRef}
        role="dialog"
      >
        <h2 id="reset-dialog-title">
          {replayReset ? "Start a new replay session" : "Reset current mission"}
        </h2>
        {replayReset ? (
          <p>
            This creates a fresh cursor-zero replay session and retains the recorded history. It
            does not mutate an operational mission.
          </p>
        ) : (
          <p>
            This will cancel the current run, retain its complete history, and create a fresh
            planned successor.
          </p>
        )}
        <div className="dialog-actions">
          <button onClick={onConfirm} type="button">
            {replayReset ? "Create new replay session" : "Confirm reset"}
          </button>
          <button onClick={onCancel} type="button">
            {replayReset ? "Keep current replay" : "Cancel reset"}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

export function DashboardApplication({
  productionBootstrap,
  productionRuntimeFactory = defaultProductionRuntime,
}: DashboardApplicationProps = {}): React.JSX.Element {
  const [server, setServer] = useState<ServerOwnerState>(initialServerState);
  const [catalogAnchor] = useState(() => new ScenarioCatalogAnchor());
  const [source, setSource] = useState<DashboardSourceSessionState>(initialSourceState);
  const [filter, setFilter] = useState<FleetFilter>("All");
  const [selected, setSelected] = useState<string | null>(null);
  const [fleetCollapsed, setFleetCollapsed] = useState(false);
  const [resetDialogOpen, setResetDialogOpen] = useState(false);
  const [markerSample, setMarkerSample] = useState("Waiting for a new position sample");
  const [liveDigest, setLiveDigest] = useState("");
  const [mutationClient, setMutationClient] = useState<DashboardMutationClient | null>(null);
  const [mutationLocked, setMutationLocked] = useState(false);
  const [overloadNoticeActive, setOverloadNoticeActive] = useState(false);
  const [overloadNoticeController] = useState(
    () => new OverloadNoticeController(setOverloadNoticeActive),
  );
  const restoreResetFocusRef = useRef(false);
  const productionRuntimeRef = useRef<ProductionRuntimePort | null>(null);
  const [snapshotRequests] = useState(() => new SnapshotRequestDispatcher());
  const [snapshotRuns] = useState(() => new SnapshotRunDispatcher());
  const resetButtonRef = useRef<HTMLButtonElement>(null);

  const consumeUnhandledInput = useCallback(
    async (input: DashboardSourceInput): Promise<void> => {
      if (input.channel === "bootstrap" && input.name === "bootstrap") {
        const parsed = parseDashboardBootstrap(input.raw);
        if (!parsed.ok) {
          setMutationClient((current) => {
            current?.lockStaleRuntime();
            return null;
          });
          setMutationLocked(true);
          setServer((current) => ({ ...current, alert: contractAlert("bootstrap") }));
          return;
        }
        setMutationClient(new DashboardMutationClient({ bearer: parsed.value.bearer }));
        setMutationLocked(false);
        setServer(initialServerState());
        return;
      }
      if (input.channel === "http-response" && input.name === "readiness") {
        const validated = validatedDocument(input, READINESS_SCHEMA_ID);
        if (validated?.ok !== true) {
          setServer((current) => ({ ...current, alert: contractAlert("readiness") }));
          return;
        }
        setServer((current) => ({ ...current, readiness: validated.value }));
        return;
      }
      if (input.channel === "http-response" && input.name === "scenario-catalog") {
        const validated = validatedDocument(input, SCENARIO_CATALOG_SCHEMA_ID);
        if (validated?.ok !== true) {
          setServer((current) => ({ ...current, alert: contractAlert("scenario catalog") }));
          return;
        }
        catalogAnchor.accept(validated.value);
        setServer((current) => ({ ...current, catalog: validated.value }));
        return;
      }
      if (import.meta.env.MODE === "test" && input.channel === "mutation-result") {
        const decoded = decodeCanonicalJson(input.raw);
        const candidate =
          decoded.ok && decoded.value !== null && typeof decoded.value === "object"
            ? (decoded.value as Record<string, unknown>)
            : null;
        if (
          !decoded.ok ||
          candidate === null ||
          (candidate["operation"] !== "start" && candidate["operation"] !== "reset") ||
          candidate["phase"] !== "pending"
        ) {
          setServer((current) => ({ ...current, alert: contractAlert("mutation result") }));
          return;
        }
        setServer((current) => ({
          ...current,
          operationPending: candidate["operation"] as "reset" | "start",
        }));
        return;
      }
      if (input.channel === "replay-bundle" && input.name === "validated-replay-bundle") {
        const validated = validatedDocument(input, REPLAY_BUNDLE_SCHEMA_ID);
        if (validated?.ok !== true) {
          setServer((current) => ({
            ...current,
            alert: contractAlert("replay bundle"),
            replay: null,
            replayInvalid: true,
          }));
          return;
        }
        const replay = await prepareReplay(validated.value, catalogAnchor.selected());
        if (replay === "scenario-mismatch") {
          setServer((current) => ({
            ...current,
            alert: contractAlert("replay bundle"),
            replay: null,
            replayInvalid: true,
          }));
          return;
        }
        setServer((current) =>
          replay === null
            ? {
                ...current,
                alert: "Replay bundle integrity check failed",
                replay: null,
                replayInvalid: true,
              }
            : { ...current, alert: null, replay, replayInvalid: false },
        );
        return;
      }
      setServer((current) => ({ ...current, alert: contractAlert(input.name) }));
    },
    [catalogAnchor],
  );

  const [session] = useState(
    () =>
      new DashboardSourceSession({
        consumeUnhandledInput,
        onState: (state) => {
          overloadNoticeController.observe(state.server.status);
          if (state.server.status === "runtimeChanged") {
            setMutationClient((current) => {
              current?.lockStaleRuntime();
              return current;
            });
            setMutationLocked(true);
          }
          setSource(state);
          if (state.server.status === "connected" || state.server.status === "modeMismatch") {
            snapshotRuns.observe(state.server.currentRun);
          }
        },
        requestSnapshot: () => {
          snapshotRequests.request();
        },
      }),
  );

  useEffect(() => {
    if (productionBootstrap === undefined) return;
    const runtime = productionRuntimeFactory({
      bootstrap: productionBootstrap,
      consumeBoundary: consumeUnhandledInput,
      session,
    });
    const requestSnapshot = (): void => {
      runtime.resnapshot();
    };
    const observeSnapshotRun = (currentRun: DashboardSnapshot["currentRun"]): void => {
      runtime.observeSnapshotRun(currentRun);
    };
    productionRuntimeRef.current = runtime;
    snapshotRequests.connect(requestSnapshot);
    snapshotRuns.connect(observeSnapshotRun);
    void runtime.start();
    return () => {
      if (productionRuntimeRef.current === runtime) productionRuntimeRef.current = null;
      snapshotRequests.disconnect(requestSnapshot);
      snapshotRuns.disconnect(observeSnapshotRun);
      runtime.dispose();
    };
  }, [
    consumeUnhandledInput,
    productionBootstrap,
    productionRuntimeFactory,
    session,
    snapshotRequests,
    snapshotRuns,
  ]);

  useEffect(() => {
    if (productionBootstrap !== undefined) return;
    if (import.meta.env.MODE !== "test") return;
    const harness = window.__AERIAL_RESCUE_DASHBOARD_TEST__;
    if (harness === undefined) return;
    if (harness.sourceScript?.inputs.length === 0) {
      harness.sourceScript = null;
      harness.appliedRevision = harness.sourceRevision;
      return;
    }
    let cancelled = false;
    let requestSnapshot: (() => void) | null = null;
    void import("./sources/test-fixture-source").then(({ TestFixtureSource }) => {
      if (cancelled) return;
      const fixtureSource = new TestFixtureSource(window, () => {
        setServer((current) => ({ ...current, alert: contractAlert("test source") }));
      });
      requestSnapshot = (): void => {
        fixtureSource.recordSnapshotRequest();
      };
      snapshotRequests.connect(requestSnapshot);
      session.replaceSource(fixtureSource);
    });
    return () => {
      cancelled = true;
      if (requestSnapshot !== null) snapshotRequests.disconnect(requestSnapshot);
    };
  }, [productionBootstrap, session, snapshotRequests]);

  useEffect(
    () => () => {
      overloadNoticeController.dispose();
      session.dispose();
    },
    [overloadNoticeController, session],
  );

  const replayCheckpoint = server.replay?.checkpoints[server.replay.cursor] ?? null;
  const state: DashboardReducedState =
    replayCheckpoint?.checkpoint.state ?? source.mission.checkpoint.state;
  const timeline = replayCheckpoint?.timeline ?? source.mission.timeline;
  const mode = server.readiness?.mode ?? "degradedLive";
  const scenario = server.catalog?.scenarios[0] ?? null;
  const replayOwnedLiveSnapshot =
    mode === "degradedLive" && source.server.currentRun?.mode === "replay";
  const locked =
    source.server.status === "runtimeChanged" ||
    (productionBootstrap !== undefined &&
      mode === "degradedLive" &&
      source.server.status === "connecting") ||
    mutationLocked;
  const operationPending = server.operationPending !== null;

  useEffect(() => {
    if (mode === "replay") return;
    let current = true;
    void replayStateDigest(source.mission.checkpoint.state).then((digest) => {
      if (current) setLiveDigest(digest);
    });
    return () => {
      current = false;
    };
  }, [mode, source.mission.checkpoint.state]);

  const replayPlaying = server.replay?.playing ?? false;
  const replaySpeed = server.replay?.speed ?? 1;
  useEffect(() => {
    if (!replayPlaying) return;
    const interval = window.setInterval(() => {
      setServer((current) =>
        current.replay === null
          ? current
          : { ...current, replay: applyReplayCursor(current.replay, current.replay.cursor + 1) },
      );
    }, 1_000 / replaySpeed);
    return () => {
      window.clearInterval(interval);
    };
  }, [replayPlaying, replaySpeed]);

  const closeResetDialog = useCallback(() => {
    restoreResetFocusRef.current = true;
    setResetDialogOpen(false);
  }, []);

  useLayoutEffect(() => {
    if (!resetDialogOpen && restoreResetFocusRef.current) {
      restoreResetFocusRef.current = false;
      resetButtonRef.current?.focus();
    }
  }, [resetDialogOpen]);

  const handleMarkerSample = useCallback((identifier: string) => {
    setMarkerSample(`Sample applied for ${identifier}`);
  }, []);

  async function submitMutation(operation: "reset" | "start"): Promise<void> {
    const client = mutationClient;
    if (client === null || scenario === null) {
      setMutationLocked(true);
      setServer((current) => ({
        ...current,
        alert: contractAlert(client === null ? "mutation bootstrap" : "scenario catalog"),
        operationPending: null,
      }));
      if (operation === "reset") closeResetDialog();
      return;
    }
    const predecessorMission = source.mission.checkpoint.state.currentMission;
    if (operation === "reset") {
      if (mode === "degradedLive") {
        if (predecessorMission === null) {
          setMutationLocked(true);
          setServer((current) => ({ ...current, alert: contractAlert("reset response") }));
          return;
        }
        setServer((current) => ({
          ...current,
          mutationOutcome: null,
          operationPending: "reset",
        }));
        closeResetDialog();
        const result = await client.reset({
          mode: "degradedLive",
          predecessorMissionId: predecessorMission.identifier,
        });
        applyMutationResult(result);
        return;
      }
      setServer((current) => ({ ...current, mutationOutcome: null, operationPending: "reset" }));
      closeResetDialog();
      const result = await client.reset({ mode: "replay" });
      applyMutationResult(result);
      return;
    }
    setServer((current) => ({ ...current, mutationOutcome: null, operationPending: "start" }));
    const result: DashboardMutationResult = await client.start(
      scenario.identifier,
      mode,
      scenario.revision,
    );
    applyMutationResult(result);
  }

  function applyMutationResult(result: DashboardMutationResult): void {
    if (result.kind === "busy") {
      setServer((current) => ({
        ...current,
        mutationOutcome: `Duplicate ${result.operation} submission ignored · ${result.operation} already pending`,
      }));
      return;
    }
    if (result.kind === "accepted") {
      const response = result.response;
      productionRuntimeRef.current?.acceptedMutation(response);
      const identity =
        response.mode === "degradedLive"
          ? `${response.missionId} · ${response.runId}`
          : response.sessionId;
      setServer((current) => ({
        ...current,
        mutationOutcome:
          result.operation === "start"
            ? `Start accepted · awaiting live snapshot · ${identity}`
            : response.mode === "replay"
              ? `New replay session accepted · loading validated replay · ${identity}`
              : `Reset accepted · awaiting planned snapshot · ${identity}`,
        operationPending: null,
      }));
      return;
    }
    if (result.kind === "contract-refused") {
      setMutationLocked(true);
      setServer((current) => ({
        ...current,
        alert: contractAlert(result.boundary),
        operationPending: null,
      }));
      return;
    }
    if (result.kind === "stale-runtime" || result.kind === "locked") {
      setMutationLocked(true);
      setServer((current) => ({
        ...current,
        alert: "Runtime changed · reload required",
        operationPending: null,
      }));
      return;
    }
    setServer((current) => ({
      ...current,
      alert:
        result.error.errorCode === "CANCELLATION_NOT_ESTABLISHED"
          ? "Cancellation was not established"
          : result.error.message,
      operationPending: null,
    }));
  }

  function changeReplayCursor(cursor: number): void {
    setServer((current) => {
      if (current.replay === null) return current;
      const replay = applyReplayCursor(current.replay, cursor);
      return {
        ...current,
        alert: replay.verification === "Refused" ? "Replay digest mismatch" : current.alert,
        replay,
      };
    });
  }

  const selectedMember = state.fleet.find((member) => member.identifier === selected) ?? null;
  const selectedScenarioMember =
    scenario?.members.find((member) => member.identifier === selected) ?? null;
  const selectedSector = state.sectors.find((sector) => sector.assignedMemberId === selected);
  const displayedDigest = replayCheckpoint?.digest ?? liveDigest;
  const alert = server.alert ?? sourceAlert(source);
  const reducedMotion =
    (window as Partial<Pick<Window, "matchMedia">>).matchMedia?.("(prefers-reduced-motion: reduce)")
      .matches ?? false;
  const productionMode = productionBootstrap !== undefined;

  function selectProductionMode(selectedMode: "degradedLive" | "replay"): void {
    if (!productionMode) return;
    setServer((current) => ({
      ...current,
      alert: mutationLocked ? current.alert : null,
      readiness: null,
      replay: selectedMode === "degradedLive" ? null : current.replay,
      replayInvalid: false,
    }));
    void productionRuntimeRef.current?.selectMode(selectedMode);
  }

  return (
    <>
      <header className={`app-header mode-${mode}`} data-mode={mode}>
        <h1>Aerial Rescue Mesh Mission Control</h1>
        <div className="header-statuses">
          <p aria-label="Operating mode" className="mode-badge" role="status">
            {mode === "replay" ? "ISOLATED REPLAY" : "DEGRADED LIVE SIMULATION"}
          </p>
          <p aria-label="Readiness" role="status">
            {server.readiness === null
              ? "LOADING"
              : server.readiness.ready
                ? "READY"
                : "UNAVAILABLE"}
          </p>
          <p aria-label="Connection" role="status">
            {connectionLabel(source, mode === "replay" && server.replay !== null)}
          </p>
        </div>
      </header>
      <main
        aria-hidden={resetDialogOpen ? true : undefined}
        className={`${fleetCollapsed ? "fleet-collapsed " : ""}mode-${mode}`}
        data-mode={mode}
      >
        <section aria-label="Scenario control" className="scenario-rail">
          <div>
            <p className="eyebrow">Mission profile</p>
            <h2>{scenario?.title ?? "Wilderness search"}</h2>
            <p>{scenario?.summary ?? "Loading validated scenario catalog."}</p>
            {server.readiness?.ready === false ? (
              <div aria-label="Readiness blockers" className="readiness-blockers" role="status">
                <p>Dependencies unavailable</p>
                <ul>
                  {server.readiness.reasons.map((reason) => (
                    <li key={reason}>{readinessReasonLabel(reason)}</li>
                  ))}
                </ul>
              </div>
            ) : null}
          </div>
          <div aria-label="Scenario metadata" role="group">
            <dl className="scenario-metadata">
              <div>
                <dt>Last known</dt>
                <dd>{scenario?.lastKnownLocation.label ?? "Loading"}</dd>
              </div>
              <div>
                <dt>Search area</dt>
                <dd>
                  {scenario === null
                    ? "Loading"
                    : `${String(scenario.searchAreaSquareMetres / 1_000_000)} km²`}
                </dd>
              </div>
              <div>
                <dt>Fleet</dt>
                <dd>
                  {scenario === null
                    ? "Loading"
                    : `${String(scenario.declaredCount)} declared = ${String(scenario.simulatedCount)} simulated + ${String(scenario.declaredOnlyCount)} declared only`}
                </dd>
              </div>
            </dl>
          </div>
          <fieldset aria-label="Mission mode" role="radiogroup">
            <legend>Mission mode</legend>
            <label>
              <input
                checked={mode === "degradedLive"}
                onChange={() => {
                  selectProductionMode("degradedLive");
                }}
                onClick={() => {
                  if (mode === "degradedLive") selectProductionMode("degradedLive");
                }}
                readOnly={!productionMode}
                type="radio"
              />{" "}
              Degraded live simulation
            </label>
            <label>
              <input
                checked={mode === "replay"}
                onChange={() => {
                  selectProductionMode("replay");
                }}
                onClick={() => {
                  if (mode === "replay") selectProductionMode("replay");
                }}
                readOnly={!productionMode}
                type="radio"
              />{" "}
              Isolated replay
            </label>
          </fieldset>
          <div className="mission-status-stack">
            <p aria-label="Dashboard state" role="status">
              {dashboardStateLabel(server, source, overloadNoticeActive)}
            </p>
            <p aria-label="Current mission" role="status">
              {replayOwnedLiveSnapshot ? (
                state.currentMission === null ? (
                  "No validated live mission"
                ) : (
                  <>
                    Previous live context ·{" "}
                    <span data-testid="mission-id">{state.currentMission.identifier}</span> ·{" "}
                    {state.currentMission.lifecycle}
                  </>
                )
              ) : mode === "replay" && server.replay === null ? (
                "No replay loaded"
              ) : state.currentMission === null ? (
                "No current mission"
              ) : (
                <>
                  <span data-testid="mission-id">{state.currentMission.identifier}</span> ·{" "}
                  {state.currentMission.lifecycle}
                </>
              )}
            </p>
            <p aria-label="Latest audit ordinal" role="status">
              {state.latestAuditOrdinal}
            </p>
            {(mode === "replay" ? server.replay !== null : !reducedMotion) ? (
              <p aria-label="Current mission digest" className="digest" role="status">
                {displayedDigest}
              </p>
            ) : null}
            <p aria-label="Telemetry motion" role="status">
              {reducedMotion
                ? "Reduced motion · positions update at samples"
                : "Interpolating between one-second samples"}
            </p>
            <p aria-label="Marker interpolation" role="status">
              {markerSample}
            </p>
            <p aria-label="Map focus" role="status">
              {selectedMember?.participation === "DECLARED_ONLY"
                ? `Selected ${selectedMember.identifier} · no executed map position`
                : selected === null
                  ? "Mission overview"
                  : `Focused on ${selected}`}
            </p>
            <span
              aria-label={
                source.server.runtimeId === null
                  ? "Runtime identifier unavailable"
                  : `Runtime identifier ${source.server.runtimeId}`
              }
              data-testid="runtime-id"
              title={source.server.runtimeId ?? undefined}
            >
              {source.server.runtimeId === null
                ? "Runtime unavailable"
                : `Runtime …${source.server.runtimeId.slice(-8)}`}
            </span>
          </div>
          {alert === null ? null : <p role="alert">{alert}</p>}
          {server.mutationOutcome === null ? null : (
            <p aria-label="Mutation outcome" data-testid="mutation-outcome" role="status">
              {server.mutationOutcome}
            </p>
          )}
          {locked || alert?.includes("Contract validation failed") ? (
            <button
              onClick={() => {
                window.location.reload();
              }}
              type="button"
            >
              Reload dashboard
            </button>
          ) : null}
          {mode === "degradedLive" ? (
            <div className="mission-actions">
              <button
                disabled={
                  locked ||
                  mutationClient === null ||
                  operationPending ||
                  server.readiness?.ready !== true ||
                  server.catalog === null ||
                  (!replayOwnedLiveSnapshot &&
                    state.currentMission !== null &&
                    state.currentMission.lifecycle !== "PLANNED")
                }
                onClick={() => {
                  void submitMutation("start");
                }}
                type="button"
              >
                Start wilderness mission
              </button>
              <button
                disabled={
                  locked ||
                  mutationClient === null ||
                  operationPending ||
                  state.currentMission === null ||
                  replayOwnedLiveSnapshot
                }
                onClick={() => {
                  setResetDialogOpen(true);
                }}
                ref={resetButtonRef}
                type="button"
              >
                Reset mission
              </button>
            </div>
          ) : productionMode && server.replay === null ? (
            <div className="mission-actions">
              <button
                disabled={
                  locked ||
                  mutationClient === null ||
                  operationPending ||
                  server.readiness?.ready !== true ||
                  server.catalog === null
                }
                onClick={() => {
                  void submitMutation("start");
                }}
                type="button"
              >
                Start replay
              </button>
            </div>
          ) : productionMode ? (
            <div className="mission-actions">
              <button
                disabled={locked || mutationClient === null || operationPending}
                onClick={() => {
                  setResetDialogOpen(true);
                }}
                ref={resetButtonRef}
                type="button"
              >
                New replay session
              </button>
            </div>
          ) : null}
        </section>
        <div className="map-stack">
          {scenario === null ? (
            <section aria-label="Search map" className="map-panel map-placeholder">
              <p>Waiting for local mission geometry</p>
            </section>
          ) : (
            <SearchMap
              fleet={state.fleet}
              onMarkerSample={handleMarkerSample}
              onSelect={setSelected}
              scenario={scenario}
              selectedIdentifier={selected}
              sectors={state.sectors}
            />
          )}
          {server.replay === null ? null : (
            <section
              aria-label="Replay controls"
              className="replay-controls replay-accent map-footer"
            >
              <div className="replay-buttons">
                <button
                  onClick={() => {
                    setServer((current) =>
                      current.replay === null
                        ? current
                        : { ...current, replay: { ...current.replay, playing: true } },
                    );
                  }}
                  type="button"
                >
                  Play replay
                </button>
                <button
                  onClick={() => {
                    setServer((current) =>
                      current.replay === null
                        ? current
                        : { ...current, replay: { ...current.replay, playing: false } },
                    );
                  }}
                  type="button"
                >
                  Pause replay
                </button>
                <button
                  onClick={() => {
                    changeReplayCursor((server.replay?.cursor ?? 0) + 1);
                  }}
                  type="button"
                >
                  Step forward
                </button>
                <button
                  onClick={() => {
                    changeReplayCursor(0);
                  }}
                  type="button"
                >
                  Restart replay
                </button>
              </div>
              <input
                aria-label="Replay progress"
                max={server.replay.checkpoints.length - 1}
                min="0"
                onChange={(event) => {
                  changeReplayCursor(Number(event.currentTarget.value));
                }}
                type="range"
                value={server.replay.cursor}
              />
              <div aria-label="Replay speed" className="speed-controls" role="group">
                {([0.5, 1, 2] as const).map((speed) => (
                  <button
                    aria-pressed={server.replay?.speed === speed}
                    key={speed}
                    onClick={() => {
                      setServer((current) =>
                        current.replay === null
                          ? current
                          : { ...current, replay: { ...current.replay, speed } },
                      );
                    }}
                    type="button"
                  >
                    {speed}×
                  </button>
                ))}
              </div>
              <div className="replay-verification">
                <p aria-label="Expected final digest" className="digest" role="status">
                  {server.replay.bundle.integrity.expectedFinalDigest}
                </p>
                <p aria-label="Computed final digest" className="digest" role="status">
                  {server.replay.checkpoints.at(-1)?.digest ?? ""}
                </p>
                <p aria-label="Replay digest verification" role="status">
                  {server.replay.verification}
                </p>
              </div>
            </section>
          )}
        </div>
        <section aria-label="Fleet status" className="fleet-rail">
          <button
            aria-label={fleetCollapsed ? "Expand fleet rail" : "Collapse fleet rail"}
            className="rail-toggle"
            onClick={() => {
              setFleetCollapsed((current) => !current);
            }}
            type="button"
          >
            {fleetCollapsed ? "‹" : "›"}
          </button>
          {fleetCollapsed ? null : (
            <>
              <div className="fleet-heading">
                <div>
                  <p className="eyebrow">Active roster</p>
                  <h2>Fleet status</h2>
                </div>
                <strong>20 SIMULATED + 3 DECLARED ONLY</strong>
              </div>
              <FleetTable
                filter={filter}
                fleet={state.fleet}
                onFilter={setFilter}
                onSelect={setSelected}
                sectors={state.sectors}
                selectedIdentifier={selected}
                scenarioMembers={scenario?.members ?? []}
              />
              <section aria-label="Drone detail" className="drone-detail">
                {selectedMember === null ? (
                  <p>Select an aircraft for details.</p>
                ) : selectedMember.participation === "DECLARED_ONLY" ? (
                  <>
                    <h3>{selectedMember.identifier}</h3>
                    {selectedScenarioMember?.participation === "DECLARED_ONLY" ? (
                      <>
                        <strong>{selectedScenarioMember.executionLabel}</strong>
                        <p>{selectedScenarioMember.role} · No telemetry expected</p>
                      </>
                    ) : (
                      <strong>DECLARED-ONLY DESCRIPTOR UNAVAILABLE</strong>
                    )}
                  </>
                ) : (
                  <>
                    <h3>{selectedMember.identifier}</h3>
                    <p>
                      {selectedMember.connectivity} · {selectedSector?.identifier ?? "No sector"} ·{" "}
                      {selectedSector?.state.replace("_", " ") ?? "UNASSIGNED"}
                    </p>
                    {selectedMember.telemetry === null ? (
                      <p>Waiting for first sample</p>
                    ) : (
                      <p>
                        {selectedMember.telemetry.batteryPercent}% ·{" "}
                        {selectedMember.telemetry.altitudeMetres} m ·{" "}
                        {selectedMember.telemetry.headingDegrees}° ·{" "}
                        {(selectedMember.telemetry.groundSpeedCentimetresPerSecond / 100).toFixed(
                          1,
                        )}{" "}
                        m/s
                      </p>
                    )}
                  </>
                )}
              </section>
              <section aria-label="Mission timeline" className="timeline">
                <h2>Mission timeline</h2>
                <ol aria-label="Ordered mission events" tabIndex={0}>
                  {timeline.map((ordered) => (
                    <li data-audit-ordinal={ordered.auditOrdinal} key={ordered.auditOrdinal}>
                      <span>#{ordered.auditOrdinal}</span> {timelineLabel(ordered)}{" "}
                      <time dateTime={ordered.event.time}>{ordered.event.time}</time>
                    </li>
                  ))}
                </ol>
              </section>
            </>
          )}
        </section>
      </main>
      {resetDialogOpen ? (
        <ResetDialog
          kind={mode === "replay" ? "replay" : "live"}
          onCancel={closeResetDialog}
          onConfirm={() => {
            void submitMutation("reset");
          }}
        />
      ) : null}
    </>
  );
}
