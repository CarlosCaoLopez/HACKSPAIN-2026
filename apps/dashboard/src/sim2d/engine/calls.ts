// Lo que se dice por teléfono. Puerto de las funciones puras de `core/calls.py`.
//
// Ninguna de estas frases es un guion fijo: todas salen del `WorldState` y del `Plan`.
// Es lo que hace que mover el viento cambie lo que el operador dice, y es la mitad de
// la demostración: la llamada no decora la decisión, la transporta.
//
// Regla de oro de `docs/guiones_definitivo.md` que aquí se cumple por construcción: a
// los inmóviles va la AMBULANCIA, nunca el camión. `unit_truck*` no tiene la capacidad
// `transport`, así que el solver le pone coste infinito y nunca aparece en un rescate.
import type { Assignment, Plan, POI, RoadEdge, Unit } from '../../types'

import { angularGap, bearingDeg, fireEtaS } from './geom'
import type { RoadGraph } from './graph'
import type { SimState } from './state'
import { DOWNWIND_HALF_ANGLE_DEG } from './tasks'

export const DEADLINE_MARGIN_MIN = 5

export const UNIT_NAMES: Record<string, string> = {
  fire_truck: 'camión',
  ambulance: 'ambulancia',
  drone: 'dron',
  crew: 'brigada',
}

const UNIT_PLURALS: Record<string, string> = {
  // «camión» pierde la tilde: por regla salía «camiónes» dicho en voz alta.
  camión: 'camiones',
  ambulancia: 'ambulancias',
  dron: 'drones',
  brigada: 'brigadas',
}

export const UNIT_STATUS_WORDS: Record<string, string> = {
  working: 'trabajando el fuego',
  moving: 'de camino',
  idle: 'disponible',
  unavailable: 'fuera de servicio',
}

/** `unit_truck2` → «camión 2», `unit_ambulance` → «ambulancia». */
export function unitName(unit: Unit): string {
  const base = UNIT_NAMES[unit.kind] ?? unit.kind
  const m = /(\d+)$/.exec(unit.id)
  return m?.[1] !== undefined ? `${base} ${m[1]}` : base
}

function plural(n: number, singular: string): string {
  const p = UNIT_PLURALS[singular] ?? `${singular}s`
  return n === 1 ? `${n} ${singular}` : `${n} ${p}`
}

/** `["camión","camión","ambulancia"]` → «1 ambulancia, 2 camiones». */
export function countKinds(kinds: readonly string[]): string {
  const count = new Map<string, number>()
  for (const k of kinds) count.set(k, (count.get(k) ?? 0) + 1)
  return [...count.entries()]
    .sort((a, b) => (a[0] < b[0] ? -1 : 1))
    .map(([k, n]) => plural(n, k))
    .join(', ')
}

/** Nunca «cero minutos»: una unidad que ya está allí llega «en un minuto». */
export function minutes(etaS: number): string {
  const m = Math.max(1, Math.ceil(etaS / 60))
  return m === 1 ? 'un minuto' : `unos ${m} minutos`
}

function bare(edgeId: string): string {
  return edgeId.replace(/^road:/, '')
}

function aliasByEdge(aliases: Record<string, string> | undefined): Map<string, string> {
  const out = new Map<string, string>()
  for (const [alias, edgeId] of Object.entries(aliases ?? {})) {
    if (!out.has(edgeId)) out.set(edgeId, alias)
    if (!out.has(bare(edgeId))) out.set(bare(edgeId), alias)
  }
  return out
}

function edgeName(a: string, b: string, roads: ReadonlyMap<string, RoadEdge>, names: Map<string, string>): string {
  for (const e of roads.values()) {
    if ((e.a === a && e.b === b) || (e.a === b && e.b === a)) {
      return names.get(e.id) ?? names.get(bare(e.id)) ?? bare(e.id)
    }
  }
  return names.get(`${a}-${b}`) ?? names.get(`${b}-${a}`) ?? `${a}-${b}`
}

/** El nombre que se dice por teléfono: «pista sur», «pista norte», o los tramos.
 *  Se cuenta sin el waypoint de salida, porque una unidad puede estar parada en una
 *  pista cortada y salir por la otra. */
