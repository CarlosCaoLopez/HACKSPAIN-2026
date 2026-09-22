// Las llamadas y la retención de unidades. Puerto de la máquina de `core/loop.py`.
//
// Es el beat central de la demo y el que no se puede recortar: **primero se piden los
// medios, las unidades NO se mueven hasta que su dotación cuelga, y solo entonces se
// dicta la orden al pueblo**, con los medios que de verdad han confirmado. El agente
// no le promete ayuda a un pueblo hasta que sabe que la ayuda existe.
//
// Y cuando nadie contesta, la unidad sale igual a los 45 s y se dice en pantalla
// («salió SIN confirmar»). Degradar y anotar, nunca congelarse ni un `except: pass`.
import type { Assignment, CallOutcome, Plan, Unit } from '../../types'

import {
  committedResourcesLine,
  coverageLine,
  deadlineMin,
  fireLine,
  minutes,
  peopleLine,
  requestedUnitsLine,
  resourcesLine,
  roadsLine,
  routeName,
  unitEtaLine,
  unitName,
} from './calls'
import type { RoadGraph } from './graph'
import type { SimState } from './state'

/** Los dos roles que RETIENEN unidades. Un `ambulance_queued` no retiene: no hay
 *  ninguna libre a la que retener. */
export const DISPATCH_ROLES: ReadonlySet<string> = new Set(['fire_crew', 'ambulance'])
/** La «tarea» con la que se manda una unidad libre de vuelta a su base. No es una
 *  tarea del plan: no la produce el solver ni aparece en `unassigned_tasks`. */
export const RETURN_TASK = 'return_to_base'
export const DISPATCH_RING_S = 45.0
export const DISPATCH_TALK_S = 150.0
/** Lo que tardan en descolgar. Local de la simulación: en el sistema real lo dice
 *  HappyRobot. */
export const PICKUP_S = 6.0
/** Una línea del guion cada cuánto. */
export const LINE_S = 4.0

export type CallRole = 'fire_crew' | 'ambulance' | 'ambulance_queued' | 'evacuation' | 'neighbor_alert'
export type CallPhase = 'ringing' | 'talking' | 'ended'

export interface CallLine {
  speaker: 'agente' | 'interlocutor'
  text: string
}

export interface SimCall {
  id: string
  role: CallRole
  callee: string
  poiId: string | null
  taskId: string | null
  phase: CallPhase
  startedT: number
  answeredT: number | null
  endedT: number | null
  outcome: CallOutcome | null
  /** Las líneas ya dichas. El guion completo está en `script`. */
  said: CallLine[]
  script: CallLine[]
  /** Las unidades que esta llamada tiene paradas. */
  heldUnits: string[]
  /** Por qué existe esta llamada, en una frase: la decisión que la dispara. */
  why: string
  /** Los datos del centro que el operador usa. Es lo que se enseña bajo la tarjeta. */
  facts: Array<{ label: string; value: string }>
}

export interface GotoAction {
  id: string
  unitId: string
  taskId: string
  route: string[]
  tSim: number
  /** `null` = no pasó por una llamada de despacho (p. ej. la vuelta a base). */
  dispatchConfirmed: boolean | null
}

const ROLE_LABEL: Record<CallRole, string> = {
  fire_crew: 'RETÉN DE BOMBEROS',
  ambulance: 'DOTACIÓN DE AMBULANCIA',
  ambulance_queued: 'DOTACIÓN DE AMBULANCIA · EN COLA',
  evacuation: 'ORDEN DE EVACUACIÓN',
  neighbor_alert: 'AVISO AL VECINO',
}

export function roleLabel(role: CallRole): string {
  return ROLE_LABEL[role]
}

export class Dispatch {
  readonly calls: SimCall[] = []
  readonly actions: GotoAction[] = []
  /** unidad → tarea por la que está retenida. */
  private readonly holds = new Map<string, string>()
  private readonly heldSince = new Map<string, number>()
  private readonly pendingGoto = new Map<string, { taskId: string; route: string[] }>()
  private readonly dispatchCalled = new Set<string>()
  private readonly ordered = new Map<string, string[]>()
  private readonly calledTasks = new Set<string>()
  private readonly ambulanceCalled = new Set<string>()
  private readonly deferred = new Map<string, { poiId: string; assignment: Assignment | null }>()
  private readonly refused: Unit[] = []
  private crewCalled = false
  private seq = 0

