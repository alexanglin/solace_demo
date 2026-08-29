/// <reference types="vite/client" />
import * as maplibregl from "maplibre-gl";
import maplibreWorkerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?url";
import type {
  GeoJSONSource,
  LngLatBoundsLike,
  Map as MapLibreMap,
  Marker,
  StyleSpecification,
} from "maplibre-gl";
import { useEffect, useMemo, useRef, useState } from "react";

import type { DashboardReducedState, DashboardScenarioCatalog } from "../contracts/generated";

type Scenario = DashboardScenarioCatalog["scenarios"][number];

// MapLibre otherwise derives its worker URL from `import.meta.url`, which resolves to a file the
// bundler never emits. Naming the emitted asset keeps the worker same-origin and local, as the
// dashboard guide requires of every map asset.
maplibregl.setWorkerUrl(maplibreWorkerUrl);

const emptyStyle: StyleSpecification = {
  layers: [{ id: "background", type: "background", paint: { "background-color": "#08151b" } }],
  sources: {},
  version: 8,
};

interface Visibility {
  readonly drones: boolean;
  readonly sectors: boolean;
  readonly trails: boolean;
}

const INITIAL_VISIBILITY: Visibility = {
  drones: true,
  sectors: true,
  trails: true,
};
const TRAIL_POINT_LIMIT = 8;
type TrailHistory = ReadonlyMap<string, readonly [number, number][]>;

export interface SearchMapProps {
  readonly fleet: DashboardReducedState["fleet"];
  readonly onMarkerSample: (identifier: string) => void;
  readonly onSelect: (identifier: string) => void;
  readonly scenario: Scenario;
  readonly selectedIdentifier: string | null;
  readonly sectors: DashboardReducedState["sectors"];
}

function polygonCoordinates(vertices: Scenario["searchPolygon"]["vertices"]): number[][] {
  return vertices.map(({ latitudeMicrodegrees, longitudeMicrodegrees }) => [
    longitudeMicrodegrees / 1_000_000,
    latitudeMicrodegrees / 1_000_000,
  ]);
}

function scenarioBounds(scenario: Scenario): LngLatBoundsLike {
  const longitudes = scenario.searchPolygon.vertices.map(
    ({ longitudeMicrodegrees }) => longitudeMicrodegrees / 1_000_000,
  );
  const latitudes = scenario.searchPolygon.vertices.map(
    ({ latitudeMicrodegrees }) => latitudeMicrodegrees / 1_000_000,
  );
  return [
    [Math.min(...longitudes), Math.min(...latitudes)],
    [Math.max(...longitudes), Math.max(...latitudes)],
  ];
}

function fitScenario(map: MapLibreMap, scenario: Scenario): void {
  map.fitBounds(scenarioBounds(scenario), {
    duration: 0,
    padding: 38,
  });
}

function sectorCentroid(scenario: Scenario, identifier: string): [number, number] {
  const index = Math.max(
    0,
    scenario.members
      .filter((member) => member.participation === "SIMULATED")
      .findIndex((member) => member.identifier === identifier),
  );
  const sector = scenario.sectors[index];
  if (sector === undefined) {
    throw new Error("validated scenario is missing a simulated member sector");
  }
  const uniqueVertices = sector.vertices.slice(0, -1);
  const divisor = uniqueVertices.length;
  return [
    uniqueVertices.reduce((sum, vertex) => sum + vertex.longitudeMicrodegrees, 0) /
      divisor /
      1_000_000,
    uniqueVertices.reduce((sum, vertex) => sum + vertex.latitudeMicrodegrees, 0) /
      divisor /
      1_000_000,
  ];
}

function coordinatesFor(
  scenario: Scenario,
  member: Extract<DashboardReducedState["fleet"][number], { participation: "SIMULATED" }>,
): [number, number] {
  return member.telemetry === null
    ? sectorCentroid(scenario, member.identifier)
    : [
        member.telemetry.longitudeMicrodegrees / 1_000_000,
        member.telemetry.latitudeMicrodegrees / 1_000_000,
      ];
}

