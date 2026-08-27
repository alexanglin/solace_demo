import type { DashboardBootstrap } from "../contracts/generated";
import { consumeDashboardBootstrap } from "../contracts/bootstrap";

export type DashboardBootstrapElementResult =
  | { readonly ok: true; readonly value: DashboardBootstrap }
  | {
      readonly ok: false;
      readonly reason: "BOOTSTRAP_MISSING" | "BOOTSTRAP_REFUSED";
    };

export function consumeDashboardBootstrapElement(
  sourceDocument: Document,
): DashboardBootstrapElementResult {
  const element = sourceDocument.querySelector(
    'script[type="application/json"][data-dashboard-bootstrap]',
  );
  if (!(element instanceof HTMLScriptElement)) {
    return { ok: false, reason: "BOOTSTRAP_MISSING" };
  }

  const raw = element.textContent;
  element.remove();
  let bootstrap: DashboardBootstrap | undefined;
  const result = consumeDashboardBootstrap(raw, (validated) => {
    bootstrap = validated;
  });
  if (!result.ok || bootstrap === undefined) {
    return { ok: false, reason: "BOOTSTRAP_REFUSED" };
  }
  return { ok: true, value: bootstrap };
}