  /** Interruptor de demo: con `false` nadie descuelga y se ve la degradación. */
  answersPhone = true

  get heldUnitIds(): string[] {
    return [...this.holds.keys()].sort()
  }

  isHeld(unitId: string): boolean {
    return this.holds.has(unitId)
  }

  get anyHeld(): boolean {
    return this.holds.size > 0
  }

  routeOf(unitId: string): string[] | null {
    return this.pendingGoto.get(unitId)?.route ?? null
  }

  // --- el reloj de las llamadas ---

  tick(state: SimState, tSim: number): void {
    for (const call of this.calls) {
      if (call.phase === 'ended') continue
      if (call.phase === 'ringing') {
        if (this.answersPhone && tSim - call.startedT >= PICKUP_S) {
          call.phase = 'talking'
          call.answeredT = tSim
          // El reloj se REINICIA al descolgar: los segundos de timbre no se le
          // descuentan a la conversación (`_on_call_started` en el core).
          for (const unitId of call.heldUnits) this.heldSince.set(unitId, tSim)
          this.reveal(call, tSim)
        }
        continue
      }
      this.reveal(call, tSim)
      if (call.said.length >= call.script.length) this.end(call, tSim, 'answered', state)
    }
    this.expireHolds(state, tSim)
  }

  private reveal(call: SimCall, tSim: number): void {
    const since = call.answeredT ?? tSim
    const due = Math.min(call.script.length, Math.floor((tSim - since) / LINE_S) + 1)
    while (call.said.length < due) {
      const line = call.script[call.said.length]
      if (!line) break
      call.said.push(line)
    }
  }

  private end(call: SimCall, tSim: number, outcome: CallOutcome, state: SimState): void {
    call.phase = 'ended'
    call.endedT = tSim
    call.outcome = outcome
    const confirmed = outcome === 'answered' || outcome === 'hung_up'
    for (const unitId of call.heldUnits) this.release(unitId, tSim, confirmed, state)
  }

  /** A los 45 s sonando (o 150 hablando) la unidad sale igual, sin confirmar. */
  private expireHolds(state: SimState, tSim: number): void {
    for (const unitId of [...this.holds.keys()]) {
      const since = this.heldSince.get(unitId) ?? tSim
      const call = this.calls.find((c) => c.heldUnits.includes(unitId) && c.phase !== 'ended')
      const budget = call?.phase === 'talking' ? DISPATCH_TALK_S : DISPATCH_RING_S
      if (tSim - since < budget) continue
      if (call) {
        call.phase = 'ended'
        call.endedT = tSim
        call.outcome = 'no_answer'
      }
      this.release(unitId, tSim, false, state)
    }
  }

  private release(unitId: string, tSim: number, confirmed: boolean, state: SimState): void {
    this.holds.delete(unitId)
    this.heldSince.delete(unitId)
    const pending = this.pendingGoto.get(unitId)
    if (!pending) return
    this.pendingGoto.delete(unitId)
    this.emitGoto(state, unitId, pending.taskId, pending.route, tSim, confirmed)
  }

  /** Una dotación que dice que no puede salir suelta la retención SIN despachar. */
  dropUnavailableHolds(state: SimState, tSim: number): void {
    for (const unitId of [...this.holds.keys()]) {
      if (state.units.get(unitId)?.status === 'unavailable') {
        this.holds.delete(unitId)
        this.heldSince.delete(unitId)
        this.pendingGoto.delete(unitId)
        const call = this.calls.find((c) => c.heldUnits.includes(unitId) && c.phase !== 'ended')
        if (call) call.heldUnits = call.heldUnits.filter((u) => u !== unitId)
        const unit = state.units.get(unitId)
        if (unit) this.refused.push(unit)
        void tSim
      }
    }
  }

  // --- el orden de `_after_plan` ---

