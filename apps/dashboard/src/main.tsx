/// <reference types="vite/client" />

import { StrictMode, useEffect } from "react";
import { createRoot } from "react-dom/client";

interface DashboardTestRevisionHarness {
  appliedRevision: number;
  sourceRevision: number;
  sourceScript: unknown;
}

type DashboardTestWindow = Window & {
  __AERIAL_RESCUE_DASHBOARD_TEST__?: DashboardTestRevisionHarness;
};

function acknowledgeTestSourceRevision(): void {
  const harness = (window as DashboardTestWindow).__AERIAL_RESCUE_DASHBOARD_TEST__;
  if (harness === undefined) {
    return;
  }
  harness.sourceScript = null;
  harness.appliedRevision = harness.sourceRevision;
}

function ApplicationShell(): React.JSX.Element {
  useEffect(() => {
    if (import.meta.env.MODE === "test") {
      acknowledgeTestSourceRevision();
    }
  }, []);

  return (
    <>
      <header>
        <h1>Aerial Rescue Mesh Mission Control</h1>
        <p aria-label="Operating mode" role="status">
          DEGRADED LIVE SIMULATION
        </p>
      </header>
      <main>
        <section aria-labelledby="dashboard-state-heading">
          <h2 id="dashboard-state-heading">Mission dashboard</h2>
          <p aria-label="Dashboard state" role="status">
            Loading scenario catalog
          </p>
        </section>
      </main>
    </>
  );
}

const rootElement = document.getElementById("root");
if (!(rootElement instanceof HTMLDivElement)) {
  throw new Error("dashboard root must be a div");
}

createRoot(rootElement).render(
  <StrictMode>
    <ApplicationShell />
  </StrictMode>,
);
