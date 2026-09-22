// El solver. Puerto de `packages/core/src/core/solver.py`.
//
// El LLM nunca asigna recursos: produce una `Policy` (pesos + restricciones) y ESTO
// produce el `Plan`. Toda la influencia de la Policy pasa por `weightedCost`, que es
// una multiplicación de factores — y por eso se puede enseñar entera en pantalla.
//
// Se porta fielmente, incluidas las constantes. Lo que NO se porta y se deja anotado:
// `HOLD_PENALTY_M` y `STICKY_BIAS_M`, que en el backend evitan que una unidad cambie
// de destino cada segundo con posiciones reales. Aquí se empieza sin ellos; si se ve
// parpadeo en los destinos, este es el sitio.
import type {
  Assignment,
  Assumption,
  Plan,
  PlanContext,
  Policy,
  Task,
  TaskSeverity,
  Unit,
  Violation,
} from '../../types'

import type { RoadGraph } from './graph'
import { match } from './hungarian'
import type { SimState } from './state'

export const INFEASIBLE = Infinity
export const RESCUE_FACTOR = 0.5
/** El mismo 4.0 que `sim.runner.DEFAULT_SPEED_MPS`. Si divergen, el ETA que dice la
 *  llamada por teléfono no es el que se ve andar por el mapa. */
export const UNIT_SPEED_MPS = 4.0
export const WEIGHT_DISCOUNT = 0.35
export const ASSUMED_WEIGHT = 2.0

export const SEVERITY_FACTOR: Record<TaskSeverity, number> = {
  critical: 0.25,
  high: 0.5,
  medium: 1.0,
  low: 1.5,
}

const SEVERITY_RANK: Record<TaskSeverity, number> = { low: 0, medium: 1, high: 2, critical: 3 }

export const WEIGHT_NAMES = [
  'life_safety',
  'immobile_first',
  'structure_protection',
  'containment',
  'response_time',
] as const
export type WeightName = (typeof WEIGHT_NAMES)[number]

// --- la traza: lo que `explain.ts` convierte en «la ecuación» ---

export interface CostTerm {
  /** El nombre del factor tal cual se dice: `gravedad crítica`, `immobile_first 0,90`. */
  label: string
  factor: number
  /** De dónde sale: un peso del catálogo, la gravedad, el factor de rescate. */
  source: 'severity' | 'rescue' | WeightName
}

export interface CostTrace {
  baseM: number
  offroadM: number
  terms: CostTerm[]
  total: number
  /** Pesos con valor > 0 cuyo predicado NO se cumple: se dicen también, en gris.
   *  Que un camión no se beneficie de `life_safety` es tan explicativo como que sí. */
  inapplicable: Array<{ name: WeightName; value: number; why: string }>
}

export interface Infeasibility {
  unitId: string
  taskId: string
  reason: 'capability' | 'no_route' | 'burning_cell' | 'civilian_route' | 'no_target' | 'reserved'
}

/** Un medio guardado a propósito, con el porqué ya redactado. */
export interface Reserved {
  capability: string
  /** Cuántas unidades de esa capacidad se dejan libres. */
  n: number
}

export interface SolveResult {
  plan: Plan
  violations: Violation[]
  traces: Map<string, CostTrace>
  /** Por tarea sin cubrir, por qué no la cogió nadie. */
  infeasible: Infeasibility[]
  /** Lo que la Policy ha guardado, para poder explicarlo. */
  reserves: Reserved[]
  /** Qué unidades quedan libres por la reserva. */
  reservedUnits: string[]
}

// --- coste ---

function isWindward(state: SimState, task: Task): boolean {
  if (!task.target_cell) return false
  const cell = state.cells.get(task.target_cell)
  return cell !== undefined && (cell.state === 'burning' || cell.state === 'at_risk')
}

const WHY_NOT: Record<WeightName, string> = {
  life_safety: 'no hay civiles expuestos en el objetivo de esta tarea',
  immobile_first: 'no consta nadie inmóvil en el objetivo de esta tarea',
  structure_protection: 'la tarea no apunta a un pueblo, hospital ni refugio',
  containment: 'no es una tarea de extinción sobre una celda activa',
  response_time: '',
}

