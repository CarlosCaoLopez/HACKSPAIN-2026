// (x, z) del mundo Minecraft → coordenadas de SVG. Funciones puras, sin React.
//
// La proyección es de este módulo y de nadie más: el core nunca piensa en píxeles y el
// contrato dice coordenadas del mundo en todas partes. Aquí se traduce una vez.
//
// (Revivido de `apps/dashboard/src/map/project.ts`, borrado en bcc4b5a.)
//
// Aquí el encuadre es FIJO y por eso se ha quitado `growViewBox` y su histéresis del
// 15 %: existía porque el mapa crecía con eventos que llegaban por red. El fuego de
// esta página no puede salir de la caja quemable, así que el marco se conoce de
// antemano y `stroke` es constante todo el run.
//
// El SVG usa el mismo sistema que el mundo (x a la derecha, z hacia abajo, que es el
// sur de Minecraft), así que no hay rotación ni espejo: solo un `viewBox` que encuadra.
// Menos transformaciones, menos sitios donde equivocarse con un signo.
import type { Scenario, Waypoint } from '../../types'

export interface Box {
  minX: number
  minZ: number
  width: number
  height: number
}

export interface Geo {
  origin: [number, number]
  cellSize: number
}

export function geoOf(scenario: Scenario): Geo {
  return { origin: scenario.origin, cellSize: scenario.hazard.cell_size }
}

/** Un `cell_<cx>_<cz>` a su cuadrado en el mundo, o `null` si el id no es una celda.
 *
 *  Devolver `null` en vez de lanzar es deliberado: un id que no parsea acaba en el
 *  carril *sin ubicar* del mapa, con su nombre a la vista. Un `throw` aquí se llevaría
 *  el panel entero por delante por un id raro. */
export function projectCell(cellId: string, geo: Geo): Box | null {
  const match = /^cell_(-?\d+)_(-?\d+)$/.exec(cellId)
  if (!match) return null
  const cx = Number(match[1])
  const cz = Number(match[2])
  const { origin, cellSize } = geo
  return {
    minX: origin[0] + cx * cellSize,
    minZ: origin[1] + cz * cellSize,
    width: cellSize,
    height: cellSize,
  }
}

/** La celda que contiene un punto del mundo: `projectCell` a la inversa.
 *
 *  Es una consulta geométrica sobre el estado vigente, no una predicción: saber que un
 *  pueblo está DENTRO de una celda que arde es mirar; saber que el fuego LLEGARÁ es
 *  propagación, y REQ-072 lo deja a P1. */
export function cellIdAt(x: number, z: number, geo: Geo): string {
  const cx = Math.floor((x - geo.origin[0]) / geo.cellSize)
  const cz = Math.floor((z - geo.origin[1]) / geo.cellSize)
  return `cell_${cx}_${cz}`
}

/** El centro de una celda, para poner un marcador encima. */
export function cellCenter(cellId: string, geo: Geo): { x: number; z: number } | null {
  const box = projectCell(cellId, geo)
  return box ? { x: box.minX + box.width / 2, z: box.minZ + box.height / 2 } : null
}

export function waypointMap(waypoints: Waypoint[]): Map<string, Waypoint> {
  return new Map(waypoints.map((wp) => [wp.id, wp]))
}

/** Rumbo en grados (0 = norte, horario) → grados de rotación del SVG.
 *
 *  Los marcadores se dibujan apuntando hacia arriba (−z, que es el norte de Minecraft),
 *  así que el rumbo del contrato vale tal cual como rotación. */
export function headingRotation(headingDeg: number): number {
  return headingDeg
}

const EMPTY: Box = { minX: 0, minZ: 0, width: 100, height: 100 }

export function boxOf(points: Array<{ x: number; z: number }>, pad: number): Box {
  if (points.length === 0) return EMPTY
  const xs = points.map((p) => p.x)
  const zs = points.map((p) => p.z)
  const minX = Math.min(...xs) - pad
  const minZ = Math.min(...zs) - pad
  return {
    minX,
    minZ,
    width: Math.max(...xs) + pad - minX,
    height: Math.max(...zs) + pad - minZ,
  }
}

export function viewBoxAttr(box: Box): string {
  return `${box.minX} ${box.minZ} ${box.width} ${box.height}`
}

/** Cuánto mide un trazo para que se vea igual con cualquier encuadre.
 *
 *  El SVG escala con el contenedor, así que un `stroke-width` fijo en unidades de mundo
 *  se ve grueso en un mapa pequeño y invisible en uno grande. Se calcula del ancho. */
export function strokeFor(box: Box): number {
  return Math.max(box.width, box.height) / 300
}
