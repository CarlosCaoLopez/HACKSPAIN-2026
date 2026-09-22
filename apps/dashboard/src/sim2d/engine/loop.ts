// El bucle. Puerto reducido de `core/loop.py` + el tick de `sim/runner.py`.
//
// Aquí viven las dos decisiones que la página tiene que ENSEÑAR, no solo ejecutar:
//   · cuándo se tira el plan (`shouldReplan`, con la Policy nueva) y cuándo solo se
//     vuelve a repartir (`resolve`, misma Policy). Son cosas distintas y en pantalla
//     se ven distintas: banda roja contra banda gris.
//   · el orden del despacho: pedir los medios, esperar a que cuelguen, y solo
//     entonces dictarle la orden al pueblo.
import type { Fact, Plan, Scenario, Violation, Wind } from '../../types'

import { Dispatch, type GotoAction, type SimCall } from './dispatch'
import { divergence, shouldReplan, type DivergenceResult } from './divergence'
import { Wildfire, type CellChange, type HazardConfig } from './fire'
import { RoadGraph } from './graph'
import { AT_WAYPOINT_M, Movement } from './movement'
import { buildPolicy, type PolicyResult } from './policy'
import { mulberry32 } from './rng'
import { solve, type CostTrace, type Infeasibility, type Reserved } from './solver'
import {
  applyCellChange,
  assertFact,
  cutEdges,
  cutRoad,
  initialState,
  setCivilians,
  setTask,
  setUnit,
  type SimState,
} from './state'
import { arrivalCloses, syncTasks } from './tasks'
import { verify } from './verifiers'

export const TICK_S = 1.0
/** Quién vuelve a su base cuando se queda libre. Los camiones NO: se quedan en el
 *  frente sofocando, que es su trabajo. Mismo criterio que `loop.HOME_CAPABILITIES`. */
const HOME_CAPABILITIES: ReadonlySet<string> = new Set(['transport', 'medical'])
/** Margen de metros para no re-resolver por cada celda que prende. */
export const RESOLVE_GAP_S = 5.0
/** Cada cuánto se mira si el frente crece, para el peso `containment`. */
const GROWTH_WINDOW_S = 30.0

export interface LogEntry {
  tSim: number
  kind: 'replan' | 'resolve' | 'fire' | 'call' | 'action' | 'inject' | 'fact'
  text: string
}

export interface Frame {
  seq: number
  tSim: number
  state: SimState
  plan: Plan | null
  policy: PolicyResult | null
  divergence: DivergenceResult | null
  violations: Violation[]
  traces: Map<string, CostTrace>
  infeasible: Infeasibility[]
  /** Lo que la Policy guarda, y qué unidades quedan libres por ello. */
  reserves: Reserved[]
  reservedUnits: string[]
  calls: SimCall[]
  actions: GotoAction[]
  heldUnits: string[]
  /** Rutas ordenadas pero retenidas: se pintan en gris discontinuo. */
  pendingRoutes: Map<string, string[]>
  log: LogEntry[]
  /** El último acontecimiento de planificación, para la banda superior. */
  banner: { kind: 'replan' | 'resolve'; reason: string; tSim: number } | null
  ignitions: string[]
  running: boolean
}

function burnableBox(scenario: Scenario): [number, number, number, number] {
  const xs = [...scenario.pois.map((p) => p.x), ...scenario.waypoints.map((w) => w.x)]
  const zs = [...scenario.pois.map((p) => p.z), ...scenario.waypoints.map((w) => w.z)]
  const m = 30
  return [Math.min(...xs) - m, Math.min(...zs) - m, Math.max(...xs) + m, Math.max(...zs) + m]
}

export class SimEngine {
  readonly scenario: Scenario
  readonly graph: RoadGraph
  state: SimState
  plan: Plan | null = null
  policy: PolicyResult | null = null
  lastDivergence: DivergenceResult | null = null
  violations: Violation[] = []
  traces = new Map<string, CostTrace>()
  infeasible: Infeasibility[] = []
  reserves: Reserved[] = []
  reservedUnits: string[] = []
  banner: Frame['banner'] = null
  readonly log: LogEntry[] = []
  readonly ignitions: string[] = []
  running = false

