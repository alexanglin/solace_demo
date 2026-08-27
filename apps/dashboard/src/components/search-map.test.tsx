import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { fixtureForState } from "../../tests/e2e/support/dashboard-fixtures";
import type { DashboardReducedState, DashboardScenarioCatalog } from "../contracts/generated";
import { SearchMap } from "./search-map";

const fakes = vi.hoisted(() => ({
  addControl: vi.fn(),
  addedLayers: [] as unknown[],
  addSource: vi.fn(),
  easeTo: vi.fn(),
  fitBounds: vi.fn(),
  maps: [] as { container: HTMLElement; layers: Set<string> }[],
  removeMap: vi.fn(),
  removeMarker: vi.fn(),
  setLayoutProperty: vi.fn(),
  setLngLat: vi.fn(),
  sources: new Map<string, { data: unknown; setData: ReturnType<typeof vi.fn> }>(),
}));

vi.mock("maplibre-gl", () => {
  class FakeMap {
    readonly container: HTMLElement;
    readonly layers = new Set<string>();

    constructor(options: { readonly container: HTMLElement }) {
      this.container = options.container;
      const canvas = document.createElement("canvas");
      canvas.className = "maplibregl-canvas";
      this.container.append(canvas);
      fakes.maps.push(this);
    }

    addLayer(layer: { readonly id: string }): void {
      this.layers.add(layer.id);
      fakes.addedLayers.push(layer);
    }

    addControl(control: unknown, position?: string): void {
      fakes.addControl(control, position);
    }

    easeTo(options: unknown): void {
      fakes.easeTo(options);
    }

    addSource(identifier: string, source: { readonly data: unknown }): void {
      const held = { data: source.data, setData: vi.fn() };
      fakes.sources.set(identifier, held);
      fakes.addSource(identifier, source);
    }

    fitBounds(...arguments_: unknown[]): void {
      fakes.fitBounds(...arguments_);
    }

    getLayer(identifier: string): object | undefined {
      return this.layers.has(identifier) ? {} : undefined;
    }

    getSource(
      identifier: string,
    ): { data: unknown; setData: ReturnType<typeof vi.fn> } | undefined {
      return fakes.sources.get(identifier);
    }

    once(_name: string, callback: () => void): void {
      callback();
    }

    remove(): void {
      fakes.removeMap();
    }

    setLayoutProperty(...arguments_: unknown[]): void {
      fakes.setLayoutProperty(...arguments_);
    }
  }

  class FakeMarker {
    private readonly element: HTMLElement;

    constructor(options: { readonly element: HTMLElement }) {
      this.element = options.element;
      this.element.classList.add("maplibregl-marker");
    }

    addTo(map: FakeMap): this {
      map.container.append(this.element);
      return this;
    }

    getElement(): HTMLElement {
      return this.element;
    }

    remove(): void {
      this.element.remove();
      fakes.removeMarker();
    }

    setLngLat(...arguments_: unknown[]): this {
      fakes.setLngLat(...arguments_);
      return this;
    }
  }

  class FakeScaleControl {
    getDefaultPosition(): string {
      return "bottom-left";
    }
  }

  return { Map: FakeMap, Marker: FakeMarker, ScaleControl: FakeScaleControl };
});

afterEach(() => {
  cleanup();
  fakes.addControl.mockClear();
  fakes.addSource.mockClear();
  fakes.addedLayers.length = 0;
  fakes.fitBounds.mockClear();
  fakes.maps.length = 0;
  fakes.removeMap.mockClear();
  fakes.removeMarker.mockClear();
  fakes.setLayoutProperty.mockClear();
  fakes.setLngLat.mockClear();
  fakes.sources.clear();
  vi.unstubAllGlobals();
});

function liveFixture(): {
  readonly scenario: DashboardScenarioCatalog["scenarios"][number];
  readonly state: DashboardReducedState;
} {
  const inputs = fixtureForState("running").inputs;
  const catalogInput = inputs.find(({ name }) => name === "scenario-catalog");
  const snapshotInput = inputs.find(({ name }) => name === "snapshot");
  if (catalogInput === undefined || snapshotInput === undefined) {
    throw new Error("map fixture is incomplete");
  }
  const catalog = JSON.parse(catalogInput.raw) as DashboardScenarioCatalog;
  const snapshot = JSON.parse(snapshotInput.raw) as { readonly state: DashboardReducedState };
  const scenario = catalog.scenarios[0];
  if (scenario === undefined) throw new Error("scenario fixture is missing");
  return { scenario, state: snapshot.state };
}

