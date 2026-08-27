/// <reference types="vite/client" />

import { createRoot } from "react-dom/client";

import { DashboardApplication } from "./dashboard-app";
import { readProductionBootstrap } from "./sources/production-runtime";
import "./styles.css";

const rootElement = document.getElementById("root");
if (!(rootElement instanceof HTMLDivElement)) {
  throw new Error("dashboard root must be a div");
}

const application =
  import.meta.env.MODE === "test" ? (
    <DashboardApplication />
  ) : (
    <DashboardApplication productionBootstrap={readProductionBootstrap(document)} />
  );
createRoot(rootElement).render(application);