  private fire: Wildfire
  private movement: Movement
  private dispatch: Dispatch
  private frameSeq = 0
  /** Hasta dónde se han consumido las órdenes de `dispatch.actions`. */
  private actionCursor = 0
  private lastResolveT = -Infinity
  private burningHistory: Array<[number, number]> = []
  private knownCriticalFacts = new Set<string>()
  private firedInjects = new Set<number>()

  /** El waypoint del que salió cada unidad. Es a donde vuelve cuando se queda libre. */
  private readonly home = new Map<string, string>()

  constructor(scenario: Scenario) {
    this.scenario = scenario
    this.graph = RoadGraph.fromScenario(scenario)
    for (const unit of scenario.units) {
      const wp = this.graph.nearestWaypoint(unit.x, unit.z)
      if (wp) this.home.set(unit.id, wp)
    }
    this.state = initialState(scenario, `sim_${scenario.id}`)
    this.fire = this.freshFire()
    this.movement = new Movement(this.graph)
    this.dispatch = new Dispatch()
  }

  private freshFire(): Wildfire {
    const h = this.scenario.hazard
    const cfg: HazardConfig = {
      kind: h.kind,
      originCell: h.origin_cell,
      cellSize: h.cell_size,
      baseSpread: h.base_spread,
      suppressReachM: h.suppress_reach_m,
      suppressRate: h.suppress_rate,
      burnable: burnableBox(this.scenario),
    }
    return new Wildfire(cfg, this.scenario.hazard.wind, mulberry32(this.scenario.seed))
  }

  // --- mandos ---

  get answersPhone(): boolean {
    return this.dispatch.answersPhone
  }
  set answersPhone(v: boolean) {
    this.dispatch.answersPhone = v
  }

  ignite(cellId: string): void {
    const changes = this.fire.ignite(cellId)
    if (changes.length === 0) return
    if (!this.ignitions.includes(cellId)) this.ignitions.push(cellId)
    this.absorb(changes)
    this.note('fire', `foco encendido en ${cellId}`)
  }

  douse(cellId: string): void {
    this.absorb(this.fire.douse(cellId))
    const i = this.ignitions.indexOf(cellId)
    if (i >= 0) this.ignitions.splice(i, 1)
    this.note('fire', `foco apagado en ${cellId}`)
  }

  setWind(wind: Wind): void {
    this.state.wind = { ...wind }
    this.fire.setWind(wind)
    this.note('inject', `viento a ${Math.round(wind.bearing_deg)}° (empuja al ${Math.round((wind.bearing_deg + 180) % 360)}°), ${wind.speed.toFixed(1)} celdas/min`)
  }

  cutRoadInject(edgeId: string, cause: string): void {
    cutRoad(this.state, edgeId, true, cause)
    this.note('inject', `${edgeId} cortada: ${cause}`)
    this.rerouteInFlight(edgeId)
  }

