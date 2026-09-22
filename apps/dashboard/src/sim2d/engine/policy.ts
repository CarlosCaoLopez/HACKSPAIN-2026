// La Policy: qué importa, nunca quién va.
//
// En el sistema de verdad esto lo produce un LLM (GPT-5.6 Luna, una llamada por
// replan). Aquí lo produce un conjunto de reglas sobre el estado, por dos razones: la
// página no toca la red, y una regla deja ver POR QUÉ salió ese peso, que es justo lo
// que el panel de la derecha tiene que enseñar.
//
// Invariante 2, que es el que contesta la pregunta de auditoría del jurado: esta
// función NO puede devolver asignaciones. El catálogo de pesos es cerrado (los cinco
// de `contracts.plan.WEIGHTS`) y el de restricciones también (los cuatro).
import type { POI, Policy, Task } from '../../types'


import type { RoadGraph } from './graph'
import type { SimState } from './state'
import { CRITICAL_DISTANCE_M, downwind, DOWNWIND_DISTANCE_M } from './tasks'
import type { WeightName } from './solver'

/** Las capacidades de las que tiene sentido guardar reserva. El dron queda fuera: hay
 *  uno solo, así que reservarlo sería no usarlo nunca. */
const RESERVABLE = ['extinguish', 'transport'] as const

/** Una reserva que se ha SOLTADO, con el motivo. Se enseña igual que la que se guarda:
 *  que el sistema ceda la reserva ante algo crítico es una decisión, no una ausencia. */
export interface ReleasedReserve {
  capability: string
  why: string
}

/** Un peso con el porqué: es lo que `explain.ts` convierte en una frase. */
export interface WeightEvidence {
  name: WeightName
  value: number
  /** Los hechos del estado que lo produjeron, ya redactados. */
  evidence: string[]
}

export interface PolicyResult {
  policy: Policy
  weights: WeightEvidence[]
  constraints: Array<{ expr: string; evidence: string[] }>
  /** Reservas que esta Policy ha decidido NO guardar, y por qué. */
  released: ReleasedReserve[]
}

/** Segundos que una tarea crítica lleva sin nadie asignado. */
function uncoveredCriticalAge(state: SimState, assignedTasks: ReadonlySet<string>): { task: Task; age: number } | null {
  let worst: { task: Task; age: number } | null = null
  for (const task of state.tasks.values()) {
    if (task.done || task.severity !== 'critical' || assignedTasks.has(task.id)) continue
    const age = state.t_sim - task.created_t
    if (!worst || age > worst.age) worst = { task, age }
  }
  return worst
}

function burningCenters(state: SimState, graph: RoadGraph): Array<[number, number]> {
  const size = graph.geo.cellSize
  const out: Array<[number, number]> = []
  for (const cell of state.cells.values()) {
    if (cell.state !== 'burning') continue
    out.push([
      graph.geo.origin[0] + (cell.cx + 0.5) * size,
      graph.geo.origin[1] + (cell.cz + 0.5) * size,
    ])
  }
  return out
}

function nearestFireM(poi: POI, centers: ReadonlyArray<[number, number]>): number {
  let best = Infinity
  for (const [x, z] of centers) best = Math.min(best, Math.hypot(poi.x - x, poi.z - z))
  return best
}

export interface PolicyInputs {
  /** Celdas ardiendo hace 30 s, para saber si el frente crece. */
  burningBefore: number
  assignedTasks: ReadonlySet<string>
}