export function routeName(
  route: readonly string[],
  roads: ReadonlyMap<string, RoadEdge>,
  aliases?: Record<string, string>,
): string {
  const hops = route.length > 1 ? route.slice(1) : route
  const sur = hops.filter((wp) => wp.toLowerCase().includes('sur')).length
  const nor = hops.filter((wp) => wp.toLowerCase().includes('nor')).length
  if (sur > 0 || nor > 0) {
    if (sur !== nor) return sur > nor ? 'pista sur' : 'pista norte'
    for (let i = hops.length - 1; i >= 0; i--) {
      const low = (hops[i] ?? '').toLowerCase()
      if (low.includes('sur')) return 'pista sur'
      if (low.includes('nor')) return 'pista norte'
    }
  }
  if (route.length < 2) return route[0] ?? ''
  const names = aliasByEdge(aliases)
  const parts: string[] = []
  for (let i = 0; i + 1 < route.length; i++) {
    const a = route[i]
    const b = route[i + 1]
    if (a !== undefined && b !== undefined) parts.push(edgeName(a, b, roads, names))
  }
  return parts.join(', ')
}

/** Qué medios hay y qué están haciendo AHORA. Sale del estado, no del guion: es lo
 *  que el operador puede decir por teléfono sin inventarse nada. */
export function resourcesLine(state: SimState): string {
  const byStatus = new Map<string, string[]>()
  for (const unit of [...state.units.values()].sort((a, b) => (a.id < b.id ? -1 : 1))) {
    const word = UNIT_STATUS_WORDS[unit.status] ?? unit.status
    const list = byStatus.get(word) ?? []
    list.push(UNIT_NAMES[unit.kind] ?? unit.kind)
    byStatus.set(word, list)
  }
  const parts = [...byStatus.entries()].map(([word, kinds]) => `${countKinds(kinds)} ${word}`)
  return parts.length > 0 ? parts.join('; ') : 'sin medios registrados'
}

/** Lo que se le PIDE al medio: qué unidades, por qué ruta y en cuánto. Sale del
 *  `Plan`: es la decisión del solver dicha en voz alta, y es lo único que la dotación
 *  puede confirmar o negar por teléfono. */
export function requestedUnitsLine(
  state: SimState,
  assignments: readonly Assignment[],
  aliases?: Record<string, string>,
): string {
  const pairs = [...assignments]
    .sort((a, b) => (a.unit_id < b.unit_id ? -1 : 1))
    .map((a) => ({ a, u: state.units.get(a.unit_id) }))
    .filter((p): p is { a: Assignment; u: Unit } => p.u !== undefined)
  if (pairs.length === 0) return 'todavía no hay ninguna unidad asignada'
  const detail = pairs
    .map(({ a, u }) => `${unitName(u)} por ${routeName(a.route, state.roads, aliases)}, ${minutes(a.eta_s)}`)
    .join('; ')
  return `${countKinds(pairs.map(({ u }) => UNIT_NAMES[u.kind] ?? u.kind))}: ${detail}`
}

/** Los medios que van DE VERDAD, y los que han dicho que no pueden. Es la diferencia
 *  entre lo que se pidió y lo que salió: al pueblo no se le promete un camión hasta
 *  que su dotación ha dicho «vamos». Un «no ha podido salir» se dice también. */
export function committedResourcesLine(
  state: SimState,
  assignments: readonly Assignment[],
  refused: readonly Unit[],
  aliases?: Record<string, string>,
): string {
  const parts: string[] = []
  if (assignments.length > 0) parts.push(`van ${requestedUnitsLine(state, assignments, aliases)}`)
  if (refused.length > 0) {
    const names = countKinds(refused.map((u) => UNIT_NAMES[u.kind] ?? u.kind))
    parts.push(`${names} no ${refused.length === 1 ? 'ha podido' : 'han podido'} salir`)
  }
  return parts.length > 0 ? parts.join('; ') : 'todavía no hay ningún medio confirmado'
}

function burningArea(nCells: number, graph: RoadGraph): string {
  const ha = (nCells * graph.geo.cellSize ** 2) / 10000
  if (ha < 1) return 'menos de una hectárea'
  return Math.round(ha) === 1 ? 'una hectárea' : `unas ${Math.round(ha)} hectáreas`
}

/** Lo que el plan NO cubre, dicho como se le puede decir a alguien: en superficie
 *  ardiendo y medios encima. «284 tareas sin cubrir» no es una frase. */
export function coverageLine(state: SimState, plan: Plan, graph: RoadGraph): string {
  let burning = 0
  for (const c of state.cells.values()) if (c.state === 'burning') burning++
  if (burning === 0) return 'no hay superficie ardiendo ahora mismo'
  const onIt: string[] = []
  for (const a of plan.assignments) {
    const u = state.units.get(a.unit_id)
    const t = state.tasks.get(a.task_id)
    if (u && t && t.kind === 'extinguish') onIt.push(UNIT_NAMES[u.kind] ?? u.kind)
  }
  const area = burningArea(burning, graph)
  const uncovered = plan.unassigned_tasks.some((tid) => state.tasks.get(tid)?.kind === 'extinguish')
  if (onIt.length === 0) return `hay ${area} ardiendo y ningún medio encima`
  const medios = countKinds(onIt)
  return uncovered
    ? `hay ${area} ardiendo y solo ${medios} encima: no llegamos a todo el frente`
    : `hay ${area} ardiendo, cubierta con ${medios}`
}