  /** Lo que hace que cortar una carretera signifique algo: las unidades que ya iban
   *  por ahí tienen que recalcular. Sin esto, el camión sigue tan tranquilo por una
   *  pista cortada mientras el mapa la pinta con su «✕ cortada» encima.
   *
   *  Es el puerto de `sim/runner.py:_cut`, con una diferencia deliberada. El backend
   *  rutea desde el waypoint más cercano, así que a quien ya va pasado del punto
   *  medio lo deja salir hacia delante — terminando de cruzar el tramo cortado. Aquí
   *  no: si la unidad está DENTRO del tramo que se acaba de cortar, se rutea desde el
   *  extremo por el que entró. Sale por donde vino y rodea. Cuesta el desvío, y a
   *  cambio no se ve nunca a nadie circular sobre la marca del corte. */
  private rerouteInFlight(edgeId: string): void {
    const edge = this.state.roads.get(edgeId)
    if (!edge) return
    const uses = (route: readonly string[]): boolean => {
      for (let i = 0; i + 1 < route.length; i++) {
        const p = route[i]
        const q = route[i + 1]
        if ((p === edge.a && q === edge.b) || (p === edge.b && q === edge.a)) return true
      }
      return false
    }
    const live = this.graph.withCuts(cutEdges(this.state))
    for (const unit of [...this.state.units.values()].sort((a, b) => (a.id < b.id ? -1 : 1))) {
      if (!this.movement.isMoving(unit.id)) continue
      const remaining = this.movement.remainingRoute(unit.id) ?? []
      // Solo se toca a quien iba a pasar por ahí. Recalcular a todo el mundo, como
      // hace el backend, suena más robusto y no lo es: a una unidad que ya casi ha
      // llegado, `shortestPath` le devuelve su propio destino —un solo waypoint— y
      // eso se lee como «sin salida». Medido: paraba los dos camiones en seco a
      // 3:30 con un `route_cut` inventado, y ni siquiera iban por la pista cortada.
      if (!uses(remaining)) continue

      const dest = this.movement.destinationOf(unit.id)
      const taskId = this.movement.taskOf(unit.id)
      if (!dest || taskId === null) continue

      // ¿Está metida justo en el tramo cortado? Entonces sale por donde entró.
      const leg = this.movement.currentLeg(unit.id)
      const trapped =
        leg !== null &&
        ((leg[0] === edge.a && leg[1] === edge.b) || (leg[0] === edge.b && leg[1] === edge.a))
      const from = trapped ? leg[0] : live.nearestWaypoint(unit.x, unit.z)

      const route = from ? live.shortestPath(from, dest) : null
      if (!route) {
        // Sin salida de verdad: se para y se dice. Nunca se la deja cruzando ni se
        // la teletransporta. Es el `route_cut:<edge>` del backend.
        this.movement.stop(unit.id)
        setUnit(this.state, unit.id, { status: 'idle' })
        this.note('action', `${unit.id} sin ruta viva: route_cut:${edgeId}`)
        continue
      }
      if (route.length < 2) continue // ya está en su destino: no hay nada que rehacer
      if (remaining.length === route.length && remaining.every((wp, i) => wp === route[i])) continue
      this.dispatch.reroute(this.state, unit.id, taskId, route)
      this.note('action', `${unit.id} desvía por ${edgeId} cortada`)
    }
    // Arrancar los desvíos YA, sin esperar al tick siguiente: el corte puede venir
    // del botón de la barra, que no pasa por `tick`.
    this.syncMotions()
  }

  /** La avería entra como HECHO crítico observado, que es la vía real: `belief`
   *  pone la unidad `unavailable` y `shouldReplan` ve un hecho crítico. Un
   *  `world.unit.status` a secas no rompe ninguna suposición y no replanifica. */
  failUnit(unitId: string, reason: string): void {
    this.assert({
      key: `unit:${unitId}:available`,
      value: false,
      confidence: 1,
      source: 'human',
      severity: 'critical',
      t_sim: this.state.t_sim,
      kind: 'observed',
      call_id: null,
    })
    this.movement.stop(unitId)
    this.note('inject', `${unitId} fuera de servicio: ${reason}`)
  }

  assert(fact: Fact): void {
    assertFact(this.state, fact)
    this.note('fact', `${fact.key} = ${String(fact.value)} (${fact.kind})`)
  }

  reset(): void {
    this.state = initialState(this.scenario, `sim_${this.scenario.id}`)
    this.fire = this.freshFire()
    this.movement = new Movement(this.graph)
    this.dispatch = new Dispatch()
    this.plan = null
    this.policy = null
    this.lastDivergence = null
    this.violations = []
    this.traces = new Map()
    this.infeasible = []
    this.reserves = []
    this.reservedUnits = []
    this.banner = null
    this.log.length = 0
    this.ignitions.length = 0
    this.actionCursor = 0
    this.lastResolveT = -Infinity
    this.burningHistory = []
    this.knownCriticalFacts = new Set()
    this.firedInjects = new Set()
  }