/** El corazón: coste = metros × gravedad × (rescate) × Π descuentos × penalización. */
export function weightedCost(
  base: number,
  state: SimState,
  task: Task,
  policy: Policy,
): CostTrace {
  const terms: CostTerm[] = []
  const inapplicable: CostTrace['inapplicable'] = []

  const sevFactor = SEVERITY_FACTOR[task.severity] ?? 1.0
  let cost = base * sevFactor
  terms.push({ label: `gravedad ${SEVERITY_WORD[task.severity]}`, factor: sevFactor, source: 'severity' })

  if (task.kind === 'rescue') {
    cost *= RESCUE_FACTOR
    terms.push({ label: 'rescate', factor: RESCUE_FACTOR, source: 'rescue' })
  }

  const civs = [...state.civilians.values()].filter((c) => c.poi_id === task.target_poi)
  const poi = task.target_poi ? state.pois.get(task.target_poi) : undefined

  const applies: Record<WeightName, boolean> = {
    life_safety: civs.some((c) => c.state === 'exposed' && c.count > 0),
    immobile_first: civs.some((c) => c.immobile > 0),
    structure_protection:
      poi !== undefined && (poi.kind === 'village' || poi.kind === 'hospital' || poi.kind === 'shelter'),
    containment: task.kind === 'extinguish' && isWindward(state, task),
    response_time: false,
  }

  // Orden de catálogo y no el del objeto: la ecuación tiene que leerse igual siempre.
  for (const name of WEIGHT_NAMES) {
    const value = policy.weights[name]
    if (value === undefined || value <= 0) continue
    if (name === 'response_time') {
      const factor = 1.0 + value
      cost *= factor
      terms.push({ label: `response_time ${fmt(value)}`, factor, source: name })
    } else if (applies[name]) {
      const factor = 1.0 - (1.0 - WEIGHT_DISCOUNT) * Math.min(value, 1.0)
      cost *= factor
      terms.push({ label: `${name} ${fmt(value)}`, factor, source: name })
    } else {
      inapplicable.push({ name, value, why: WHY_NOT[name] })
    }
  }

  return { baseM: base, offroadM: 0, terms, total: cost, inapplicable }
}

const SEVERITY_WORD: Record<TaskSeverity, string> = {
  critical: 'crítica',
  high: 'alta',
  medium: 'media',
  low: 'baja',
}

function fmt(v: number): string {
  return v.toFixed(2).replace('.', ',')
}

// --- columnas ---

function severityRank(s: TaskSeverity): number {
  return SEVERITY_RANK[s] ?? 0
}

function bySeverityThenAge(a: Task, b: Task): number {
  return severityRank(b.severity) - severityRank(a.severity) || a.created_t - b.created_t || (a.id < b.id ? -1 : 1)
}

/** Columnas extra de evacuación: tantas ambulancias como viajes haga falta dar.
 *  Con una sola columna, tres de las cuatro se quedaban en el hospital mientras un
 *  pueblo de 24 vecinos esperaba a la única que salía. */
function evacColumns(state: SimState, units: Unit[], tasks: Task[]): Task[] {
  const transport = units.filter((u) => u.capabilities.includes('transport'))
  const evac = tasks.filter((t) => t.kind === 'evacuate').sort(bySeverityThenAge)
  if (transport.length === 0 || evac.length === 0) return []
  const capacity = Math.max(0, ...transport.map((u) => u.capacity)) || 1
  let room = transport.length - evac.length
  const extra: Task[] = []
  for (const task of evac) {
    if (room <= 0) break
    let pending = 0
    for (const g of state.civilians.values()) {
      if (g.poi_id === task.target_poi && g.state !== 'safe') pending += g.count
    }
    const trips = Math.max(1, Math.ceil(pending / capacity))
    const n = Math.min(trips - 1, room)
    for (let i = 0; i < n; i++) extra.push(task)
    room -= n
  }
  return extra
}