function sectorCollection(
  scenario: Scenario,
  sectors: DashboardReducedState["sectors"],
): GeoJSON.FeatureCollection<GeoJSON.Polygon> {
  const stateByIdentifier = new Map(sectors.map((sector) => [sector.identifier, sector]));
  return {
    features: scenario.sectors.map((sector) => {
      const state = stateByIdentifier.get(sector.identifier);
      return {
        geometry: { coordinates: [polygonCoordinates(sector.vertices)], type: "Polygon" },
        properties: {
          assignedMemberId: state?.assignedMemberId ?? null,
          identifier: sector.identifier,
          state: state?.state ?? "UNASSIGNED",
          stateLabel: (state?.state ?? "UNASSIGNED").replace("_", " "),
        },
        type: "Feature",
      };
    }),
    type: "FeatureCollection",
  };
}

function trailCollection(history: TrailHistory): GeoJSON.FeatureCollection<GeoJSON.LineString> {
  return {
    features: Array.from(history, ([droneId, coordinates]) => ({
      geometry: { coordinates: [...coordinates], type: "LineString" as const },
      properties: { droneId },
      type: "Feature" as const,
    })).filter(({ geometry }) => geometry.coordinates.length >= 2),
    type: "FeatureCollection",
  };
}

function updateGeoJson(
  map: MapLibreMap,
  identifier: string,
  data: GeoJSON.FeatureCollection,
): void {
  const source = map.getSource<GeoJSONSource>(identifier);
  void source?.setData(data);
}

function installGeometry(
  map: MapLibreMap,
  scenario: Scenario,
  sectors: DashboardReducedState["sectors"],
  trails: TrailHistory,
): void {
  map.addSource("mission-boundary", {
    data: {
      features: [
        {
          geometry: {
            coordinates: [polygonCoordinates(scenario.searchPolygon.vertices)],
            type: "Polygon",
          },
          properties: {},
          type: "Feature",
        },
      ],
      type: "FeatureCollection",
    },
    type: "geojson",
  });
  map.addLayer({
    id: "mission-boundary",
    paint: { "line-color": "#70d6bb", "line-width": 2 },
    source: "mission-boundary",
    type: "line",
  });
  map.addSource("sectors", {
    data: sectorCollection(scenario, sectors),
    type: "geojson",
  });
  map.addLayer({
    id: "sector-fill",
    paint: {
      "fill-color": [
        "match",
        ["get", "state"],
        "ASSIGNED",
        "#b6812d",
        "AT_RISK",
        "#b8413b",
        "SEARCHED",
        "#2f8b6f",
        "#315863",
      ],
      "fill-opacity": ["match", ["get", "state"], "AT_RISK", 0.34, "SEARCHED", 0.28, 0.16],
    },
    source: "sectors",
    type: "fill",
  });
  map.addLayer({
    id: "sector-lines",
    paint: {
      "line-color": [
        "match",
        ["get", "state"],
        "ASSIGNED",
        "#ffc864",
        "AT_RISK",
        "#ff776c",
        "SEARCHED",
        "#77e7cb",
        "#6d8990",
      ],
      "line-width": ["match", ["get", "state"], "SEARCHED", 3, "AT_RISK", 2, 1],
    },
    source: "sectors",
    type: "line",
  });
  map.addLayer({
    filter: ["==", ["get", "state"], "AT_RISK"],
    id: "sector-at-risk-dash",
    paint: { "line-color": "#ffe1de", "line-dasharray": [2, 2], "line-width": 1 },
    source: "sectors",
    type: "line",
  });
  map.addSource("trails", {
    data: trailCollection(trails),
    type: "geojson",
  });
  map.addLayer({
    id: "trails",
    paint: { "line-color": "#d3a44e", "line-dasharray": [2, 2], "line-width": 1 },
    source: "trails",
    type: "line",
  });
}

function setLayerVisibility(map: MapLibreMap, visibility: Visibility): void {
  if (map.getLayer("sector-fill") !== undefined) {
    map.setLayoutProperty("sector-fill", "visibility", visibility.sectors ? "visible" : "none");
    map.setLayoutProperty("sector-lines", "visibility", visibility.sectors ? "visible" : "none");
    map.setLayoutProperty(
      "sector-at-risk-dash",
      "visibility",
      visibility.sectors ? "visible" : "none",
    );
    map.setLayoutProperty("trails", "visibility", visibility.trails ? "visible" : "none");
  }
}