  afterPlan(
    state: SimState,
    plan: Plan,
    graph: RoadGraph,
    aliases: Record<string, string>,
    /** Lo que cada unidad está recorriendo ya, para no reemitir el mismo `goto`. */
    following: (unitId: string) => string[] | null = () => null,
  ): void {
    this.dropUnavailableHolds(state, state.t_sim)
    this.emitCrewCall(state, plan, graph, aliases)
    this.emitAmbulanceCall(state, plan, graph, aliases)
    this.emitActions(state, plan, following)
    this.emitEvacuationCalls(state, plan, graph, aliases)
    if (!this.anyHeld) this.flushDeferred(state, plan, graph, aliases)
  }

  /** 1. Al retén: lo que pide el solver, y RETIENE todos los camiones del frente. */
  private emitCrewCall(state: SimState, plan: Plan, graph: RoadGraph, aliases: Record<string, string>): void {
    if (this.crewCalled) return
    const ext = plan.assignments.filter((a) => state.tasks.get(a.task_id)?.kind === 'extinguish')
    if (ext.length === 0) return
    const base = [...state.pois.values()].find((p) => p.kind === 'base')
    const task = state.tasks.get(ext[0]?.task_id ?? '')
    if (!task) return
    this.crewCalled = true

    const held = ext.map((a) => a.unit_id)
    for (const unitId of held) this.hold(unitId, task.id, state.t_sim)

    const requested = requestedUnitsLine(state, ext, aliases)
    const coverage = coverageLine(state, plan, graph)
    const nearest = [...state.pois.values()].sort((a, b) => (a.id < b.id ? -1 : 1))[0]
    const donde = nearest ? fireLine(state, nearest, graph) : 'un frente activo'
    const brief =
      `Tiene un incendio forestal declarado: ${donde}. ${roadsLine(state, aliases)}. ` +
      `Les pedimos ${requested}. ${coverage}.`

    this.push(state, {
      role: 'fire_crew',
      callee: base?.name ?? 'Retén de bomberos',
      poiId: base?.id ?? null,
      taskId: task.id,
      heldUnits: held,
      why: `El solver ha puesto ${held.length === 1 ? 'un camión' : `${held.length} camiones`} sobre ${task.id}. Antes de que salga ninguno se le pide al parque.`,
      facts: [
        { label: 'requested_units', value: requested },
        { label: 'coverage', value: coverage },
        { label: 'roads_status', value: roadsLine(state, aliases) },
      ],
      script: [
        { speaker: 'agente', text: opener('el parque de bomberos') },
        { speaker: 'interlocutor', text: 'Aquí el retén, dígame.' },
        { speaker: 'agente', text: `${brief} ¿Pueden salir ya?` },
        { speaker: 'interlocutor', text: held.length > 1 ? 'Sí, salimos ya los dos.' : 'Sí, salimos ya.' },
        { speaker: 'agente', text: 'Recibido. Quedan movilizados.' },
      ],
    })
  }