function columns(state: SimState, units: Unit[], tasks: Task[]): Task[] {
  const extUnits = units.filter((u) => u.capabilities.includes('extinguish')).length
  const extTasks = tasks.filter((t) => t.kind === 'extinguish').sort(bySeverityThenAge)
  const cols = [...tasks]
  let i = 0
  while (extTasks.length > 0 && extTasks.length + i < extUnits) {
    const t = extTasks[i % extTasks.length]
    if (t) cols.push(t)
    i++
  }
  cols.push(...evacColumns(state, units, tasks))
  return cols
}

/** Lee `reserve_capability:cap:n` de la Policy. */
export function reservesOf(policy: Policy): Reserved[] {
  const out: Reserved[] = []
  for (const expr of policy.hard_constraints) {
    const parts = expr.split(':')
    if (parts[0] !== 'reserve_capability' || parts.length !== 3) continue
    const cap = parts[1]
    const n = Number(parts[2])
    if (cap && Number.isFinite(n) && n > 0) out.push({ capability: cap, n })
  }
  return out
}

/** Aplica la reserva recortando columnas. Devuelve las que se quedan fuera.
 *
 *  `reserve_capability:cap:n` es una restricción de CARDINALIDAD, no de par: no se
 *  puede expresar poniendo infinitos en celdas sueltas de la matriz, porque no dice
 *  «esta unidad no puede hacer esta tarea» sino «como mucho salen tantas». Y con el
 *  emparejamiento 1:1, el número de unidades que salen es el número de columnas que
 *  piden su capacidad. Así que se aplica aquí.
 *
 *  El orden del recorte importa:
 *    1. primero las columnas DUPLICADAS —las que se añadieron para que no sobrara
 *       nadie—, porque quitarlas guarda medios sin dejar ninguna tarea sin cubrir;
 *    2. y si aún se pasa, las tareas originales menos graves. Esas sí salen en
 *       `unassigned_tasks`, y deben salir: el hueco es la consecuencia visible de
 *       haber guardado reserva, y taparlo sería mentir sobre lo que costó. */
function applyReserves(
  cols: Task[],
  units: Unit[],
  reserves: readonly Reserved[],
): { kept: Task[]; dropped: Task[] } {
  if (reserves.length === 0) return { kept: cols, dropped: [] }
  const kept = [...cols]
  const dropped: Task[] = []

  for (const { capability, n } of reserves) {
    const active = units.filter((u) => u.capabilities.includes(capability)).length
    const allowed = Math.max(0, active - n)
    // Sin medios de sobra no se guarda nada: reservar el único que queda es no cubrir.
    if (active <= 1) continue

    const mine = () => kept.filter((t) => t.required_capability === capability)
    const seen = new Set<string>()
    const duplicates = new Set<number>()
    kept.forEach((t, i) => {
      if (t.required_capability !== capability) return
      if (seen.has(t.id)) duplicates.add(i)
      seen.add(t.id)
    })

    // 1) Las duplicadas, de la última a la primera.
    const dupIdx = [...duplicates].sort((a, b) => b - a)
    for (const i of dupIdx) {
      if (mine().length <= allowed) break
      const [gone] = kept.splice(i, 1)
      if (gone) dropped.push(gone)
    }
    // 2) Y si no basta, las originales menos graves.
    while (mine().length > allowed) {
      const worst = mine().sort(bySeverityThenAge).pop()
      if (!worst) break
      const i = kept.lastIndexOf(worst)
      if (i < 0) break
      const [gone] = kept.splice(i, 1)
      if (gone) dropped.push(gone)
    }
  }
  return { kept, dropped }
}

// --- la matriz ---

function taskWaypoint(state: SimState, task: Task, graph: RoadGraph): string | null {
  if (task.kind === 'rescue' && task.target_x != null && task.target_z != null) {
    return graph.nearestWaypoint(task.target_x, task.target_z)
  }
  if (task.target_poi != null) return state.pois.get(task.target_poi)?.waypoint_id ?? null
  if (task.target_cell != null) {
    if (!state.cells.has(task.target_cell)) return null
    const center = cellCenterOf(task.target_cell, graph)
    if (!center) return null
    // A tiro si se puede, y si no, donde el fuego va a llegar antes.
    return graph.attackWaypoint(center[0], center[1], state.wind)
  }
  return null
}