export function SearchMap({
  fleet,
  onMarkerSample,
  onSelect,
  scenario,
  selectedIdentifier,
  sectors,
}: SearchMapProps): React.JSX.Element {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const markersRef = useRef(new Map<string, Marker>());
  const markerSampleRef = useRef(onMarkerSample);
  const onSelectRef = useRef(onSelect);
  const sampleWitnessRef = useRef(new Map<string, string>());
  const sectorStateRef = useRef(sectors);
  const trailHistoryRef = useRef(new Map<string, readonly [number, number][]>());
  const [scaleLabel, setScaleLabel] = useState("Scale control active · local viewport");
  const [visibility, setVisibility] = useState<Visibility>(INITIAL_VISIBILITY);
  const [viewport, setViewport] = useState("Local mission view");
  const simulatedFleet = useMemo(
    () => fleet.filter((member) => member.participation === "SIMULATED"),
    [fleet],
  );

  useEffect(() => {
    markerSampleRef.current = onMarkerSample;
    onSelectRef.current = onSelect;
    sectorStateRef.current = sectors;
  }, [onMarkerSample, onSelect, sectors]);

  useEffect(() => {
    const container = containerRef.current;
    if (container === null) return;
    const markers = markersRef.current;
    const sampleWitnesses = sampleWitnessRef.current;
    const trailHistories = trailHistoryRef.current;
    const map = new maplibregl.Map({
      attributionControl: false,
      center: [
        scenario.lastKnownLocation.longitudeMicrodegrees / 1_000_000,
        scenario.lastKnownLocation.latitudeMicrodegrees / 1_000_000,
      ],
      container,
      interactive: false,
      style: emptyStyle,
      zoom: 10,
    });
    mapRef.current = map;
    map.addControl(new maplibregl.ScaleControl({ maxWidth: 100, unit: "metric" }), "bottom-left");
    let active = true;
    map.once("load", () => {
      if (!active) return;
      installGeometry(map, scenario, sectorStateRef.current, trailHistories);
      setLayerVisibility(map, INITIAL_VISIBILITY);
      fitScenario(map, scenario);
      setViewport("Mission bounds fitted automatically");
      setScaleLabel("Scale control active · mission bounds");
    });
    return () => {
      active = false;
      for (const marker of markers.values()) marker.remove();
      markers.clear();
      sampleWitnesses.clear();
      trailHistories.clear();
      mapRef.current = null;
      map.remove();
    };
  }, [scenario]);

  useEffect(() => {
    const map = mapRef.current;
    if (map !== null) updateGeoJson(map, "sectors", sectorCollection(scenario, sectors));
  }, [scenario, sectors]);

  useEffect(() => {
    const map = mapRef.current;
    if (map === null) return;
    setLayerVisibility(map, visibility);
  }, [visibility]);

  useEffect(() => {
    const map = mapRef.current;
    if (map === null) return;
    const active = new Set<string>();
    for (const member of simulatedFleet) {
      active.add(member.identifier);
      let marker = markersRef.current.get(member.identifier);
      if (marker === undefined) {
        const element = document.createElement("div");
        element.className = "drone-marker";
        element.dataset["droneId"] = member.identifier;
        element.dataset["participation"] = "SIMULATED";
        element.setAttribute("aria-hidden", "true");
        element.addEventListener("click", () => {
          onSelectRef.current(member.identifier);
        });
        const createdMarker = new maplibregl.Marker({ element })
          .setLngLat(coordinatesFor(scenario, member))
          .addTo(map);
        markersRef.current.set(member.identifier, createdMarker);
        marker = createdMarker;
      } else {
        marker.setLngLat(coordinatesFor(scenario, member));
      }
      marker.getElement().classList.toggle("selected", member.identifier === selectedIdentifier);
      marker.getElement().dataset["connectivity"] = member.connectivity;
      marker.getElement().title = `${member.identifier} · ${member.connectivity}`;
      marker.getElement().hidden = !visibility.drones;
      const witness = member.telemetry === null ? "none" : JSON.stringify(member.telemetry);
      const previous = sampleWitnessRef.current.get(member.identifier);
      sampleWitnessRef.current.set(member.identifier, witness);
      if (member.telemetry !== null && previous !== witness) {
        const point: [number, number] = [
          member.telemetry.longitudeMicrodegrees / 1_000_000,
          member.telemetry.latitudeMicrodegrees / 1_000_000,
        ];
        const history = trailHistoryRef.current.get(member.identifier) ?? [];
        trailHistoryRef.current.set(
          member.identifier,
          [...history, point].slice(-TRAIL_POINT_LIMIT),
        );
      }
      if (previous !== undefined && previous !== witness)
        markerSampleRef.current(member.identifier);
    }
    for (const [identifier, marker] of markersRef.current) {
      if (!active.has(identifier)) {
        marker.remove();
        markersRef.current.delete(identifier);
        trailHistoryRef.current.delete(identifier);
        sampleWitnessRef.current.delete(identifier);
      }
    }
    updateGeoJson(map, "trails", trailCollection(trailHistoryRef.current));
  }, [scenario, selectedIdentifier, simulatedFleet, visibility.drones]);

  useEffect(() => {
    const map = mapRef.current;
    if (map === null || selectedIdentifier === null) return;
    const selected = simulatedFleet.find(({ identifier }) => identifier === selectedIdentifier);
    if (selected === undefined) return;
    const reducedMotion =
      (window as Partial<Pick<Window, "matchMedia">>).matchMedia?.(
        "(prefers-reduced-motion: reduce)",
      ).matches ?? false;
    map.easeTo({
      center: coordinatesFor(scenario, selected),
      duration: reducedMotion ? 0 : 400,
    });
  }, [scenario, selectedIdentifier, simulatedFleet]);

  function toggle(layer: keyof Visibility): void {
    setVisibility((current) => ({ ...current, [layer]: !current[layer] }));
  }

  function fitMission(): void {
    const map = mapRef.current;
    if (map === null) return;
    fitScenario(map, scenario);
    setViewport("Mission bounds fitted");
    setScaleLabel("Scale control active · mission bounds");
  }

  return (
    <section aria-label="Search map" className="map-panel">
      <div className="map-toolbar">
        <button onClick={fitMission} type="button">
          Fit mission
        </button>
        <label>
          <input
            checked={visibility.sectors}
            onChange={() => {
              toggle("sectors");
            }}
            type="checkbox"
          />{" "}
          Sectors
        </label>
        <label>
          <input
            checked={visibility.drones}
            onChange={() => {
              toggle("drones");
            }}
            type="checkbox"
          />{" "}
          Drones
        </label>
        <label>
          <input
            checked={visibility.trails}
            onChange={() => {
              toggle("trails");
            }}
            type="checkbox"
          />{" "}
          Trails
        </label>
      </div>
      <div className="map-canvas" ref={containerRef} />
      <p aria-label="Map content" className="visually-hidden" role="status">
        {scenario.sectors.length} sector polygons · {simulatedFleet.length} simulated drone markers
        · 0 declared-only markers
      </p>
      <p aria-label="Layer visibility" className="visually-hidden" role="status">
        sectors {visibility.sectors ? "visible" : "hidden"} · drones{" "}
        {visibility.drones ? "visible" : "hidden"} · trails{" "}
        {visibility.trails ? "visible" : "hidden"}
      </p>
      <p aria-label="Map viewport" className="visually-hidden" role="status">
        {viewport}
      </p>
      <div className="map-overlay">
        <ul aria-label="Map legend">
          <li aria-label="Unassigned sector — outline">Unassigned</li>
          <li aria-label="Assigned sector — solid">Assigned</li>
          <li aria-label="At risk sector — diagonal hatch">At risk</li>
          <li aria-label="Searched sector — check mark">Searched ✓</li>
        </ul>
        <p aria-label="Map scale" role="status">
          {scaleLabel}
        </p>
        <small>Synthetic map data · Rendered with MapLibre GL JS</small>
      </div>
    </section>
  );
}
