import type {
  DashboardEventFrame,
  DashboardSnapshot,
  DashboardSourceSignal,
} from "../contracts/generated";
import { decodeCanonicalJson } from "../contracts/bootstrap";
import {
  createDashboardSchemaRegistry,
  type DashboardSchemaId,
} from "../contracts/schema-registry";
import {
  checkpointFromSnapshot,
  emptyReducerCheckpoint,
  foldVerifiedOrderedDashboardEvent,
  type ReducerCheckpoint,
  type ReducerRefusal,
} from "../domain/reducer";
import {
  appendMeaningfulTimelineEvent,
  type MissionTimeline,
  replaceTimelineFromSnapshot,
} from "../domain/timeline";
import type {
  DashboardEventSource,
  DashboardSourceInput,
  DashboardSourceSubscription,
} from "./event-source";

const SNAPSHOT_SCHEMA_ID =
  "https://aerial-rescue.invalid/schemas/v1/dashboard/dashboard-snapshot.schema.json";
const EVENT_FRAME_SCHEMA_ID =
  "https://aerial-rescue.invalid/schemas/v1/dashboard/dashboard-event-frame.schema.json";
const OVERLOAD_SCHEMA_ID =
  "https://aerial-rescue.invalid/schemas/v1/dashboard/stream-overloaded.schema.json";
const SOURCE_SIGNAL_SCHEMA_ID =
  "https://aerial-rescue.invalid/schemas/v1/dashboard/source-signal.schema.json";
const schemaRegistry = createDashboardSchemaRegistry();

export type DashboardSourceStatus =
  | "idle"
  | "connecting"
  | "connected"
  | "disconnected"
  | "offline"
  | "recovered"
  | "runtimeChanged"
  | "modeMismatch"
  | "resynchronizing"
  | "disposed";

export type DashboardSourceRefusal =
  | {
      readonly code:
        | "CURSOR_WITNESS_MISMATCH"
        | "MALFORMED_JSON"
        | "CANONICAL_PROFILE_REFUSED"
        | "UNKNOWN_FRAME"
        | "SIGNAL_NAME_MISMATCH"
        | "UNSUPPORTED_CHANNEL"
        | "PROCESSING_FAILED"
        | "RESNAPSHOT_FAILED"
        | "RUN_IDENTITY_MISMATCH";
      readonly inputName: string;
    }
  | {
      readonly code: "SCHEMA_VALIDATION_FAILED";
      readonly inputName: string;
      readonly schemaId: DashboardSchemaId;
    }
  | {
      readonly code: "REDUCER_REFUSED";
      readonly inputName: string;
      readonly reducerCode: ReducerRefusal;
    }
  | {
      readonly actualMode: "degradedLive" | "replay";
      readonly code: "RUN_MODE_MISMATCH";
      readonly expectedMode: "degradedLive" | "replay";
      readonly inputName: string;
    };

export interface DashboardServerSourceState {
  readonly currentRun: DashboardSnapshot["currentRun"];
  readonly refusal: DashboardSourceRefusal | null;
  readonly runtimeId: string | null;
  readonly status: DashboardSourceStatus;
}

export interface DashboardMissionSourceState {
  readonly checkpoint: ReducerCheckpoint;
  readonly timeline: MissionTimeline;
}

export interface DashboardSourceSessionState {
  readonly mission: DashboardMissionSourceState;
  readonly server: DashboardServerSourceState;
}

export interface DashboardSourceSessionOptions {
  readonly consumeUnhandledInput?: (input: DashboardSourceInput) => Promise<void> | void;
  readonly onState?: (state: DashboardSourceSessionState) => void;
  readonly requestSnapshot?: () => Promise<void> | void;
}

type ValidatedDocument<Value> =
  { readonly ok: true; readonly value: Value } | { readonly ok: false };

