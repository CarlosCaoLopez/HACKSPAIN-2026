// De dónde salen las tareas. Puerto de `packages/core/src/core/tasks.py`.
//
// Es la pieza que decide QUÉ hay que hacer, antes de que nadie decida quién lo hace.
// Tres familias:
//   · `extinguish` — una por FRENTE (componente conexa de celdas ardiendo), no una
//     por celda: con una por celda salían ciento cuarenta tareas y otros tantos planes.
//   · `evacuate`   — al pueblo más cercano al fuego, y a cualquiera con el fuego a
//     menos de 60 m. Por geometría, no por tiempo: es la diferencia que hace que al
//     girar el viento cambie antes el frente que la orden de evacuación.
//   · `rescue`     — nace de un hecho `poi:<id>:immobile`/`:injuries` que ha entrado
//     por una llamada. Nunca de un `assumed_default` (invariante 8).
//
// Del original se simplifican a propósito las dos histéresis de elección de celda
// cabeza (`HEAD_BAND_S`, `INTERCEPT_HYSTERESIS`) y la herencia de componentes entre
// ticks: existen para que el backend no cambie de destino cada segundo con posiciones
// reales. Si se ve parpadeo en los destinos, es aquí donde se añaden.
import type { Cell, POI, Task, TaskSeverity, Wind } from '../../types'

import { angularGap, bearingDeg, fireEtaS } from './geom'
import type { RoadGraph } from './graph'
import { latestFact, type SimState } from './state'

export const EVAC_STATES: ReadonlySet<string> = new Set(['exposed', 'warned'])
/** El mismo 60 que `loop.AT_THE_DOOR_M`: «el fuego en la puerta». */
export const CRITICAL_DISTANCE_M = 60.0
export const DOWNWIND_DISTANCE_M = 200.0
export const DOWNWIND_HALF_ANGLE_DEG = 45.0
export const MERGE_RADIUS = 2
export const FAR_HORIZON_S = 3600.0

const LEVELS: readonly TaskSeverity[] = ['low', 'medium', 'high', 'critical']

/** El vecindario dilatado con el que dos celdas cuentan como el mismo frente. */
const NEIGHBOURHOOD: Array<[number, number]> = (() => {
  const out: Array<[number, number]> = []
  for (let dx = -MERGE_RADIUS; dx <= MERGE_RADIUS; dx++) {
    for (let dz = -MERGE_RADIUS; dz <= MERGE_RADIUS; dz++) {
      if (dx !== 0 || dz !== 0) out.push([dx, dz])
    }
  }
  return out
})()

export function frontTaskId(cellId: string): string {
  return `task_front_${cellId.replace(/^cell_/, '')}`
}
export function evacTaskId(poiId: string): string {
  return `task_evac_${poiId}`
}
export function rescueTaskId(poiId: string): string {
  return `task_rescue_${poiId}`
}

/** Componentes conexas de las celdas `burning`, cada una ordenada por id. Puro. */
export function fronts(cells: ReadonlyMap<string, Cell>): Cell[][] {
  const burning = new Map<string, Cell>()
  for (const c of cells.values()) if (c.state === 'burning') burning.set(`${c.cx},${c.cz}`, c)
  const seen = new Set<string>()
  const out: Cell[][] = []
  const keys = [...burning.keys()].sort((a, b) => {
    const ca = burning.get(a)
    const cb = burning.get(b)
    return (ca?.id ?? '') < (cb?.id ?? '') ? -1 : 1
  })
  for (const key of keys) {
    if (seen.has(key)) continue
    seen.add(key)
    const stack = [key]
    const comp: Cell[] = []
    while (stack.length > 0) {
      const cur = stack.pop()
      if (cur === undefined) break
      const cell = burning.get(cur)
      if (!cell) continue
      comp.push(cell)
      for (const [dx, dz] of NEIGHBOURHOOD) {
        const nb = `${cell.cx + dx},${cell.cz + dz}`
        if (burning.has(nb) && !seen.has(nb)) {
          seen.add(nb)
          stack.push(nb)
        }
      }
    }
    out.push(comp.sort((a, b) => (a.id < b.id ? -1 : 1)))
  }
  return out
}

