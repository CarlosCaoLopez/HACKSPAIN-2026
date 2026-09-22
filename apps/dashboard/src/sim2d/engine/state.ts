// El estado del mundo simulado y cómo se le aplican los cambios.
//
// Es `WorldState` de `contracts` con `Map` en vez de `Record`: el bucle del fuego
// toca cientos de celdas por tick y copiar un objeto plano cada vez sería tirar
// tiempo. La conversión a `WorldState` de verdad la hace `toWorldState`, y solo la
// necesitan las piezas que ya están escritas contra el contrato.
//
// El invariante 5 («`WorldState` es inmutable: `apply(state, ev)` devuelve uno nuevo»)
// se respeta en el espíritu que importa aquí: nadie fuera de este módulo muta nada, y
// cada tick publica una identidad nueva para React. Por dentro los `Map` se mutan a
// propósito, igual que `useWorldView` hace en el dashboard de verdad y por la misma
// razón, que está documentada allí: copiar los Map con 500 eventos es tirar tiempo.
import type {
  Cell,
  CellState,
  CivilianGroup,
  Fact,
  POI,
  RoadEdge,
  Scenario,
  Task,
  Unit,
  Wind,
  WorldState,
} from '../../types'

import type { CellChange } from './fire'

export interface SimState {
  run_id: string
  seq: number
  t_sim: number
  wind: Wind
  units: Map<string, Unit>
  cells: Map<string, Cell>
  roads: Map<string, RoadEdge>
  pois: Map<string, POI>
  civilians: Map<string, CivilianGroup>
  tasks: Map<string, Task>
  facts: Fact[]
  /** Por qué cambió cada celda la última vez. `extinguished` es la que importa
   *  pintar: una celda que apagó un camión no es una que se quemó sola. */
  cellCauses: Map<string, CellChange['cause']>
}

export function initialState(scenario: Scenario, runId: string): SimState {
  return {
    run_id: runId,
    seq: 0,
    t_sim: 0,
    wind: { ...scenario.hazard.wind },
    units: new Map(scenario.units.map((u) => [u.id, { ...u }])),
    // Las celdas se materializan según el fuego las toca, igual que `core.belief`.
    cells: new Map(),
    roads: new Map(scenario.roads.map((r) => [r.id, { ...r }])),
    pois: new Map(scenario.pois.map((p) => [p.id, { ...p }])),
    civilians: new Map(scenario.civilians.map((c) => [c.id, { ...c }])),
    tasks: new Map(),
    facts: [],
    cellCauses: new Map(),
  }
}

const CELL_RE = /^cell_(-?\d+)_(-?\d+)$/

export function applyCellChange(s: SimState, ch: CellChange): void {
  const m = CELL_RE.exec(ch.cell_id)
  const cx = m?.[1] !== undefined ? Number(m[1]) : 0
  const cz = m?.[2] !== undefined ? Number(m[2]) : 0
  if (ch.state === 'intact') {
    s.cells.delete(ch.cell_id)
    s.cellCauses.delete(ch.cell_id)
    return
  }
  s.cells.set(ch.cell_id, {
    id: ch.cell_id,
    cx,
    cz,
    state: ch.state,
    fuel: 1,
    t_changed: s.t_sim,
  })
  s.cellCauses.set(ch.cell_id, ch.cause)
}

export function setUnit(s: SimState, id: string, patch: Partial<Unit>): void {
  const before = s.units.get(id)
  if (!before) return
  s.units.set(id, { ...before, ...patch })
}

export function setCivilians(s: SimState, id: string, patch: Partial<CivilianGroup>): void {
  const before = s.civilians.get(id)
  if (!before) return
  s.civilians.set(id, { ...before, ...patch })
}

export function cutRoad(s: SimState, edgeId: string, cut: boolean, cause: string | null): void {
  const before = s.roads.get(edgeId)
  if (!before) return
  s.roads.set(edgeId, { ...before, cut, cut_cause: cut ? cause : null })
}

export function cutEdges(s: SimState): Set<string> {
  const out = new Set<string>()
  for (const road of s.roads.values()) if (road.cut) out.add(road.id)
  return out
}

/** Aserta un hecho. `belief.apply_fact` hace además una cosa que hay que copiar: un
 *  `unit:<id>:available` mueve el estado de la unidad sin emitir `world.unit.status`.
 *  Es como una dotación dice por teléfono que no puede salir. */
export function assertFact(s: SimState, fact: Fact): void {
  s.facts = [...s.facts, fact]
  const m = /^unit:([^:]+):available$/.exec(fact.key)
  if (m?.[1] !== undefined) {
    setUnit(s, m[1], { status: fact.value ? 'idle' : 'unavailable' })
  }
}

/** El último hecho con esa clave, que es el que vale. */
export function latestFact(s: SimState, key: string): Fact | null {
  for (let i = s.facts.length - 1; i >= 0; i--) {
    const f = s.facts[i]
    if (f && f.key === key) return f
  }
  return null
}

export function burningCells(s: SimState): string[] {
  const out: string[] = []
  for (const [id, cell] of s.cells) if (cell.state === 'burning') out.push(id)
  return out.sort()
}

export function cellsWith(s: SimState, state: CellState): string[] {
  const out: string[] = []
  for (const [id, cell] of s.cells) if (cell.state === state) out.push(id)
  return out.sort()
}

export function setTask(s: SimState, task: Task): void {
  s.tasks.set(task.id, task)
}

/** La vista de contrato, para lo que ya está escrito contra `WorldState`. */
export function toWorldState(s: SimState): WorldState {
  return {
    run_id: s.run_id,
    seq: s.seq,
    t_sim: s.t_sim,
    wind: s.wind,
    units: Object.fromEntries(s.units),
    cells: Object.fromEntries(s.cells),
    roads: Object.fromEntries(s.roads),
    pois: Object.fromEntries(s.pois),
    civilians: Object.fromEntries(s.civilians),
    tasks: Object.fromEntries(s.tasks),
    facts: s.facts,
  }
}