test("installs local geometry, moves markers, toggles layers, and fits mission bounds", async () => {
  // Arrange
  const { scenario, state } = liveFixture();
  const onMarkerSample = vi.fn();
  const rendered = render(
    <SearchMap
      fleet={state.fleet}
      onMarkerSample={onMarkerSample}
      onSelect={vi.fn()}
      scenario={scenario}
      selectedIdentifier="drone-sim-12"
      sectors={state.sectors}
    />,
  );
  const changedFleet: DashboardReducedState["fleet"] = state.fleet.map((member) =>
    member.identifier === "drone-sim-01" && member.participation === "SIMULATED"
      ? {
          ...member,
          telemetry:
            member.telemetry === null
              ? null
              : {
                  ...member.telemetry,
                  latitudeMicrodegrees: member.telemetry.latitudeMicrodegrees + 10,
                },
        }
      : member,
  );

  // Act
  fireEvent.click(screen.getByRole("checkbox", { name: /sectors/i }));
  fireEvent.click(screen.getByRole("checkbox", { name: /drones/i }));
  fireEvent.click(screen.getByRole("checkbox", { name: /trails/i }));
  fireEvent.click(screen.getByRole("button", { name: "Fit mission" }));
  rendered.rerender(
    <SearchMap
      fleet={changedFleet}
      onMarkerSample={onMarkerSample}
      onSelect={vi.fn()}
      scenario={scenario}
      selectedIdentifier="drone-sim-01"
      sectors={state.sectors}
    />,
  );
  await waitFor(() => {
    if (!onMarkerSample.mock.calls.some(([identifier]) => identifier === "drone-sim-01")) {
      throw new Error("updated marker sample has not been observed");
    }
  });

  // Assert
  expect(document.querySelectorAll('[data-participation="SIMULATED"]')).toHaveLength(20);
  expect(document.querySelector('[data-drone-id="drone-sim-01"]')?.className).toContain("selected");
  expect(fakes.fitBounds).toHaveBeenCalledTimes(2);
  expect(fakes.setLayoutProperty).toHaveBeenCalledWith("sector-fill", "visibility", "none");
  expect(onMarkerSample).toHaveBeenCalledWith("drone-sim-01");
  expect(screen.getByRole("status", { name: "Map viewport" }).textContent).toContain(
    "Mission bounds",
  );
});

test("fits validated mission geometry as soon as the local map style loads", () => {
  // Arrange
  const { scenario, state } = liveFixture();
  const expectedBounds = [
    [
      Math.min(...scenario.searchPolygon.vertices.map((vertex) => vertex.longitudeMicrodegrees)) /
        1_000_000,
      Math.min(...scenario.searchPolygon.vertices.map((vertex) => vertex.latitudeMicrodegrees)) /
        1_000_000,
    ],
    [
      Math.max(...scenario.searchPolygon.vertices.map((vertex) => vertex.longitudeMicrodegrees)) /
        1_000_000,
      Math.max(...scenario.searchPolygon.vertices.map((vertex) => vertex.latitudeMicrodegrees)) /
        1_000_000,
    ],
  ];

  // Act
  render(
    <SearchMap
      fleet={state.fleet}
      onMarkerSample={vi.fn()}
      onSelect={vi.fn()}
      scenario={scenario}
      sectors={state.sectors}
      selectedIdentifier={null}
    />,
  );

  // Assert
  expect(fakes.fitBounds).toHaveBeenCalledOnce();
  expect(fakes.fitBounds).toHaveBeenCalledWith(expectedBounds, { duration: 0, padding: 38 });
  expect(screen.getByRole("status", { name: "Map viewport" }).textContent).toBe(
    "Mission bounds fitted automatically",
  );
});

test("falls back to prepared sector centroids and removes stale markers and map resources", () => {
  // Arrange
  const { scenario, state } = liveFixture();
  const preparedFleet: DashboardReducedState["fleet"] = state.fleet.map((member) =>
    member.participation === "SIMULATED" ? { ...member, telemetry: null } : member,
  );
  const rendered = render(
    <SearchMap
      fleet={preparedFleet}
      onMarkerSample={vi.fn()}
      onSelect={vi.fn()}
      scenario={scenario}
      selectedIdentifier={null}
      sectors={state.sectors}
    />,
  );

  // Act
  rendered.rerender(
    <SearchMap
      fleet={preparedFleet.slice(0, 4)}
      onMarkerSample={vi.fn()}
      onSelect={vi.fn()}
      scenario={scenario}
      selectedIdentifier={null}
      sectors={state.sectors}
    />,
  );
  rendered.unmount();

  // Assert
  expect(fakes.removeMarker).toHaveBeenCalled();
  expect(fakes.removeMap).toHaveBeenCalledOnce();
  expect(fakes.setLngLat).toHaveBeenCalled();
});