  /** 2. A la dotación de la ambulancia. Retiene las ambulancias de esa tarea. */
  private emitAmbulanceCall(state: SimState, plan: Plan, graph: RoadGraph, aliases: Record<string, string>): void {
    const transportTasks = [...state.tasks.values()]
      .filter((t) => !t.done && t.required_capability === 'transport')
      .sort((a, b) => (a.kind === 'rescue' ? -1 : 1) - (b.kind === 'rescue' ? -1 : 1) || (a.id < b.id ? -1 : 1))
    for (const task of transportTasks) {
      if (this.ambulanceCalled.has(task.id)) continue
      const mine = plan.assignments.filter((a) => a.task_id === task.id)
      if (mine.length === 0) continue
      const poi = task.target_poi ? state.pois.get(task.target_poi) : undefined
      if (!poi) continue
      this.ambulanceCalled.add(task.id)

      const held = mine.map((a) => a.unit_id)
      for (const unitId of held) this.hold(unitId, task.id, state.t_sim)

      const { count, immobile } = peopleLine(state, poi.id)
      const requested = requestedUnitsLine(state, mine, aliases)
      const brief =
        task.kind === 'rescue'
          ? `Le piden una ambulancia en ${poi.name}, por un incendio forestal: hay ${immobile} ${immobile === 1 ? 'persona que no puede moverse sola' : 'personas que no pueden moverse solas'}. ${roadsLine(state, aliases)}. Les pedimos ${requested}.`
          : `Hay que evacuar ${poi.name}, ${count} vecinos, por un incendio forestal. ${roadsLine(state, aliases)}. Les pedimos ${requested}.`

      this.push(state, {
        role: 'ambulance',
        callee: 'Dotación de ambulancia',
        poiId: poi.id,
        taskId: task.id,
        heldUnits: held,
        why:
          task.kind === 'rescue'
            ? `El rescate nace de un hecho observado: ${immobile} inmóvil(es) en ${poi.name}. El rescate gana a la evacuación a igual gravedad.`
            : `El solver abre una columna por viaje: ${count} vecinos y ${held.length} ${held.length === 1 ? 'ambulancia' : 'ambulancias'}.`,
        facts: [
          { label: 'requested_units', value: requested },
          { label: 'roads_status', value: roadsLine(state, aliases) },
          { label: 'fire_status', value: fireLine(state, poi, graph) },
          { label: 'resources', value: resourcesLine(state) },
        ],
        script: [
          { speaker: 'agente', text: opener(poi.name) },
          { speaker: 'interlocutor', text: 'Dígame qué tenemos.' },
          { speaker: 'agente', text: `${brief} ¿Pueden ir ya?` },
          { speaker: 'interlocutor', text: 'Sí, salimos del hospital ahora mismo.' },
          { speaker: 'agente', text: 'Recibido, gracias.' },
        ],
      })
    }
  }

  /** 3. Los `goto` de quien NO está retenido. */
  private emitActions(state: SimState, plan: Plan, following: (unitId: string) => string[] | null): void {
    for (const a of plan.assignments) {
      if (a.route.length === 0) continue
      if (this.holds.has(a.unit_id)) {
        // Se SOBRESCRIBE en cada replan: la unidad sale por la ruta más nueva, no
        // por la que se calculó hace dos minutos.
        this.pendingGoto.set(a.unit_id, { taskId: a.task_id, route: a.route })
        continue
      }
      if (this.sameWay(a.unit_id, a.route, following(a.unit_id))) continue
      this.emitGoto(state, a.unit_id, a.task_id, a.route, state.t_sim, null)
    }
  }

  /** ¿La unidad ya va por ahí? Se compara contra lo que está recorriendo AHORA (y,
   *  si está parada, contra lo último que se le ordenó): la ruta nueva arranca del
   *  waypoint más cercano, que cambia según avanza, así que la de hace un segundo
   *  es un sufijo de la de hace diez. Sin esto se reemite el mismo `goto` cada pocos
   *  segundos y el registro de acciones se vuelve ilegible.
   *
   *  Un sufijo NO es lo mismo que «mismo destino»: cuando se corta la pista sur, la
   *  ruta al mismo pueblo por el desvío norte no es sufijo de nada y sí se emite,
   *  que es justo el beat del replan. */
  private sameWay(unitId: string, route: readonly string[], current: readonly string[] | null): boolean {
    for (const before of [current, this.ordered.get(unitId) ?? null]) {
      if (before && isSuffix(before, route)) return true
    }
    return false
  }

  /** 4 y 5. La orden de evacuación y el aviso al vecino. La orden se DIFIERE
   *  mientras quede alguien retenido, y se reconstruye al soltarse para que lleve
   *  los medios que de verdad han confirmado. */
  private emitEvacuationCalls(state: SimState, plan: Plan, graph: RoadGraph, aliases: Record<string, string>): void {
    for (const task of [...state.tasks.values()].sort((a, b) => (a.id < b.id ? -1 : 1))) {
      if (task.done || task.kind !== 'evacuate' || !task.target_poi) continue
      if (this.calledTasks.has(task.id)) continue
      const poi = state.pois.get(task.target_poi)
      if (!poi) continue
      this.calledTasks.add(task.id)
      const assignment = plan.assignments.find((a) => a.task_id === task.id) ?? null
      if (this.anyHeld) {
        this.deferred.set(task.id, { poiId: poi.id, assignment })
        continue
      }
      this.evacuationCall(state, plan, graph, aliases, poi.id, assignment)
      this.neighborAlerts(state, graph, poi.id)
    }
  }