/** ¿El viento empuja el fuego que hay en (x, z) hacia el POI (±45°)? */
export function downwind(x: number, z: number, poi: POI, wind: Wind): boolean {
  if (wind.speed <= 0) return false
  const push = (wind.bearing_deg + 180) % 360
  return angularGap(bearingDeg(x, z, poi.x, poi.z), push) <= DOWNWIND_HALF_ANGLE_DEG
}

function peopledPois(state: SimState): POI[] {
  const withPeople = new Set<string>()
  for (const g of state.civilians.values()) if (g.count > 0 && g.state !== 'safe') withPeople.add(g.poi_id)
  return [...state.pois.values()].filter((p) => withPeople.has(p.id)).sort((a, b) => (a.id < b.id ? -1 : 1))
}

function cellCenter(cell: Cell, graph: RoadGraph): [number, number] {
  const size = graph.geo.cellSize
  return [
    graph.geo.origin[0] + (cell.cx + 0.5) * size,
    graph.geo.origin[1] + (cell.cz + 0.5) * size,
  ]
}

function rankSeverity(rank: number, threatS: number, attackable: boolean): TaskSeverity {
  let level = threatS > FAR_HORIZON_S ? 1 : rank === 0 ? 3 : rank === 1 ? 2 : 1
  if (!attackable) level = Math.max(level - 1, 0)
  return LEVELS[level] ?? 'medium'
}

function fireDistanceM(poi: POI, burning: readonly Cell[], graph: RoadGraph): number {
  let best = Infinity
  for (const cell of burning) {
    const [cx, cz] = cellCenter(cell, graph)
    best = Math.min(best, Math.hypot(poi.x - cx, poi.z - cz))
  }
  return best
}

/** Qué pueblos toca evacuar: el más cercano al fuego, y los que lo tengan en la
 *  puerta (≤ 60 m). Al resto se le AVISA, que no es lo mismo. */
export function toEvacuate(state: SimState, burning: readonly Cell[], graph: RoadGraph): Set<string> {
  const villages = [...state.pois.values()].filter((p) => p.kind === 'village').sort((a, b) => (a.id < b.id ? -1 : 1))
  if (villages.length === 0 || burning.length === 0) return new Set()
  const dist = new Map(villages.map((p) => [p.id, fireDistanceM(p, burning, graph)]))
  let closest = villages[0]
  for (const p of villages) {
    if ((dist.get(p.id) ?? Infinity) < (dist.get(closest?.id ?? '') ?? Infinity)) closest = p
  }
  const out = new Set<string>()
  if (closest) out.add(closest.id)
  for (const p of villages) if ((dist.get(p.id) ?? Infinity) <= CRITICAL_DISTANCE_M) out.add(p.id)
  return out
}

function evacSeverity(poi: POI, burning: readonly Cell[], wind: Wind, graph: RoadGraph): TaskSeverity {
  for (const cell of burning) {
    const [cx, cz] = cellCenter(cell, graph)
    const d = Math.hypot(poi.x - cx, poi.z - cz)
    if (d <= CRITICAL_DISTANCE_M) return 'critical'
    if (d <= DOWNWIND_DISTANCE_M && downwind(cx, cz, poi, wind)) return 'critical'
  }
  return 'high'
}

const RANK: Record<TaskSeverity, number> = { low: 0, medium: 1, high: 2, critical: 3 }

export interface SyncResult {
  changed: Task[]
  /** Qué POI amenaza cada frente: lo usa la explicación y el guion de la llamada. */
  threatened: Map<string, string>
}