function cellCenterOf(cellId: string, graph: RoadGraph): [number, number] | null {
  const m = /^cell_(-?\d+)_(-?\d+)$/.exec(cellId)
  if (!m || m[1] === undefined || m[2] === undefined) return null
  const size = graph.geo.cellSize
  return [
    graph.geo.origin[0] + (Number(m[1]) + 0.5) * size,
    graph.geo.origin[1] + (Number(m[2]) + 0.5) * size,
  ]
}

/** Metros a pie del waypoint de ataque al centro de la celda objetivo. Un frente a
 *  20 m de la carretera cuesta poco más que su ruta; uno a 100 m, mucho más. */
function offroadM(task: Task, wpId: string, graph: RoadGraph): number {
  if (!task.target_cell) return 0
  const center = cellCenterOf(task.target_cell, graph)
  const wp = graph.at(wpId)
  if (!center || !wp) return 0
  return Math.hypot(wp[0] - center[0], wp[1] - center[1])
}

/** Sin ordenar: el orden es el de inserción, igual que `state.units.values()` en
 *  Python. Es determinista (el escenario y `syncTasks` lo son) y además hace que la
 *  matriz salga fila a fila igual que la del core, que es lo que permite compararlas. */
export function activeUnits(state: SimState): Unit[] {
  return [...state.units.values()].filter((u) => u.status !== 'unavailable')
}

export function openTasks(state: SimState): Task[] {
  return [...state.tasks.values()].filter((t) => !t.done)
}

export interface CostMatrix {
  matrix: number[][]
  units: Unit[]
  tasks: Task[]
  routes: Array<Array<string[]>>
  traces: Array<Array<CostTrace | null>>
  reasons: Array<Array<Infeasibility['reason'] | null>>
  /** Tareas que se quedaron sin columna porque la Policy guarda medios. */
  reservedOut: Task[]
  reserves: Reserved[]
}

export function costMatrix(
  state: SimState,
  policy: Policy,
  graph: RoadGraph,
  vetoes: ReadonlySet<string> = new Set(),
): CostMatrix {
  const units = activeUnits(state)
  const reserves = reservesOf(policy)
  const { kept: tasks, dropped: reservedOut } = applyReserves(
    columns(state, units, openTasks(state)),
    units,
    reserves,
  )

  const targets = tasks.map((t) => taskWaypoint(state, t, graph))
  const offroad = tasks.map((t, j) => {
    const wp = targets[j]
    return wp ? offroadM(t, wp, graph) : 0
  })

  const matrix: number[][] = []
  const routes: Array<Array<string[]>> = []
  const traces: Array<Array<CostTrace | null>> = []
  const reasons: Array<Array<Infeasibility['reason'] | null>> = []

  for (const unit of units) {
    const row: number[] = []
    const rrow: Array<string[]> = []
    const trow: Array<CostTrace | null> = []
    const xrow: Array<Infeasibility['reason'] | null> = []
    const from = graph.nearestWaypoint(unit.x, unit.z)

    tasks.forEach((task, j) => {
      const target = targets[j]
      let route: string[] = []
      let cost = INFEASIBLE
      let trace: CostTrace | null = null
      let reason: Infeasibility['reason'] | null = null

      if (vetoes.has(`${unit.id}:${task.id}`)) {
        reason = 'capability'
      } else if (!unit.capabilities.includes(task.required_capability)) {
        reason = 'capability'
      } else if (!target || !from) {
        reason = 'no_target'
      } else {
        const path = graph.shortestPath(from, target)
        if (!path) {
          reason = 'no_route'
        } else {
          route = path
          const base = graph.routeLengthM(path) + (offroad[j] ?? 0)
          trace = weightedCost(base, state, task, policy)
          trace.offroadM = offroad[j] ?? 0
          trace.baseM = graph.routeLengthM(path)
          cost = trace.total
        }
      }

      row.push(cost)
      rrow.push(route)
      trow.push(trace)
      xrow.push(reason)
    })

    matrix.push(row)
    routes.push(rrow)
    traces.push(trow)
    reasons.push(xrow)
  }

  return { matrix, units, tasks, routes, traces, reasons, reservedOut, reserves }
}