function initialState(): DashboardSourceSessionState {
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

/** Owns one active source generation and the validated reducer-facing session state. */
export class DashboardSourceSession {
  private activeSubscription: DashboardSourceSubscription | null = null;
  private readonly consumeUnhandledInput:
    ((input: DashboardSourceInput) => Promise<void> | void) | undefined;
  private currentState = initialState();
  private disposed = false;
  private expectedLiveRun: { readonly missionId: string; readonly runId: string } | null = null;
  private expectedRunMode: "degradedLive" | "replay" | null = null;
  private generation = 0;
  private readonly onState: ((state: DashboardSourceSessionState) => void) | undefined;
  private processing: Promise<void> = Promise.resolve();
  private readonly requestSnapshot: (() => Promise<void> | void) | undefined;

  constructor(options: DashboardSourceSessionOptions = {}) {
    this.consumeUnhandledInput = options.consumeUnhandledInput;
    this.onState = options.onState;
    this.requestSnapshot = options.requestSnapshot;
  }

  get state(): DashboardSourceSessionState {
    return this.currentState;
  }

  /** Seed the process identity validated from the transient production bootstrap. */
  anchorRuntime(runtimeId: string): void {
    const acceptedRuntime = this.currentState.server.runtimeId;
    if (acceptedRuntime !== null && acceptedRuntime !== runtimeId) {
      this.lockRuntimeChange();
      return;
    }
    this.publish({
      mission: this.currentState.mission,
      server: { ...this.currentState.server, runtimeId },
    });
  }

  /** Require the next accepted live snapshot to confirm the mutation's stable identities. */
  expectLiveRun(missionId: string, runId: string): void {
    if (!this.disposed) this.expectedLiveRun = { missionId, runId };
  }

  /** Confirms that an initial mode mismatch names the replay session selected for resumption. */
  acceptReplayResume(sessionId: string): boolean {
    const run = this.currentState.server.currentRun;
    if (
      this.disposed ||
      this.currentState.server.status !== "modeMismatch" ||
      run?.mode !== "replay" ||
      run.sessionId !== sessionId
    ) {
      return false;
    }
    this.publish({
      mission: this.currentState.mission,
      server: { ...this.currentState.server, refusal: null, status: "connected" },
    });
    return true;
  }

  replaceSource(
    source: DashboardEventSource,
    expectedRunMode: "degradedLive" | "replay" | null = null,
  ): void {
    if (this.disposed) {
      throw new Error("a disposed dashboard source session cannot be reopened");
    }
    this.generation += 1;
    this.closeActiveSubscription();
    this.expectedRunMode = expectedRunMode;
    const sourceGeneration = this.generation;
    const subscription = source.open((input) => this.enqueue(sourceGeneration, input));
    if (this.generation === sourceGeneration) {
      this.activeSubscription = subscription;
    } else {
      subscription.dispose();
    }
  }

  whenIdle(): Promise<void> {
    return this.processing;
  }

  dispose(): void {
    if (this.disposed) {
      return;
    }
    this.disposed = true;
    this.generation += 1;
    this.closeActiveSubscription();
    this.publish({
      mission: this.currentState.mission,
      server: { ...this.currentState.server, status: "disposed" },
    });
  }

  private closeActiveSubscription(): void {
    const subscription = this.activeSubscription;
    this.activeSubscription = null;
    subscription?.dispose();
  }

  private enqueue(sourceGeneration: number, input: DashboardSourceInput): Promise<void> {
    const operation = this.processing.then(async () => {
      if (!this.disposed && sourceGeneration === this.generation) {
        await this.processInput(sourceGeneration, input);
      }
    });
    this.processing = operation.catch(() => {
      if (!this.disposed && sourceGeneration === this.generation) {
        this.refuse({ code: "PROCESSING_FAILED", inputName: input.name });
      }
    });
    return this.processing;
  }

  private async processInput(sourceGeneration: number, input: DashboardSourceInput): Promise<void> {
    if (input.channel === "source-signal") {
      this.processSourceSignal(input);
      return;
    }
    if (input.channel !== "sse-frame") {
      if (this.consumeUnhandledInput !== undefined) {
        await this.consumeUnhandledInput(input);
        return;
      }
      this.refuse({ code: "UNSUPPORTED_CHANNEL", inputName: input.name });
      return;
    }
    if (input.name === "snapshot") {
      await this.processSnapshot(input);
      return;
    }
    if (input.name === "dashboard-event") {
      await this.processEventFrame(input);
      return;
    }
    if (input.name === "stream-overloaded") {
      await this.processOverload(sourceGeneration, input);
      return;
    }
    this.refuse({ code: "UNKNOWN_FRAME", inputName: input.name });
  }

  private validate<Value>(
    input: DashboardSourceInput,
    schemaId: DashboardSchemaId,
  ): ValidatedDocument<Value> {
    const decoded = decodeCanonicalJson(input.raw);
    if (!decoded.ok) {
      this.refuse({ code: decoded.failure.code, inputName: input.name });
      return { ok: false };
    }
    const validated = schemaRegistry.validate(schemaId, decoded.value);
    if (!validated.ok) {
      this.refuse({ code: validated.failure.code, inputName: input.name, schemaId });
      return { ok: false };
    }
    return { ok: true, value: validated.value as Value };
  }

  private async processSnapshot(input: DashboardSourceInput): Promise<void> {
    const validated = this.validate<DashboardSnapshot>(input, SNAPSHOT_SCHEMA_ID);
    if (!validated.ok) {
      return;
    }
    const acceptedRuntime = this.currentState.server.runtimeId;
    if (!this.acceptCursorWitness(input, validated.value.cursor)) {
      return;
    }
    if (acceptedRuntime !== null && validated.value.runtimeId !== acceptedRuntime) {
      this.lockRuntimeChange();
      return;
    }
    const actualMode = validated.value.currentRun?.mode;
    if (
      this.expectedRunMode !== null &&
      actualMode !== undefined &&
      actualMode !== this.expectedRunMode
    ) {
      this.publish({
        mission: this.currentState.mission,
        server: {
          currentRun: validated.value.currentRun,
          refusal: {
            actualMode,
            code: "RUN_MODE_MISMATCH",
            expectedMode: this.expectedRunMode,
            inputName: input.name,
          },
          runtimeId: validated.value.runtimeId,
          status: "modeMismatch",
        },
      });
      return;
    }
    if (!this.acceptRunIdentity(input, validated.value)) {
      return;
    }
    const anchored = await checkpointFromSnapshot(validated.value);
    if (!anchored.ok) {
      this.reducerRefusal(input.name, anchored.failure.code);
      return;
    }
    this.expectedLiveRun = null;
    this.publish({
      mission: {
        checkpoint: anchored.checkpoint,
        timeline: replaceTimelineFromSnapshot(validated.value.timeline),
      },
      server: {
        currentRun: validated.value.currentRun,
        refusal: null,
        runtimeId: validated.value.runtimeId,
        status: "connected",
      },
    });
  }

  private async processEventFrame(input: DashboardSourceInput): Promise<void> {
    const validated = this.validate<DashboardEventFrame>(input, EVENT_FRAME_SCHEMA_ID);
    if (!validated.ok) {
      return;
    }
    if (!this.acceptCursorWitness(input, validated.value.cursor)) {
      return;
    }
    if (this.currentState.server.status === "modeMismatch") {
      return;
    }
    const folded = await foldVerifiedOrderedDashboardEvent(
      this.currentState.mission.checkpoint,
      validated.value.event,
      validated.value.digest,
    );
    if (!folded.ok) {
      this.reducerRefusal(input.name, folded.failure.code);
      return;
    }
    this.publish({
      mission: {
        checkpoint: folded.checkpoint,
        timeline: appendMeaningfulTimelineEvent(
          this.currentState.mission.timeline,
          validated.value.event,
        ),
      },
      server: {
        ...this.currentState.server,
        refusal: null,
        status: "connected",
      },
    });
  }

  private processSourceSignal(input: DashboardSourceInput): void {
    const validated = this.validate<DashboardSourceSignal>(input, SOURCE_SIGNAL_SCHEMA_ID);
    if (!validated.ok) {
      return;
    }
    if (validated.value.signal !== input.name) {
      this.refuse({ code: "SIGNAL_NAME_MISMATCH", inputName: input.name });
      return;
    }
    if (this.currentState.server.status === "modeMismatch") {
      return;
    }
    this.publish({
      mission: this.currentState.mission,
      server: {
        ...this.currentState.server,
        refusal: null,
        status: validated.value.signal,
      },
    });
  }

  private async processOverload(
    sourceGeneration: number,
    input: DashboardSourceInput,
  ): Promise<void> {
    const validated = this.validate(input, OVERLOAD_SCHEMA_ID);
    if (!validated.ok || sourceGeneration !== this.generation) {
      return;
    }
    this.generation += 1;
    this.closeActiveSubscription();
    this.publish({
      mission: this.currentState.mission,
      server: { ...this.currentState.server, refusal: null, status: "resynchronizing" },
    });
    try {
      await this.requestSnapshot?.();
    } catch {
      this.refuse({ code: "RESNAPSHOT_FAILED", inputName: input.name });
    }
  }

  private reducerRefusal(inputName: string, reducerCode: ReducerRefusal): void {
    this.refuse({ code: "REDUCER_REFUSED", inputName, reducerCode });
  }

  private acceptCursorWitness(input: DashboardSourceInput, bodyCursor: string): boolean {
    if (input.lastEventId !== undefined && input.lastEventId !== bodyCursor) {
      this.refuse({ code: "CURSOR_WITNESS_MISMATCH", inputName: input.name });
      return false;
    }
    return true;
  }

  private acceptRunIdentity(input: DashboardSourceInput, snapshot: DashboardSnapshot): boolean {
    const run = snapshot.currentRun;
    if (
      run?.mode === "degradedLive" &&
      snapshot.state.currentMission?.identifier !== run.missionId
    ) {
      this.refuse({ code: "RUN_IDENTITY_MISMATCH", inputName: input.name });
      return false;
    }
    if (
      this.expectedLiveRun !== null &&
      (run?.mode !== "degradedLive" ||
        run.missionId !== this.expectedLiveRun.missionId ||
        run.runId !== this.expectedLiveRun.runId)
    ) {
      this.refuse({ code: "RUN_IDENTITY_MISMATCH", inputName: input.name });
      return false;
    }
    return true;
  }

  private lockRuntimeChange(): void {
    this.generation += 1;
    this.closeActiveSubscription();
    this.publish({
      mission: this.currentState.mission,
      server: { ...this.currentState.server, refusal: null, status: "runtimeChanged" },
    });
  }

  private refuse(refusal: DashboardSourceRefusal): void {
    this.publish({
      mission: this.currentState.mission,
      server: { ...this.currentState.server, refusal },
    });
  }

  private publish(state: DashboardSourceSessionState): void {
    this.currentState = state;
    this.onState?.(state);
  }
}