test("projects sector states, pointer selection, and bounded per-drone telemetry trails", () => {
  // Arrange
  const { scenario, state } = liveFixture();
  const onSelect = vi.fn();
  const rendered = render(
    <SearchMap
      fleet={state.fleet}
      onMarkerSample={vi.fn()}
      onSelect={onSelect}
      scenario={scenario}
      sectors={state.sectors}
      selectedIdentifier={null}
    />,
  );
  const simulated = state.fleet.find(
    (member) => member.identifier === "drone-sim-01" && member.participation === "SIMULATED",
  );
  if (simulated?.participation !== "SIMULATED" || simulated.telemetry === null) {
    throw new Error("trail fixture is missing drone-sim-01 telemetry");
  }
  const initialTelemetry = simulated.telemetry;
  const selectedMarker = document.querySelector('[data-drone-id="drone-sim-07"]');
  if (!(selectedMarker instanceof HTMLElement)) {
    throw new Error("map fixture is missing the drone-sim-07 marker");
  }

  // Act
  fireEvent.click(selectedMarker);
  for (let sample = 1; sample <= 12; sample += 1) {
    const fleet = state.fleet.map((member) =>
      member.identifier === simulated.identifier
        ? {
            ...simulated,
            telemetry: {
              ...initialTelemetry,
              longitudeMicrodegrees: initialTelemetry.longitudeMicrodegrees + sample,
            },
          }
        : member,
    );
    rendered.rerender(
      <SearchMap
        fleet={fleet}
        onMarkerSample={vi.fn()}
        onSelect={onSelect}
        scenario={scenario}
        sectors={state.sectors.map((sector) =>
          sector.identifier === "sector-01" ? { ...sector, state: "SEARCHED" } : sector,
        )}
        selectedIdentifier="drone-sim-07"
      />,
    );
  }
  const sectorUpdates = fakes.sources.get("sectors")?.setData.mock.calls;
  const trailUpdates = fakes.sources.get("trails")?.setData.mock.calls;
  const finalSectors = sectorUpdates?.at(-1)?.[0] as {
    readonly features: readonly { readonly properties: { readonly state: string } }[];
  };
  const finalTrails = trailUpdates?.at(-1)?.[0] as {
    readonly features: readonly {
      readonly geometry: { readonly coordinates: readonly number[][] };
      readonly properties: { readonly droneId: string };
    }[];
  };

  // Assert
  expect(onSelect).toHaveBeenCalledWith("drone-sim-07");
  expect(finalSectors.features[0]?.properties.state).toBe("SEARCHED");
  expect(finalTrails.features.every(({ geometry }) => geometry.coordinates.length <= 8)).toBe(true);
  expect(new Set(finalTrails.features.map(({ properties }) => properties.droneId)).size).toBe(
    finalTrails.features.length,
  );
  expect(JSON.stringify(fakes.addedLayers)).toContain("AT_RISK");
  expect(JSON.stringify(fakes.addedLayers)).toContain("line-dasharray");
});

test("installs a functional scale and focuses a selected aircraft without reduced-motion travel", () => {
  // Arrange
  const { scenario, state } = liveFixture();
  vi.stubGlobal(
    "matchMedia",
    vi.fn(() => ({ matches: true })),
  );
  const selected = state.fleet.find(
    (member) => member.identifier === "drone-sim-07" && member.participation === "SIMULATED",
  );
  if (selected?.participation !== "SIMULATED" || selected.telemetry === null) {
    throw new Error("map fixture is missing drone-sim-07 telemetry");
  }
  render(
    <SearchMap
      fleet={state.fleet}
      onMarkerSample={vi.fn()}
      onSelect={vi.fn()}
      scenario={scenario}
      sectors={state.sectors}
      selectedIdentifier={selected.identifier}
    />,
  );

  // Act
  fireEvent.click(screen.getByRole("button", { name: "Fit mission" }));

  // Assert
  expect(fakes.addControl).toHaveBeenCalledWith(expect.anything(), "bottom-left");
  expect(fakes.easeTo).toHaveBeenCalledWith({
    center: [
      selected.telemetry.longitudeMicrodegrees / 1_000_000,
      selected.telemetry.latitudeMicrodegrees / 1_000_000,
    ],
    duration: 0,
  });
  expect(screen.getByRole("status", { name: "Map scale" }).textContent).toContain("mission bounds");
});