// --- restricciones duras ---

const KNOWN_CONSTRAINTS: Record<string, number> = {
  no_unit_into_burning_cell: 0,
  hospital_min_coverage: 1,
  no_civilian_route_through: 1,
  reserve_capability: 2,
}

function routeCrossesBurning(
  route: readonly string[],
  state: SimState,
  graph: RoadGraph,
  skipLast: boolean,
): boolean {
  // El primer waypoint no cuenta: es donde la unidad YA está, y si el fuego le llega,
  // salir de ahí es justo lo que hay que poder hacer. Con `skipLast` tampoco el
  // destino: una tarea de extinción apunta por definición a la celda que arde.
  const body = skipLast ? route.slice(1, -1) : route.slice(1)
  for (const wid of body) {
    const wp = graph.at(wid)
    if (!wp) continue
    const cell = state.cells.get(graph.cellOf(wp[0], wp[1]))
    if (cell && cell.state === 'burning') return true
  }
  return false
}

/** Pone INFEASIBLE donde toca, in situ. Devuelve las restricciones que no reconoce:
 *  una restricción desconocida NO se ignora en silencio, es el bug más caro. */
export function applyHardConstraints(
  cm: CostMatrix,
  state: SimState,
  policy: Policy,
  graph: RoadGraph,
): Violation[] {
  const violations: Violation[] = []

  for (const expr of policy.hard_constraints) {
    const parts = expr.split(':')
    const name = parts[0] ?? ''
    const args = parts.slice(1)
    const arity = KNOWN_CONSTRAINTS[name]
    if (arity === undefined || args.length !== arity) {
      violations.push({
        verifier: 'unknown_constraint',
        severity: 'soft',
        message: `restricción desconocida o aridad incorrecta: '${expr}'`,
        involved: [expr],
      })
      continue
    }

    if (name === 'no_unit_into_burning_cell') {
      cm.units.forEach((_u, i) => {
        cm.tasks.forEach((task, j) => {
          const route = cm.routes[i]?.[j] ?? []
          if (route.length > 0 && routeCrossesBurning(route, state, graph, task.kind === 'extinguish')) {
            const row = cm.matrix[i]
            const xrow = cm.reasons[i]
            if (row) row[j] = INFEASIBLE
            if (xrow) xrow[j] = 'burning_cell'
          }
        })
      })
    } else if (name === 'no_civilian_route_through') {
      const wpId = args[0]
      cm.units.forEach((_u, i) => {
        cm.tasks.forEach((task, j) => {
          const route = cm.routes[i]?.[j] ?? []
          if (task.kind === 'evacuate' && wpId !== undefined && route.slice(1).includes(wpId)) {
            const row = cm.matrix[i]
            const xrow = cm.reasons[i]
            if (row) row[j] = INFEASIBLE
            if (xrow) xrow[j] = 'civilian_route'
          }
        })
      })
    }
    // `hospital_min_coverage` y `reserve_capability` son de cardinalidad global:
    // las comprueba `verifiers.ts` sobre el plan ya construido. Aquí se reconocen
    // para no marcarlas como desconocidas.
  }

  return violations
}

// --- suposiciones ---

function assumedFacts(state: SimState): Assumption[] {
  const seen = new Set<string>()
  const out: Assumption[] = []
  for (let i = state.facts.length - 1; i >= 0; i--) {
    const f = state.facts[i]
    if (!f || f.kind !== 'assumed_default' || seen.has(f.key)) continue
    seen.add(f.key)
    out.push({ key: f.key, expected: f.value, weight: ASSUMED_WEIGHT })
  }
  return out.reverse()
}

/** Lo que este plan da por cierto. Es lo que vigila `divergence.ts`, así que si aquí
 *  falta una suposición, el replan no salta. */
