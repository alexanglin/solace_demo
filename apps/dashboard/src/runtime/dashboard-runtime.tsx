import { useEffect, useMemo, useState } from "react";

import { ApplicationShell } from "../application-shell";
import type { DashboardBootstrap } from "../contracts/generated";
import {
  createProposalDecisionSubmitter,
  type ProposalDecisionSubmitter,
} from "../operator/mutation-client";
import {
  createNativeDashboardEventSource,
  startDashboardLiveSource,
  type DashboardEventSourcePort,
  type DashboardSourceView,
} from "./live-source";

interface DashboardRuntimeProperties {
  readonly bootstrap: DashboardBootstrap;
  readonly createSubmitter?: (bearer: string) => ProposalDecisionSubmitter;
  readonly isOnline?: () => boolean;
  readonly openEventSource?: () => DashboardEventSourcePort;
}

function browserIsOnline(): boolean {
  return navigator.onLine;
}

function browserSubmitter(bearer: string): ProposalDecisionSubmitter {
  return createProposalDecisionSubmitter({
    bearer,
    fetcher: window.fetch.bind(window),
    newIdempotencyKey: () => globalThis.crypto.randomUUID(),
  });
}

export function DashboardRuntime({
  bootstrap,
  createSubmitter = browserSubmitter,
  isOnline = browserIsOnline,
  openEventSource = createNativeDashboardEventSource,
}: DashboardRuntimeProperties): React.JSX.Element {
  const [view, setView] = useState<DashboardSourceView>({
    mode: "degradedLive",
    sourceState: "loading",
  });

  useEffect(() => {
    const source = startDashboardLiveSource({
      runtimeId: bootstrap.runtimeId,
      isOnline,
      onView: setView,
      openEventSource,
    });
    return () => {
      source.dispose();
    };
  }, [bootstrap.runtimeId, isOnline, openEventSource]);

  const runMode = view.snapshot?.currentRun?.mode;
  const submitDecision = useMemo(
    () => (runMode === "degradedLive" ? createSubmitter(bootstrap.bearer) : undefined),
    [bootstrap.bearer, createSubmitter, runMode],
  );

  return (
    <ApplicationShell
      mode={view.mode}
      sourceState={view.sourceState}
      {...(view.snapshot === undefined ? {} : { snapshot: view.snapshot })}
      {...(submitDecision === undefined ? {} : { submitDecision })}
    />
  );
}
