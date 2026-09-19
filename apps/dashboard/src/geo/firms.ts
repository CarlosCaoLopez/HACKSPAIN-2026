// Los focos reales de FIRMS que enseña el mapa. SPEC-008 REQ-288.
//
// Dos orígenes, unidos SIN duplicar por su procedencia:
//   1. Eventos `world.fact.asserted` con `source` `api:firms:<sat>:<YYYY-MM-DDTHHMM>:<lat>,<lon>`.
//      La coordenada viaja en el propio `source`, así que funciona en replay y sin red.
//   2. `/api/feeds` → detecciones, que incluyen los focos que caen FUERA de la rejilla del
//      valle y por eso no llegan a ser hecho (`feeds/firms.py`: sin celda no hay hecho).
//
// Con tope (REQ-288): el día del ancla trae más de 1.500 detecciones, y un cuadrado con
// etiqueta por cada una bloquea la vista Mapa. Se enseñan los más cercanos al ancla.
import type { Event, VelaEvent } from '../types'
import type { FeedDetection } from '../hooks/useFeeds'

export interface Focus {
  key: string
  satellite: string
  /** `HH:MM` UTC del pase, para la etiqueta. */
  time: string
  lat: number
  lon: number
  /** Lado de la huella en metros: 375 en VIIRS. */
  sizeM: number
}

const VIIRS_PIXEL_M = 375
/** Los focos que caben en pantalla sin tapar el valle (REQ-288). */
export const MAX_FOCI = 5
/** Dos píxeles VIIRS: por debajo, dos focos se leen como uno solo. */
const MIN_SEPARATION_M = 2 * VIIRS_PIXEL_M
const EARTH_RADIUS_M = 6_371_000
const SOURCE = /^api:firms:([^:]+):(\d{4}-\d{2}-\d{2})T(\d{2})(\d{2}):(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)$/

/** El formato de `feeds/firms.py::source_of`, o `null` si no es de FIRMS o está roto.
 *  Un `source` que no parsea se ignora: un foco sin coordenada no se puede colocar. */
export function parseFirmsSource(source: string): Focus | null {
  const m = SOURCE.exec(source)
  if (!m) return null
  const [, satellite, date, hh, mm, lat, lon] = m
  if (!satellite || !date || !hh || !mm || !lat || !lon) return null
  return {
    key: `${satellite}|${date}T${hh}${mm}|${Number(lat).toFixed(4)},${Number(lon).toFixed(4)}`,
    satellite,
    time: `${hh}:${mm}`,
    lat: Number(lat),
    lon: Number(lon),
    sizeM: VIIRS_PIXEL_M,
  }
}

/** Una detección de `/api/feeds` con la MISMA clave que su `source`, para poder unirlas. */
function fromDetection(d: FeedDetection): Focus | null {
  const t = new Date(d.t_real)
  if (Number.isNaN(t.getTime())) return null
  const pad = (n: number) => String(n).padStart(2, '0')
  const date = `${t.getUTCFullYear()}-${pad(t.getUTCMonth() + 1)}-${pad(t.getUTCDate())}`
  const hh = pad(t.getUTCHours())
  const mm = pad(t.getUTCMinutes())
  return {
    key: `${d.satellite}|${date}T${hh}${mm}|${d.lat.toFixed(4)},${d.lon.toFixed(4)}`,
    satellite: d.satellite,
    time: `${hh}:${mm}`,
    lat: d.lat,
    lon: d.lon,
    sizeM: VIIRS_PIXEL_M,
  }
}

/** Distancia equirectangular: a esta escala (km) el error es despreciable. */
function distanceM(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const rad = Math.PI / 180
  const x = (lon2 - lon1) * rad * Math.cos(((lat1 + lat2) / 2) * rad)
  const y = (lat2 - lat1) * rad
  return Math.hypot(x, y) * EARTH_RADIUS_M
}

/** Los `max` focos más cercanos al ancla, a ≥ `MIN_SEPARATION_M` entre sí: sin la
 *  separación, los cinco más cercanos suelen ser píxeles contiguos del mismo frente. */
function nearestSpread(foci: Focus[], lat0: number, lon0: number, max: number): Focus[] {
  const byDistance = [...foci].sort(
    (a, b) => distanceM(lat0, lon0, a.lat, a.lon) - distanceM(lat0, lon0, b.lat, b.lon),
  )
  const picked: Focus[] = []
  for (const f of byDistance) {
    if (picked.length >= max) break
    if (picked.every((p) => distanceM(p.lat, p.lon, f.lat, f.lon) >= MIN_SEPARATION_M)) picked.push(f)
  }
  return picked
}

export function firmsFoci(
  events: Event[],
  detections: FeedDetection[],
  anchor: { lat0: number; lon0: number },
  max: number = MAX_FOCI,
): Focus[] {
  const out = new Map<string, Focus>()
  for (const envelope of events) {
    if (envelope.type !== 'world.fact.asserted') continue
    const ev = envelope as unknown as VelaEvent
    if (ev.type !== 'world.fact.asserted') continue
    const focus = parseFirmsSource(ev.payload.source)
    if (focus) out.set(focus.key, focus)
  }
  for (const d of detections) {
    const focus = fromDetection(d)
    if (focus && !out.has(focus.key)) out.set(focus.key, focus)
  }
  return nearestSpread([...out.values()], anchor.lat0, anchor.lon0, max)
}
