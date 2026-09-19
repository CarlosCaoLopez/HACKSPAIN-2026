// (x, z) del mundo Minecraft ↔ (lat, lon) reales. SPEC-008 REQ-285.
//
// Es la ÚNICA traducción mundo → coordenada real del dashboard. Es la misma fórmula que `gateway/feeds/anchor.py` (`to_geo`, la
// inversa de `to_world`, REQ-239): equirectangular alrededor del ancla, +X este, +Z sur y
// sin rotación. Si una cambia, la otra también, o el valle cae en otro sitio del mapa.
import type { Geo } from './grid'

/** Lo que `GET /api/feeds` sirve del ancla (`anchor_view` en el gateway). */
export interface FeedAnchor {
  id: string
  place: string
  /** `false` = ancla marcador con coordenadas inventadas: no se pinta como un sitio real. */
  fixed: boolean
  lat0: number
  lon0: number
  meters_per_block: number
  reference_start: string | null
}

export type LatLon = [number, number]

// Mismo valor que `EARTH_RADIUS_M` del gateway.
const EARTH_RADIUS_M = 6_371_008.8

function metersPerDegreeLon(anchor: FeedAnchor): number {
  return (Math.PI / 180) * EARTH_RADIUS_M * Math.cos((anchor.lat0 * Math.PI) / 180)
}

export function toLatLon(anchor: FeedAnchor, x: number, z: number): LatLon {
  const northM = -z * anchor.meters_per_block
  const eastM = x * anchor.meters_per_block
  return [
    anchor.lat0 + ((northM / EARTH_RADIUS_M) * 180) / Math.PI,
    anchor.lon0 + eastM / metersPerDegreeLon(anchor),
  ]
}

/** Las esquinas noroeste y sureste de una celda `cell_<cx>_<cz>`, o `null` si el id no es
 *  una celda. Devuelve `[[latSur, lonOeste], [latNorte, lonEste]]`, el orden de Leaflet. */
export function cellBounds(
  anchor: FeedAnchor,
  cellId: string,
  geo: Geo,
): [LatLon, LatLon] | null {
  const match = /^cell_(-?\d+)_(-?\d+)$/.exec(cellId)
  if (!match) return null
  const x0 = geo.origin[0] + Number(match[1]) * geo.cellSize
  const z0 = geo.origin[1] + Number(match[2]) * geo.cellSize
  // z crece hacia el sur: la esquina de z menor es el norte.
  const north = toLatLon(anchor, x0, z0)
  const south = toLatLon(anchor, x0 + geo.cellSize, z0 + geo.cellSize)
  return [
    [south[0], north[1]],
    [north[0], south[1]],
  ]
}

/** El cuadrado de `sizeM` metros de lado centrado en un punto real (la huella de FIRMS). */
export function squareAround(lat: number, lon: number, sizeM: number, lat0: number): [LatLon, LatLon] {
  const dLat = (sizeM / 2 / EARTH_RADIUS_M) * (180 / Math.PI)
  const dLon = dLat / Math.cos((lat0 * Math.PI) / 180)
  return [
    [lat - dLat, lon - dLon],
    [lat + dLat, lon + dLon],
  ]
}
