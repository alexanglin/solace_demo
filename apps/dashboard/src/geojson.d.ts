declare namespace GeoJSON {
  type BBox = number[];
  type Position = number[];
  type GeoJsonProperties = Record<string, unknown> | null;
  interface GeoJsonObject {
    readonly bbox?: BBox;
    readonly type: string;
  }
  interface Point extends GeoJsonObject {
    readonly type: "Point";
    readonly coordinates: Position;
  }
  interface MultiPoint extends GeoJsonObject {
    readonly type: "MultiPoint";
    readonly coordinates: Position[];
  }
  interface LineString extends GeoJsonObject {
    readonly type: "LineString";
    readonly coordinates: Position[];
  }
  interface MultiLineString extends GeoJsonObject {
    readonly type: "MultiLineString";
    readonly coordinates: Position[][];
  }
  interface Polygon extends GeoJsonObject {
    readonly type: "Polygon";
    readonly coordinates: Position[][];
  }
  interface MultiPolygon extends GeoJsonObject {
    readonly type: "MultiPolygon";
    readonly coordinates: Position[][][];
  }
  interface GeometryCollection extends GeoJsonObject {
    readonly type: "GeometryCollection";
    readonly geometries: Geometry[];
  }
  type Geometry =
    Point | MultiPoint | LineString | MultiLineString | Polygon | MultiPolygon | GeometryCollection;
  type GeometryObject = Geometry;
  interface Feature<
    G extends Geometry | null = Geometry,
    P = GeoJsonProperties,
  > extends GeoJsonObject {
    readonly geometry: G;
    readonly id?: string | number;
    readonly properties: P;
    readonly type: "Feature";
  }
  interface FeatureCollection<
    G extends Geometry | null = Geometry,
    P = GeoJsonProperties,
  > extends GeoJsonObject {
    readonly features: Feature<G, P>[];
    readonly type: "FeatureCollection";
  }
  type GeoJSON = Geometry | Feature | FeatureCollection;
}