  private flushDeferred(state: SimState, plan: Plan, graph: RoadGraph, aliases: Record<string, string>): void {
    for (const [taskId, info] of [...this.deferred.entries()]) {
      this.deferred.delete(taskId)
      // Se RECONSTRUYE ahora: `committed_resources` tiene que decir lo que salió.
      const fresh = plan.assignments.find((a) => a.task_id === taskId) ?? info.assignment
      this.evacuationCall(state, plan, graph, aliases, info.poiId, fresh)
      this.neighborAlerts(state, graph, info.poiId)
    }
  }

  private evacuationCall(
    state: SimState,
    plan: Plan,
    graph: RoadGraph,
    aliases: Record<string, string>,
    poiId: string,
    assignment: Assignment | null,
  ): void {
    const poi = state.pois.get(poiId)
    if (!poi) return
    const { count } = peopleLine(state, poi.id)
    const committed = committedResourcesLine(
      state,
      plan.assignments.filter((a) => !this.holds.has(a.unit_id)),
      this.refused,
      aliases,
    )
    const route = assignment ? routeName(assignment.route, state.roads, aliases) : 'la salida que tengan libre'
    const deadline = deadlineMin(state, poi, graph, assignment)
    const brief =
      `Ha llegado la orden de evacuar ${poi.name} por ${route} en los próximos ${deadline} minutos, ` +
      `por el incendio forestal. Situación ahora mismo: ${fireLine(state, poi, graph)}; ` +
      `${roadsLine(state, aliases)}; ${unitEtaLine(state, assignment)}. Medios en camino: ${committed}.`

    this.push(state, {
      role: 'evacuation',
      callee: poi.name,
      poiId: poi.id,
      taskId: null,
      heldUnits: [],
      why: 'Los dos medios han colgado: ya se sabe qué ayuda existe, así que ahora se puede prometer.',
      facts: [
        { label: 'committed_resources', value: committed },
        { label: 'route_name', value: route },
        { label: 'deadline_min', value: String(deadline) },
        { label: 'fire_status', value: fireLine(state, poi, graph) },
      ],
      script: [
        { speaker: 'agente', text: opener(poi.name) },
        { speaker: 'interlocutor', text: 'Sí, dígame, estamos viendo el humo desde aquí.' },
        { speaker: 'agente', text: brief },
        { speaker: 'interlocutor', text: `Entendido, la aceptamos. Somos ${count} personas.` },
        { speaker: 'agente', text: 'Recibido. No salgan hasta que les llamemos.' },
      ],
    })
  }

  /** Al que no arde no se le da una orden: se le avisa y se le pregunta acogida. */
  private neighborAlerts(state: SimState, graph: RoadGraph, sourcePoiId: string): void {
    const source = state.pois.get(sourcePoiId)
    if (!source) return
    const { count } = peopleLine(state, sourcePoiId)
    for (const poi of [...state.pois.values()].sort((a, b) => (a.id < b.id ? -1 : 1))) {
      if (poi.kind !== 'village' || poi.id === sourcePoiId) continue
      if (this.calls.some((c) => c.role === 'neighbor_alert' && c.poiId === poi.id)) continue
      const [d] = nearestFireDistance(state, poi, graph)
      if (d <= 60) continue // tiene el fuego en la puerta: recibirá su propia orden
      this.push(state, {
        role: 'neighbor_alert',
        callee: poi.name,
        poiId: poi.id,
        taskId: null,
        heldUnits: [],
        why: `${poi.name} no tiene el fuego encima. No se le ordena salir: se le avisa de que puede llegarle gente.`,
        facts: [
          { label: 'incoming_people', value: String(count) },
          { label: 'source_poi_name', value: source.name },
          { label: 'fire_status', value: fireLine(state, poi, graph) },
        ],
        script: [
          { speaker: 'agente', text: opener(poi.name) },
          { speaker: 'interlocutor', text: 'Sí, dígame.' },
          {
            speaker: 'agente',
            text: `Está usted en ${poi.name}, que ahora mismo no tiene el fuego encima. Le aviso porque se está evacuando ${source.name} y pueden llegarles del orden de ${count} personas. ¿Tienen sitio para acogerlas?`,
          },
          { speaker: 'interlocutor', text: 'Sí, el polideportivo está abierto, caben de sobra.' },
          { speaker: 'agente', text: 'Perfecto. No hace falta que hagan nada más de momento; les avisamos si cambia algo.' },
        ],
      })
    }
  }