export function buildPolicy(state: SimState, graph: RoadGraph, inputs: PolicyInputs): PolicyResult {
  const weights: WeightEvidence[] = []
  const constraints: Array<{ expr: string; evidence: string[] }> = []
  const released: ReleasedReserve[] = []
  const centers = burningCenters(state, graph)
  const groups = [...state.civilians.values()].sort((a, b) => (a.id < b.id ? -1 : 1))

  // --- life_safety ---
  const exposed = groups.filter((g) => g.state === 'exposed' && g.count > 0)
  if (exposed.length > 0) {
    const close = exposed.filter((g) => {
      const poi = state.pois.get(g.poi_id)
      return poi !== undefined && nearestFireM(poi, centers) <= CRITICAL_DISTANCE_M
    })
    const value = close.length > 0 ? 0.9 : 0.6
    weights.push({
      name: 'life_safety',
      value,
      evidence: exposed.map((g) => {
        const poi = state.pois.get(g.poi_id)
        const d = poi ? nearestFireM(poi, centers) : Infinity
        const where = Number.isFinite(d) ? `, el frente a ${Math.round(d)} m` : ''
        return `${poi?.name ?? g.poi_id}: ${g.count} personas expuestas${where}`
      }),
    })
  }

  // --- immobile_first ---
  const immobile = groups.filter((g) => g.immobile > 0)
  if (immobile.length > 0) {
    const hasRescue = [...state.tasks.values()].some((t) => t.kind === 'rescue' && !t.done)
    const value = hasRescue ? 0.95 : 0.7
    const evidence = immobile.map((g) => {
      const poi = state.pois.get(g.poi_id)
      return `${poi?.name ?? g.poi_id}: ${g.immobile} de ${g.count} no pueden moverse solas`
    })
    evidence.push(
      hasRescue
        ? 'hay una tarea de rescate abierta: un hecho observado lo confirmó'
        : 'todavía ningún hecho observado lo confirma: no hay tarea de rescate',
    )
    weights.push({ name: 'immobile_first', value, evidence })
  }

  // --- structure_protection ---
  const threatened: string[] = []
  for (const poi of state.pois.values()) {
    if (poi.kind !== 'village' && poi.kind !== 'hospital' && poi.kind !== 'shelter') continue
    for (const [x, z] of centers) {
      const d = Math.hypot(poi.x - x, poi.z - z)
      if (d <= DOWNWIND_DISTANCE_M && downwind(x, z, poi, state.wind)) {
        threatened.push(`${poi.name}: a ${Math.round(d)} m y a sotavento`)
        break
      }
    }
  }
  if (threatened.length > 0) {
    weights.push({ name: 'structure_protection', value: 0.5, evidence: threatened.sort() })
  }

  // --- containment ---
  if (centers.length > 0) {
    const growing = centers.length > inputs.burningBefore
    const value = growing ? 0.7 : 0.4
    weights.push({
      name: 'containment',
      value,
      evidence: [
        growing
          ? `el frente ha pasado de ${inputs.burningBefore} a ${centers.length} celdas ardiendo`
          : `${centers.length} celdas ardiendo, el frente no crece`,
      ],
    })
  }

  // --- response_time ---
  const stale = uncoveredCriticalAge(state, inputs.assignedTasks)
  const rtValue = stale && stale.age > 60 ? 0.5 : 0.2
  weights.push({
    name: 'response_time',
    value: rtValue,
    evidence:
      stale && stale.age > 60
        ? [`${stale.task.id} lleva ${Math.round(stale.age)} s crítica y sin nadie asignado`]
        : ['ninguna tarea crítica lleva más de un minuto descubierta'],
  })

  // --- restricciones duras ---
  constraints.push({
    expr: 'no_unit_into_burning_cell',
    evidence:
      centers.length > 0
        ? [`${centers.length} celdas ardiendo; ninguna ruta puede atravesarlas`]
        : ['no hay fuego todavía: hoy no veta ninguna ruta'],
  })
  // Un waypoint cuya celda arde no puede estar en una ruta de evacuación.
  for (const wp of graph.waypointIds.sort()) {
    const p = graph.at(wp)
    if (!p) continue
    const cell = state.cells.get(graph.cellOf(p[0], p[1]))
    if (cell?.state === 'burning') {
      constraints.push({
        expr: `no_civilian_route_through:${wp}`,
        evidence: [`${wp} está en ${cell.id}, que arde: los civiles no pasan por ahí`],
      })
    }
  }
  // --- la reserva ---
  //
  // Uno de los motivos de usar un solver determinista es que NO lo compromete todo:
  // guarda medios por si entra otra emergencia. Se guarda uno de cada capacidad
  // mientras sobren, y **la reserva cede** si hay una tarea crítica sin cubrir que la
  // necesita: un medio parado mientras arde un pueblo no es prudencia, es un fallo.
  //
  // La regla de antes solo reservaba con un rescate abierto, y por eso no se
  // disparaba nunca en este escenario: salían 2/2 camiones y 4/4 ambulancias siempre.
  for (const capability of RESERVABLE) {
    const active = [...state.units.values()].filter(
      (u) => u.capabilities.includes(capability) && u.status !== 'unavailable',
    )
    // Con un solo medio no hay reserva posible: guardarlo es no cubrir nada.
    if (active.length <= 1) continue

    const urgent = [...state.tasks.values()].filter(
      (t) => !t.done && t.severity === 'critical' && t.required_capability === capability,
    )
    const word = capability === 'transport' ? 'de transporte' : 'de extinción'

    // La reserva cede cuando guardarla dejaría una tarea CRÍTICA sin NADIE. No cuando
    // le faltan medios para ir más rápido: tres ambulancias evacuando un pueblo lo
    // evacúan, solo que en más viajes, y eso es exactamente el precio de tener una
    // guardada.
    //
    // Mi primera versión preguntaba si la tarea estaba asignada en el plan ANTERIOR,
    // y en el primer plan no hay anterior: toda tarea crítica salía «sin nadie» y la
    // reserva se soltaba siempre. Medido: 4/4 ambulancias desde el segundo uno, que
    // es justo lo que había que arreglar.
    if (urgent.length > active.length - 1) {
      released.push({
        capability,
        why: `hay ${urgent.length} ${urgent.length === 1 ? 'tarea crítica' : 'tareas críticas'} (${urgent
          .map((t) => t.id)
          .join(', ')}) y solo ${active.length} medios ${word}: guardar uno dejaría alguna sin nadie`,
      })
      continue
    }
    constraints.push({
      expr: `reserve_capability:${capability}:1`,
      evidence: [
        `${active.length} medios ${word} activos: se guarda 1 libre`,
        'si entra otra emergencia, ese medio sale de inmediato',
      ],
    })
  }

  const dominant = [...weights]
    .filter((w) => w.name !== 'response_time')
    .sort((a, b) => b.value - a.value)[0]

  return {
    policy: {
      rationale: rationaleFor(dominant, state, centers.length),
      weights: Object.fromEntries(weights.map((w) => [w.name, w.value])),
      hard_constraints: constraints.map((c) => c.expr),
      horizon_s: 600,
      escalate_to_human: stale !== null && stale.age > 90,
      notify: [],
    },
    weights,
    constraints,
    released,
  }
}

function rationaleFor(
  dominant: WeightEvidence | undefined,
  state: SimState,
  burning: number,
): string {
  if (!dominant) {
    return burning > 0
      ? 'Contener el frente: todavía no hay nadie expuesto.'
      : 'Sin fuego declarado: nada que priorizar.'
  }
  const villages = [...state.pois.values()].filter((p) => p.kind === 'village')
  const name = villages[0]?.name ?? 'el pueblo'
  switch (dominant.name) {
    case 'immobile_first':
      return `Primero quien no puede salir por su pie; después, contener el frente.`
    case 'life_safety':
      return `Sacar a los civiles expuestos antes que contener: el fuego llega a ${name} antes que los medios.`
    case 'structure_protection':
      return 'Proteger los núcleos que el viento tiene a sotavento.'
    case 'containment':
      return 'Contener el frente mientras no haya nadie expuesto.'
    default:
      return 'Cubrir lo crítico con los medios que quedan.'
  }
}
