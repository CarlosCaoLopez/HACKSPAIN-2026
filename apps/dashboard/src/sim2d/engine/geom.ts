// La geometría del mundo: rumbos, viento, celdas y el tiempo que tarda el fuego en llegar.
//
// En Python esto vive partido entre `sim/hazard.py` (el viento) y `core/solver.py` (los
// rumbos y `fire_eta_s`). Aquí va junto porque si no `fire.ts` y `solver.ts` se importan
// en círculo, y porque son la misma trigonometría: un solo sitio donde equivocarse de signo.
//
// Las dos convenciones que hay que tener delante todo el rato:
//   · `bearing_deg` es de dónde VIENE el viento (0 = norte, horario). Empuja hacia
//     `bearing + 180`. El YAML lo dice: «270: viene del oeste, empuja hacia el este».
//   · El norte es −z. z crece hacia el SUR, que en pantalla es hacia abajo.

/** Rumbo de (x,z) a (tx,tz) en grados, 0 = norte, horario. */
export function bearingDeg(x: number, z: number, tx: number, tz: number): number {
  return ((Math.atan2(tx - x, -(tz - z)) * 180) / Math.PI + 360) % 360
}

/** La diferencia angular más corta entre dos rumbos, 0..180. */
export function angularGap(a: number, b: number): number {
  const d = Math.abs(a - b) % 360
  return d > 180 ? 360 - d : d
}

/** Hacia dónde SOPLA un viento con ese rumbo, como vector unitario del mundo (dx, dz). */
export function windVector(bearingDeg_: number): { dx: number; dz: number } {
  const towards = ((bearingDeg_ + 180) * Math.PI) / 180
  return { dx: Math.sin(towards), dz: -Math.cos(towards) }
}

/** Lo mismo, más el rumbo de empuje en grados, que es lo que se pinta y se dice. */
export function blowTo(bearingDeg_: number): { dx: number; dz: number; deg: number } {
  return { ...windVector(bearingDeg_), deg: (bearingDeg_ + 180) % 360 }
}

export interface Geo {
  origin: [number, number]
  cellSize: number
}

export function cellIdOf(x: number, z: number, geo: Geo): string {
  const cx = Math.floor((x - geo.origin[0]) / geo.cellSize)
  const cz = Math.floor((z - geo.origin[1]) / geo.cellSize)
  return `cell_${cx}_${cz}`
}

const CELL_RE = /^cell_(-?\d+)_(-?\d+)$/

export function parseCell(cellId: string): [number, number] | null {
  const m = CELL_RE.exec(cellId)
  if (!m || m[1] === undefined || m[2] === undefined) return null
  return [Number(m[1]), Number(m[2])]
}

export function cellId(cx: number, cz: number): string {
  return `cell_${cx}_${cz}`
}

/** El centro de una celda en coordenadas del mundo. */
export function cellCenter(id: string, geo: Geo): [number, number] | null {
  const parsed = parseCell(id)
  if (!parsed) return null
  const [cx, cz] = parsed
  return [
    geo.origin[0] + (cx + 0.5) * geo.cellSize,
    geo.origin[1] + (cz + 0.5) * geo.cellSize,
  ]
}

/** Cuánto tarda el frente en llegar de un punto a otro, en segundos.
 *
 *  La tasa cae con el coseno del ángulo entre la dirección al objetivo y la del empuje
 *  del viento: de espaldas al viento solo queda `base_spread`. Es la misma fórmula que
 *  `solver.fire_eta_s`, y es la que decide qué pueblo está más amenazado. */
export function fireEtaS(
  x: number,
  z: number,
  tx: number,
  tz: number,
  wind: { bearing_deg: number; speed: number },
  cellSize: number,
  baseSpread: number,
): number {
  const d = Math.hypot(tx - x, tz - z)
  if (d === 0) return 0
  const push = (wind.bearing_deg + 180) % 360
  const gap = angularGap(bearingDeg(x, z, tx, tz), push)
  const rate = baseSpread + wind.speed * Math.max(0, Math.cos((gap * Math.PI) / 180))
  if (rate <= 0) return Infinity
  return (d / (cellSize * rate)) * 60
}