/** Las tareas que nacen, cambian o se cierran en este tick. */
export function syncTasks(state: SimState, graph: RoadGraph): SyncResult {
  const changed: Task[] = []
  const threatened = new Map<string, string>()
  const burningCells = [...state.cells.values()].filter((c) => c.state === 'burning')
  const pois = peopledPois(state)

  // --- extinguish: una por frente ---
  const comps = fronts(state.cells)
  const evaluated = comps.map((comp) => {
    let threatS = Infinity
    let target = comp[0]?.id ?? ''
    let who = ''
    for (const cell of comp) {
      const [cx, cz] = cellCenter(cell, graph)
      for (const poi of pois) {
        const eta = fireEtaS(cx, cz, poi.x, poi.z, state.wind, graph.geo.cellSize, graph.baseSpread)
        if (eta < threatS) {
          threatS = eta
          target = cell.id
          who = poi.id
        }
      }
    }
    const center = comp.find((c) => c.id === target)
    const attackable =
      center !== undefined && graph.attackableFrom(...cellCenter(center, graph)) !== null
    return { comp, threatS, target, attackable, who }
  })
  evaluated.sort((a, b) => a.threatS - b.threatS || (a.target < b.target ? -1 : 1))

  const liveFrontIds = new Set<string>()
  evaluated.forEach((f, rank) => {
    const id = frontTaskId(f.target)
    liveFrontIds.add(id)
    if (f.who) threatened.set(id, f.who)
    const severity = rankSeverity(rank, f.threatS, f.attackable)
    const before = state.tasks.get(id)
    if (before && !before.done && before.severity === severity && before.target_cell === f.target) return
    changed.push({
      id,
      kind: 'extinguish',
      target_poi: null,
      target_cell: f.target,
      target_x: null,
      target_z: null,
      required_capability: 'extinguish',
      severity,
      created_t: before?.created_t ?? state.t_sim,
      done: false,
    })
  })
  // Un frente que se apagó cierra su tarea.
  for (const task of state.tasks.values()) {
    if (task.kind !== 'extinguish' || task.done || liveFrontIds.has(task.id)) continue
    changed.push({ ...task, done: true })
  }

  // --- evacuate ---
  const toEvac = toEvacuate(state, burningCells, graph)
  for (const poi of [...state.pois.values()].sort((a, b) => (a.id < b.id ? -1 : 1))) {
    if (poi.kind !== 'village') continue
    const id = evacTaskId(poi.id)
    const before = state.tasks.get(id)
    const groups = [...state.civilians.values()].filter((g) => g.poi_id === poi.id)
    const pending = groups.filter((g) => EVAC_STATES.has(g.state) && g.count > 0)

    if (before && !before.done) {
      // Se cierra cuando todos sus grupos están a salvo. La gravedad solo sube.
      if (groups.every((g) => g.state === 'safe')) {
        changed.push({ ...before, done: true })
        continue
      }
      const severity = evacSeverity(poi, burningCells, state.wind, graph)
      if (RANK[severity] > RANK[before.severity]) changed.push({ ...before, severity })
      continue
    }
    if (burningCells.length === 0 || !toEvac.has(poi.id) || pending.length === 0) continue
    changed.push({
      id,
      kind: 'evacuate',
      target_poi: poi.id,
      target_cell: null,
      target_x: null,
      target_z: null,
      required_capability: 'transport',
      severity: evacSeverity(poi, burningCells, state.wind, graph),
      created_t: state.t_sim,
      done: false,
    })
  }

  // --- rescue: solo desde un hecho observado (invariante 8) ---
  for (const poi of [...state.pois.values()].sort((a, b) => (a.id < b.id ? -1 : 1))) {
    const immobile = latestFact(state, `poi:${poi.id}:immobile`)
    const injuries = latestFact(state, `poi:${poi.id}:injuries`)
    const solid = [immobile, injuries].filter(
      (f) => f !== null && f.kind !== 'assumed_default' && Number(f.value) > 0,
    )
    const id = rescueTaskId(poi.id)
    const before = state.tasks.get(id)
    if (solid.length === 0) continue
    if (before && !before.done) continue
    const point = latestFact(state, `poi:${poi.id}:rescue_point`)
    let tx: number | null = null
    let tz: number | null = null
    if (point && point.kind === 'observed' && typeof point.value === 'string') {
      const [sx, sz] = point.value.split(',')
      if (sx !== undefined && sz !== undefined) {
        tx = Number(sx)
        tz = Number(sz)
      }
    }
    changed.push({
      id,
      kind: 'rescue',
      target_poi: poi.id,
      target_cell: null,
      target_x: tx,
      target_z: tz,
      required_capability: 'transport',
      severity: 'critical',
      created_t: state.t_sim,
      done: false,
    })
  }

  return { changed, threatened }
}

/** Llegar al waypoint de un POI cierra sus tareas de evacuación y rescate. */
export function arrivalCloses(state: SimState, waypointId: string): Task[] {
  const out: Task[] = []
  for (const task of state.tasks.values()) {
    if (task.done || (task.kind !== 'evacuate' && task.kind !== 'rescue')) continue
    const poi = task.target_poi ? state.pois.get(task.target_poi) : undefined
    if (poi && poi.waypoint_id === waypointId) out.push({ ...task, done: true })
  }
  return out
}