/** (metros al frente más cercano, si el viento lo empuja hacia el POI). */
export function nearestFire(state: SimState, poi: POI, graph: RoadGraph): [number, boolean] {
  let best = Infinity
  let downwind = false
  const push = (state.wind.bearing_deg + 180) % 360
  const size = graph.geo.cellSize
  for (const cell of state.cells.values()) {
    if (cell.state !== 'burning') continue
    const cx = graph.geo.origin[0] + (cell.cx + 0.5) * size
    const cz = graph.geo.origin[1] + (cell.cz + 0.5) * size
    const d = Math.hypot(poi.x - cx, poi.z - cz)
    if (d < best) {
      best = d
      downwind = state.wind.speed > 0 && angularGap(bearingDeg(cx, cz, poi.x, poi.z), push) <= DOWNWIND_HALF_ANGLE_DEG
    }
  }
  return [best, downwind]
}

export function fireLine(state: SimState, poi: POI, graph: RoadGraph): string {
  const [best, downwind] = nearestFire(state, poi, graph)
  if (!Number.isFinite(best)) return 'no hay ningún frente activo cerca ahora mismo'
  const metros = Math.round(best / 10) * 10
  const empuje = downwind ? ' y el viento lo empuja hacia ustedes' : ' y el viento no lo empuja hacia ustedes'
  return `el frente más cercano está a unos ${metros} metros${empuje}`
}

export function roadsLine(state: SimState, aliases?: Record<string, string>): string {
  const names = aliasByEdge(aliases)
  const cut: string[] = []
  for (const edge of [...state.roads.values()].sort((a, b) => (a.id < b.id ? -1 : 1))) {
    if (!edge.cut) continue
    const name = names.get(edge.id) ?? names.get(bare(edge.id)) ?? bare(edge.id)
    cut.push(edge.cut_cause ? `${name} (cortada por ${edge.cut_cause})` : `${name} (cortada)`)
  }
  return cut.length === 0 ? 'no hay ninguna carretera cortada' : `carreteras cortadas: ${cut.join(', ')}`
}

export function unitEtaLine(state: SimState, assignment: Assignment | null): string {
  if (!assignment) return 'todavía no hay ninguna unidad asignada a su pueblo'
  const unit = state.units.get(assignment.unit_id)
  if (!unit) return 'todavía no hay ninguna unidad asignada a su pueblo'
  const name = unitName(unit)
  const articulo = name.startsWith('camión') || name.startsWith('dron') ? 'el' : 'la'
  return `va ${articulo} ${name} y llega en ${minutes(assignment.eta_s)}`
}

/** Minutos que tarda el frente más cercano en llegar al POI con el viento de ahora. */
export function fireEtaMin(state: SimState, poi: POI, graph: RoadGraph): number {
  let best = Infinity
  const size = graph.geo.cellSize
  for (const cell of state.cells.values()) {
    if (cell.state !== 'burning') continue
    const cx = graph.geo.origin[0] + (cell.cx + 0.5) * size
    const cz = graph.geo.origin[1] + (cell.cz + 0.5) * size
    best = Math.min(best, fireEtaS(cx, cz, poi.x, poi.z, state.wind, size, graph.baseSpread))
  }
  return Number.isFinite(best) ? best / 60 : Infinity
}

/** El plazo que se dicta al pueblo: el ETA del medio más el margen, o, sin medio, lo
 *  que tarda el fuego menos el margen. */
export function deadlineMin(state: SimState, poi: POI, graph: RoadGraph, assignment: Assignment | null): number {
  if (assignment) return Math.ceil(assignment.eta_s / 60) + DEADLINE_MARGIN_MIN
  const eta = fireEtaMin(state, poi, graph)
  if (!Number.isFinite(eta)) return DEADLINE_MARGIN_MIN
  return Math.max(1, Math.trunc(eta) - DEADLINE_MARGIN_MIN)
}

export function peopleLine(state: SimState, poiId: string): { count: number; immobile: number } {
  let count = 0
  let immobile = 0
  for (const g of state.civilians.values()) {
    if (g.poi_id !== poiId || g.state === 'safe') continue
    count += g.count
    immobile += g.immobile
  }
  return { count, immobile }
}