  // --- el tick ---

  tick(dt: number = TICK_S): void {
    const s = this.state
    s.t_sim += dt
    s.seq++

    this.runInjects()
    this.absorb(this.fire.tick(dt))

    for (const arrival of this.movement.step(s, dt)) {
      for (const task of arrivalCloses(s, arrival.waypointId)) {
        setTask(s, task)
        // Al llegar, los vecinos salen: es lo que cierra la evacuación.
        if (task.kind === 'evacuate' || task.kind === 'rescue') this.rescueAt(task.target_poi ?? null)
      }
    }

    // Quién está sofocando: los de extinción que ya han llegado. Y se les pone
    // `working`, porque `resourcesLine` lo dice en voz alta por teléfono — un camión
    // encima del frente no es «disponible».
    const working: Array<[number, number]> = []
    const suppressing: string[] = []
    for (const unit of s.units.values()) {
      if (unit.status === 'unavailable' || !unit.capabilities.includes('extinguish')) continue
      if (this.movement.isMoving(unit.id)) continue
      working.push([unit.x, unit.z])
      suppressing.push(unit.id)
    }
    const put = this.fire.suppress(working, dt)
    this.absorb(put)
    for (const unitId of suppressing) {
      const near = this.fire.activeNear(s.units.get(unitId)?.x ?? 0, s.units.get(unitId)?.z ?? 0)
      const unit = s.units.get(unitId)
      if (!unit) continue
      const wanted = near ? 'working' : 'idle'
      if (unit.status !== wanted) setUnit(s, unitId, { status: wanted })
    }

    this.dispatch.dropUnavailableHolds(s, s.t_sim)
    this.dispatch.tick(s, s.t_sim)
    this.syncMotions()

    const before = new Map([...s.tasks].map(([id, t]) => [id, t.severity + String(t.done)]))
    const { changed } = syncTasks(s, this.graph)
    for (const t of changed) setTask(s, t)

    // --- ¿replanificar, re-resolver o nada? ---
    const criticals = this.newCriticalFacts()
    let hard: Violation[] = []
    if (this.plan) {
      this.lastDivergence = divergence(s, this.plan.context)
      hard = verify(s, this.plan, this.graph).filter((v) => v.severity === 'hard')
    }
    const value = this.lastDivergence?.value ?? 0
    const decision = this.plan ? shouldReplan(value, hard.length, criticals) : { flag: true, reason: 'plan inicial' }

    if (decision.flag && this.hasWork()) {
      this.replan(decision.reason)
    } else if (this.plan && this.resolveDue(changed, before)) {
      this.resolve()
    }

    this.frameSeq++
  }

  private hasWork(): boolean {
    for (const t of this.state.tasks.values()) if (!t.done) return true
    return false
  }

  /** Solo se vuelve a repartir cuando el cambio de tareas lo merece: con el fuego
   *  creciendo celda a celda, si no, serían ciento cuarenta planes por run. */
  private resolveDue(changed: readonly { id: string; kind: string; severity: string; done: boolean }[], before: Map<string, string>): boolean {
    if (changed.length === 0) return false
    for (const t of changed) {
      if (t.done) return true
      if (t.kind === 'evacuate' || t.kind === 'rescue') return true
      if (t.severity === 'critical' && before.get(t.id) !== 'criticalfalse') return true
    }
    return this.state.t_sim - this.lastResolveT >= RESOLVE_GAP_S
  }

  private replan(reason: string): void {
    const assigned = new Set((this.plan?.assignments ?? []).map((a) => a.task_id))
    this.policy = buildPolicy(this.state, this.graph, {
      burningBefore: this.burningBefore(),
      assignedTasks: assigned,
    })
    this.runSolver()
    this.banner = { kind: 'replan', reason, tSim: this.state.t_sim }
    this.note('replan', `REPLAN — ${reason}`)
  }