export function buildContext(
  state: SimState,
  assignments: readonly Assignment[],
  graph: RoadGraph,
): PlanContext {
  const assumptions: Assumption[] = []
  const seen = new Set<string>()
  for (const a of assignments) {
    for (const eid of graph.routeEdges(a.route)) {
      if (seen.has(eid)) continue
      seen.add(eid)
      assumptions.push({ key: `road:${eid.replace(/^road:/, '')}:open`, expected: true, weight: 1.0 })
    }
  }
  assumptions.push({ key: 'wind:bearing_deg', expected: state.wind.bearing_deg, weight: 0.5 })
  assumptions.push(...assumedFacts(state))
  return { assumptions, world_seq: state.seq }
}

// --- resolver ---

export function solve(
  state: SimState,
  policy: Policy,
  graph: RoadGraph,
  vetoes: ReadonlySet<string> = new Set(),
): SolveResult {
  const cm = costMatrix(state, policy, graph, vetoes)
  const violations = applyHardConstraints(cm, state, policy, graph)

  const pairs = match(cm.matrix)
  const assignments: Assignment[] = []
  const traces = new Map<string, CostTrace>()
  const assignedTasks = new Set<string>()

  for (const [i, j] of pairs) {
    const unit = cm.units[i]
    const task = cm.tasks[j]
    const route = cm.routes[i]?.[j] ?? []
    const trace = cm.traces[i]?.[j] ?? null
    const cost = cm.matrix[i]?.[j] ?? 0
    if (!unit || !task) continue
    const lengthM = graph.routeLengthM(route)
    const assignment: Assignment = {
      unit_id: unit.id,
      task_id: task.id,
      route,
      eta_s: Number.isFinite(lengthM) ? lengthM / UNIT_SPEED_MPS : 0,
      cost,
    }
    assignments.push(assignment)
    assignedTasks.add(task.id)
    if (trace) traces.set(`${unit.id}:${task.id}`, trace)
  }

  const unassigned: string[] = []
  const infeasible: Infeasibility[] = []
  // Lo recortado por reserva se dice como tal: «nadie va porque se guarda un medio»
  // no es lo mismo que «no había ruta», y en un panel que explica decisiones la
  // diferencia es justo lo que hay que leer.
  for (const task of cm.reservedOut) {
    if (unassigned.includes(task.id) || cm.tasks.some((t) => t.id === task.id)) continue
    unassigned.push(task.id)
    infeasible.push({ unitId: '', taskId: task.id, reason: 'reserved' })
  }
  for (const task of cm.tasks) {
    if (assignedTasks.has(task.id) || unassigned.includes(task.id)) continue
    unassigned.push(task.id)
    // Por qué no la cogió nadie: el motivo más común entre las unidades capaces.
    const j = cm.tasks.indexOf(task)
    for (let i = 0; i < cm.units.length; i++) {
      const reason = cm.reasons[i]?.[j]
      const unit = cm.units[i]
      if (reason && unit) infeasible.push({ unitId: unit.id, taskId: task.id, reason })
    }
  }

  const plan: Plan = {
    id: `plan_${state.run_id}_${state.seq}`,
    run_id: state.run_id,
    created_t: state.t_sim,
    policy,
    assignments,
    unassigned_tasks: unassigned,
    context: buildContext(state, assignments, graph),
  }

  // Los medios que están libres POR LA RESERVA: como mucho `n` por capacidad. Sin el
  // tope, cuando no queda ninguna tarea abierta se listaba la flota entera «en
  // reserva», que es al revés: entonces no están guardados, es que no hay nada que
  // hacer.
  const assignedUnits = new Set(assignments.map((a) => a.unit_id))
  const reservedUnits: string[] = []
  for (const { capability, n } of cm.reserves) {
    const free = cm.units.filter(
      (u) => !assignedUnits.has(u.id) && u.capabilities.includes(capability) && !reservedUnits.includes(u.id),
    )
    for (const u of free.slice(0, n)) reservedUnits.push(u.id)
  }

  return { plan, violations, traces, infeasible, reserves: cm.reserves, reservedUnits }
}