  // --- interno ---

  private hold(unitId: string, taskId: string, tSim: number): void {
    if (this.holds.has(unitId)) return
    this.holds.set(unitId, taskId)
    this.heldSince.set(unitId, tSim)
    this.dispatchCalled.add(unitId)
  }

  /** Reencamina una unidad que ya iba de camino, por un corte de carretera.
   *
   *  Se emite como una orden normal en vez de tocar el `Movement` por detrás: así
   *  `ordered` queda al día —o `sameWay` suprimiría después una orden legítima por
   *  compararla con una ruta obsoleta— y sigue habiendo **un solo camino** por el que
   *  una unidad se pone en marcha, que es la invariante que arregló el
   *  teletransporte y que no conviene romper con una segunda vía. */
  reroute(state: SimState, unitId: string, taskId: string, route: string[]): void {
    if (route.length < 2) return
    this.emitGoto(state, unitId, taskId, route, state.t_sim, null)
  }

  /** Manda una unidad de vuelta a casa. Devuelve `false` si ya se le ordenó: la vuelta
   *  se ordena UNA vez, o el plan siguiente la repetiría en cada tick. */
  returnHome(state: SimState, unitId: string, home: string, route: string[]): boolean {
    if (route.length < 2) return false
    const last = this.ordered.get(unitId)
    if (last && last[last.length - 1] === home) return false
    this.emitGoto(state, unitId, RETURN_TASK, route, state.t_sim, null)
    return true
  }

  private emitGoto(
    state: SimState,
    unitId: string,
    taskId: string,
    route: string[],
    tSim: number,
    confirmed: boolean | null,
  ): void {
    this.seq++
    this.ordered.set(unitId, [...route])
    this.actions.push({
      id: `act_${state.run_id}_${this.seq}`,
      unitId,
      taskId,
      route: [...route],
      tSim,
      dispatchConfirmed: confirmed,
    })
  }

  private push(
    state: SimState,
    c: Omit<SimCall, 'id' | 'phase' | 'startedT' | 'answeredT' | 'endedT' | 'outcome' | 'said'>,
  ): void {
    this.seq++
    this.calls.push({
      ...c,
      id: `call_${state.run_id}_${this.seq}`,
      phase: 'ringing',
      startedT: state.t_sim,
      answeredT: null,
      endedT: null,
      outcome: null,
      said: [],
    })
  }
}

/** ¿Son la misma marcha? La corta tiene que ser sufijo de la larga, en cualquiera de
 *  los dos sentidos. Los dos casos pasan de verdad: la ruta nueva se acorta según la
 *  unidad avanza (el plan arranca del waypoint más cercano), y se ALARGA cuando la
 *  unidad ya ha llegado a un waypoint que la orden anterior no incluía —una orden
 *  retenida se calculó desde donde la unidad estaba entonces—. Sin mirar los dos
 *  sentidos, la misma marcha se ordena dos veces en el mismo tick. */
function isSuffix(a: readonly string[], b: readonly string[]): boolean {
  const [short, long] = a.length <= b.length ? [a, b] : [b, a]
  if (short.length === 0) return false
  const tail = long.slice(long.length - short.length)
  return tail.every((wp, i) => wp === short[i])
}

function opener(about: string): string {
  return `Buenos días, le llamo del centro de coordinación de emergencias por el incendio forestal. Es una llamada importante sobre ${about}, ¿puede atenderme un momento?`
}

function nearestFireDistance(state: SimState, poi: { x: number; z: number }, graph: RoadGraph): [number] {
  let best = Infinity
  const size = graph.geo.cellSize
  for (const cell of state.cells.values()) {
    if (cell.state !== 'burning') continue
    const cx = graph.geo.origin[0] + (cell.cx + 0.5) * size
    const cz = graph.geo.origin[1] + (cell.cz + 0.5) * size
    best = Math.min(best, Math.hypot(poi.x - cx, poi.z - cz))
  }
  return [best]
}

export { minutes, unitName }