  private resolve(): void {
    if (!this.policy) return
    this.runSolver()
    this.banner = { kind: 'resolve', reason: 'han cambiado las tareas', tSim: this.state.t_sim }
    this.note('resolve', 'nuevo reparto con la misma policy')
  }

  private runSolver(): void {
    if (!this.policy) return
    const cut = cutEdges(this.state)
    const live = this.graph.withCuts(cut)
    const res = solve(this.state, this.policy.policy, live)
    this.plan = res.plan
    this.traces = res.traces
    this.infeasible = res.infeasible
    this.reserves = res.reserves
    this.reservedUnits = res.reservedUnits
    this.violations = [...res.violations, ...verify(this.state, res.plan, this.graph)]
    this.lastResolveT = this.state.t_sim
    this.lastDivergence = divergence(this.state, res.plan.context)
    this.dispatch.afterPlan(this.state, res.plan, live, this.scenario.road_aliases, (u) => this.movement.remainingRoute(u))
    this.returnHome(res.plan, live)
    this.syncMotions()
  }

  /** Cada `goto` emitido pone en marcha a su unidad, **una sola vez**.
   *
   *  El cursor no es una optimización, es la corrección. Antes esto recorría el
   *  histórico entero en cada tick, y en cuanto una unidad terminaba su ruta
   *  (`destinationOf` → `null`) cualquier orden vieja volvía a cumplir la condición y
   *  se relanzaba con su ruta original — cuyo primer waypoint es donde la unidad
   *  estaba CUANDO SE DIO aquella orden. El camión llegaba al frente y reaparecía en
   *  el parque; la ambulancia llegaba a Pueblo A y reaparecía en el hospital, y el
   *  viaje se repetía en bucle cada treinta y cuatro segundos.
   *
   *  `dispatch.actions` solo crece, así que basta con recordar por dónde se iba.
   *  Una unidad que llega a su destino se queda ahí hasta que el solver le dé otra
   *  orden: el camión, sofocando; la ambulancia, hasta que la reasignen o la manden
   *  a casa (`returnHome`). */
  private syncMotions(): void {
    const pending = this.dispatch.actions.slice(this.actionCursor)
    this.actionCursor = this.dispatch.actions.length
    for (const action of pending) {
      if (this.dispatch.isHeld(action.unitId)) continue
      const unit = this.state.units.get(action.unitId)
      if (!unit || unit.status === 'unavailable') continue
      this.movement.start(action.unitId, action.route, action.taskId, [unit.x, unit.z])
    }
  }

  /** Una unidad de transporte libre, parada y lejos de casa, vuelve **por la
   *  carretera**. Puerto de `core/loop.py:_return_home`.
   *
   *  Sin esto, la ambulancia que acaba de evacuar Pueblo A se quedaba allí plantada:
   *  cuando el frente giraba y hacía falta en Pueblo B salía «desde» Pueblo A con un
   *  ETA que no se correspondía con nada. Y si no hay ruta viva de vuelta, no se
   *  ordena nada y se anota — nunca un salto. */
  private returnHome(plan: Plan, live: RoadGraph): void {
    const assigned = new Set(plan.assignments.map((a) => a.unit_id))
    for (const unit of [...this.state.units.values()].sort((a, b) => (a.id < b.id ? -1 : 1))) {
      if (assigned.has(unit.id) || unit.status !== 'idle') continue
      if (!unit.capabilities.some((c) => HOME_CAPABILITIES.has(c))) continue
      const home = this.home.get(unit.id)
      if (!home) continue
      const at = live.at(home)
      if (at && Math.hypot(unit.x - at[0], unit.z - at[1]) <= AT_WAYPOINT_M) continue
      const here = live.nearestWaypoint(unit.x, unit.z)
      const route = here ? live.shortestPath(here, home) : null
      if (!route || route.length < 2) {
        this.note('action', `${unit.id} libre sin ruta viva de vuelta a ${home}`)
        continue
      }
      if (this.dispatch.returnHome(this.state, unit.id, home, route)) {
        this.note('action', `${unit.id} vuelve a ${home} por la carretera`)
      }
    }
  }

  /** Cuando llega el transporte, los vecinos salen y el grupo queda a salvo. */
  private rescueAt(poiId: string | null): void {
    if (!poiId) return
    for (const g of this.state.civilians.values()) {
      if (g.poi_id === poiId && g.state !== 'safe') {
        setCivilians(this.state, g.id, { state: 'safe' })
        this.note('action', `rescue: ${g.count} vecinos de ${poiId} a salvo`)
      }
    }
  }

  private newCriticalFacts(): string[] {
    const out: string[] = []
    for (const f of this.state.facts) {
      const key = `${f.key}@${f.t_sim}`
      if (f.severity !== 'critical' || this.knownCriticalFacts.has(key)) continue
      this.knownCriticalFacts.add(key)
      out.push(f.key)
    }
    return out
  }

  private burningBefore(): number {
    const cutoff = this.state.t_sim - GROWTH_WINDOW_S
    for (let i = this.burningHistory.length - 1; i >= 0; i--) {
      const h = this.burningHistory[i]
      if (h && h[0] <= cutoff) return h[1]
    }
    return 0
  }

  private absorb(changes: readonly CellChange[]): void {
    for (const ch of changes) applyCellChange(this.state, ch)
    if (changes.length > 0) {
      let burning = 0
      for (const c of this.state.cells.values()) if (c.state === 'burning') burning++
      this.burningHistory.push([this.state.t_sim, burning])
      if (this.burningHistory.length > 400) this.burningHistory.shift()
    }
  }

  private runInjects(): void {
    this.scenario.injects.forEach((spec, i) => {
      if (this.firedInjects.has(i) || this.state.t_sim < spec.at) return
      this.firedInjects.add(i)
      this.applyInject(spec.type, spec.payload)
    })
  }

  applyInject(type: string, payload: Record<string, unknown>): void {
    if (type === 'wind_shift') {
      this.setWind({
        bearing_deg: Number(payload['bearing'] ?? this.state.wind.bearing_deg),
        speed: Number(payload['speed'] ?? this.state.wind.speed),
      })
    } else if (type === 'road_cut') {
      this.cutRoadInject(String(payload['edge'] ?? ''), String(payload['cause'] ?? 'sin causa'))
    } else if (type === 'unit_failure') {
      this.failUnit(String(payload['unit'] ?? ''), String(payload['reason'] ?? 'avería'))
    }
  }

  private note(kind: LogEntry['kind'], text: string): void {
    this.log.push({ tSim: this.state.t_sim, kind, text })
    if (this.log.length > 300) this.log.shift()
  }

  // --- lo que ve React ---

  frame(): Frame {
    const pending = new Map<string, string[]>()
    for (const unitId of this.dispatch.heldUnitIds) {
      const route = this.dispatch.routeOf(unitId)
      if (route) pending.set(unitId, route)
    }
    return {
      seq: this.frameSeq,
      tSim: this.state.t_sim,
      state: this.state,
      plan: this.plan,
      policy: this.policy,
      divergence: this.lastDivergence,
      violations: this.violations,
      traces: this.traces,
      infeasible: this.infeasible,
      reserves: this.reserves,
      reservedUnits: this.reservedUnits,
      calls: this.dispatch.calls,
      actions: this.dispatch.actions,
      heldUnits: this.dispatch.heldUnitIds,
      pendingRoutes: pending,
      log: this.log,
      banner: this.banner,
      // Todos los que se han encendido a mano, ardan o no: es el contador de la barra.
      // Quién dibuja el aro, y sobre qué celdas, lo decide el mapa.
      ignitions: [...this.ignitions],
      running: this.running,
    }
  }

  headingOf(unitId: string): number {
    return this.movement.headingOf(unitId)
  }
}

export { setUnit }
