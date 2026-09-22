// «Y su significado». Traduce cada decisión a una frase de persona, citando el estado
// que la produjo.
//
// La regla que hace que esto valga algo: **una explicación no se escribe, se deriva**.
// Cada frase sale de los mismos números que usó el solver, y la ecuación de coste se
// imprime término a término desde la `CostTrace` que devuelve `weightedCost`. Si la
// multiplicación que se ve en pantalla no da el coste que el solver guardó, la
// explicación está mintiendo — y una explicación que miente es peor que no tenerla.
import type { Assignment, Plan, Task } from '../types'

import type { DivergenceResult } from './engine/divergence'
import type { RoadGraph } from './graph-types'
import type { WeightEvidence } from './engine/policy'
import type { CostTrace, Infeasibility, WeightName } from './engine/solver'
import { WEIGHT_DISCOUNT } from './engine/solver'
import type { SimState } from './engine/state'
import { unitName } from './engine/calls'

export interface Explanation {
  /** Lo que se lee a diez metros. */
  headline: string
  /** La frase de persona. */
  meaning: string
  /** El estado que lo produjo, con ids y números. */
  evidence: string[]
  /** Qué le hace al coste, con el factor. */
  effect?: string
}

const N = new Intl.NumberFormat('es-ES', { maximumFractionDigits: 2 })
const N1 = new Intl.NumberFormat('es-ES', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
/** Tres decimales, y solo dentro de la ecuación de coste.
 *
 *  No es pedantería: el descuento de un peso de 0,70 es 0,545, y redondeado a 0,55 la
 *  multiplicación que se lee en pantalla da 51,81 donde el solver guardó 51,47. Quien
 *  compruebe la cuenta a mano —que es exactamente lo que queremos que haga— encontraría
 *  un descuadre y dejaría de fiarse de todo el panel. La cifra que se enseña tiene que
 *  reproducir el resultado. */
const N3 = new Intl.NumberFormat('es-ES', { minimumFractionDigits: 3, maximumFractionDigits: 3 })

export function num(v: number): string {
  return N.format(v)
}
export function num2(v: number): string {
  return N1.format(v)
}
export function num3(v: number): string {
  return N3.format(v)
}
/** Metros con un decimal: la suma de la ecuación tiene que cuadrar igual que los
 *  factores. Fuera de la ecuación se siguen redondeando a entero, que es como se leen. */
function metrosExactos(v: number): string {
  return `${N1.format(v)} m`
}
export function metros(v: number): string {
  return `${Math.round(v)} m`
}
export function segundos(v: number): string {
  if (!Number.isFinite(v)) return '—'
  const s = Math.round(v)
  return s < 60 ? `${s} s` : `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

// --- pesos ---

const WEIGHT_MEANING: Record<WeightName, string> = {
  life_safety: 'Sacar a la gente antes que salvar cosas.',
  immobile_first: 'Antes que a nadie, a quien no puede salir por su pie.',
  structure_protection: 'Defender los núcleos habitados que el viento tiene enfrente.',
  containment: 'Frenar el frente donde todavía se puede.',
  response_time: 'Cuánto pesa la distancia frente a la gravedad.',
}

export function explainWeight(w: WeightEvidence): Explanation {
  const discount = 1 - (1 - WEIGHT_DISCOUNT) * Math.min(w.value, 1)
  const effect =
    w.name === 'response_time'
      ? `Es el único peso que PENALIZA en vez de descontar: multiplica todos los costes por ${num2(1 + w.value)}. No cambia a quién se manda, cambia cuánto pesa la distancia.`
      : `Abarata un ${num(Math.round((1 - discount) * 100))} % el coste de toda tarea a la que se aplica: lo multiplica por ${num2(discount)}.`
  return {
    headline: `${w.name} ${num2(w.value)}`,
    meaning: WEIGHT_MEANING[w.name],
    evidence: w.evidence,
    effect,
  }
}

// --- restricciones duras ---

const CONSTRAINT_MEANING: Record<string, string> = {
  no_unit_into_burning_cell:
    'Ninguna ruta puede cruzar una celda que arde. El destino de un frente no cuenta: ahí es justo donde va a trabajar el camión.',
  no_civilian_route_through: 'Por ese punto no se evacúa a nadie.',
  hospital_min_coverage: 'El hospital no se queda por debajo de esa cobertura.',
  reserve_capability:
    'Se guardan esos medios libres por si entra otra emergencia. La reserva se suelta si aparece una tarea crítica que la necesita: un medio parado mientras arde un pueblo no es prudencia.',
}

export function explainConstraint(expr: string, evidence: string[]): Explanation {
  const name = expr.split(':')[0] ?? expr
  return {
    headline: expr,
    meaning: CONSTRAINT_MEANING[name] ?? 'Restricción fuera del catálogo.',
    evidence,
  }
}

// --- la ecuación de coste ---

export interface CostEquation {
  baseM: number
  offroadM: number
  terms: Array<{ label: string; factor: number }>
  total: number
  /** La cadena completa, ya con coma decimal. */
  text: string
  inapplicable: CostTrace['inapplicable']
}

export function costEquation(trace: CostTrace): CostEquation {
  const head =
    trace.offroadM > 0.05
      ? `(${metrosExactos(trace.baseM)} ruta + ${metrosExactos(trace.offroadM)} a pie)`
      : metrosExactos(trace.baseM)
  const body = trace.terms.map((t) => ` × ${num3(t.factor)} (${t.label})`).join('')
  return {
    baseM: trace.baseM,
    offroadM: trace.offroadM,
    terms: trace.terms.map((t) => ({ label: t.label, factor: t.factor })),
    total: trace.total,
    text: `coste = ${head}${body} = ${num2(trace.total)}`,
    inapplicable: trace.inapplicable,
  }
}

// --- asignaciones ---

export interface AssignmentExplanation extends Explanation {
  unitId: string
  taskId: string
  key: string
  routeText: string
  destinationWaypoint: string
  etaText: string
  equation: CostEquation | null
}

function taskWhere(state: SimState, task: Task, graph: RoadGraph): string {
  if (task.target_poi) return state.pois.get(task.target_poi)?.name ?? task.target_poi
  if (task.target_cell) {
    const c = state.cells.get(task.target_cell)
    if (c) {
      const size = graph.geo.cellSize
      const x = Math.round(graph.geo.origin[0] + (c.cx + 0.5) * size)
      const z = Math.round(graph.geo.origin[1] + (c.cz + 0.5) * size)
      return `el frente en (${x}, ${z})`
    }
  }
  return task.id
}

const KIND_VERB: Record<string, string> = {
  extinguish: 'atacar',
  evacuate: 'evacuar',
  rescue: 'rescatar a los inmóviles de',
  notify: 'avisar a',
  recon: 'reconocer',
  restore: 'restablecer',
}

export function explainAssignment(
  a: Assignment,
  state: SimState,
  graph: RoadGraph,
  trace: CostTrace | undefined,
  routeLabel: string,
): AssignmentExplanation {
  const unit = state.units.get(a.unit_id)
  const task = state.tasks.get(a.task_id)
  const name = unit ? unitName(unit) : a.unit_id
  const where = task ? taskWhere(state, task, graph) : a.task_id
  const verb = task ? (KIND_VERB[task.kind] ?? task.kind) : ''
  const destination = a.route[a.route.length - 1] ?? ''
  const wp = graph.at(destination)
  const at = wp ? ` en (${Math.round(wp[0])}, ${Math.round(wp[1])})` : ''

  const evidence: string[] = []
  if (task) evidence.push(`tarea ${task.id}, gravedad ${task.severity}`)
  evidence.push(`ruta ${a.route.join(' → ')} · ${metros(graph.routeLengthM(a.route))}`)
  if (unit) evidence.push(`capacidades de ${name}: ${unit.capabilities.join(', ') || 'ninguna'}`)

  return {
    key: `${a.unit_id}:${a.task_id}`,
    unitId: a.unit_id,
    taskId: a.task_id,
    headline: `${cap(name)} → ${where}`,
    meaning: `Enviar ${elLa(name)} ${name} a ${verb} ${where}${at}, por ${routeLabel}. Llega en ${segundos(a.eta_s)}.`,
    evidence,
    routeText: `${a.route.join(' → ')} · ${metros(graph.routeLengthM(a.route))}`,
    destinationWaypoint: destination,
    etaText: segundos(a.eta_s),
    equation: trace ? costEquation(trace) : null,
  }
}

const INFEASIBLE_WHY: Record<Infeasibility['reason'], string> = {
  capability: 'no tiene la capacidad que la tarea pide',
  no_route: 'no hay ninguna ruta abierta hasta allí',
  burning_cell: 'la única ruta cruza una celda que arde',
  civilian_route: 'su ruta pasa por un punto vetado a civiles',
  no_target: 'la tarea no tiene todavía un punto al que ir',
  reserved: 'nadie va: la política guarda ese medio libre por si entra otra emergencia',
}

export function explainUnassigned(
  taskId: string,
  state: SimState,
  infeasible: readonly Infeasibility[],
  graph: RoadGraph,
): Explanation {
  const task = state.tasks.get(taskId)
  const mine = infeasible.filter((i) => i.taskId === taskId)
  const byReason = new Map<Infeasibility['reason'], string[]>()
  for (const i of mine) {
    const list = byReason.get(i.reason) ?? []
    const u = state.units.get(i.unitId)
    list.push(u ? unitName(u) : i.unitId)
    byReason.set(i.reason, list)
  }
  const evidence = [...byReason.entries()].map(([reason, units]) => `${units.join(', ')}: ${INFEASIBLE_WHY[reason]}`)
  if (evidence.length === 0) evidence.push('no quedaba ninguna unidad libre cuando se repartió')
  return {
    headline: task ? `${taskWhere(state, task, graph)} — sin cubrir` : `${taskId} — sin cubrir`,
    meaning: 'Nadie va. El hueco se enseña en vez de taparse: priorizar con los medios que quedan, no con los que harían falta.',
    evidence,
  }
}

// --- divergencia y replan ---

export function explainDivergence(d: DivergenceResult, threshold: number): Explanation {
  const evidence = d.terms.map((t) => {
    if (t.broken === null) return `· ${t.key}: no evaluable, no cuenta`
    const mark = t.broken ? '✗' : '✓'
    const actual = t.actual === null ? '—' : String(t.actual)
    return `${mark} ${t.key}: esperaba ${String(t.expected)}, ahora ${actual} · peso ${num2(t.weight)}`
  })
  evidence.push(
    `divergencia = ${num2(d.brokenWeight)} / ${num2(d.totalWeight)} = ${num2(d.value)} ${d.value > threshold ? '>' : '≤'} ${num2(threshold)}`,
  )
  return {
    headline: `divergencia ${num2(d.value)}`,
    meaning:
      d.value > threshold
        ? 'El plan ya no se sostiene sobre lo que daba por cierto: se recalcula.'
        : `${num2(d.brokenWeight)} de ${num2(d.totalWeight)} puntos de suposición se han roto. El umbral es ${num2(threshold)}, así que el plan sigue en pie.`,
    evidence,
  }
}

// --- utilidades de redacción ---

function cap(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1)
}

/** «el camión», «la ambulancia». Sale mal una vez de cada cien y se lee natural. */
function elLa(name: string): string {
  return name.startsWith('camión') || name.startsWith('dron') ? 'el' : 'la'
}

export function planSummary(plan: Plan | null): string {
  if (!plan) return 'todavía no hay plan'
  const n = plan.assignments.length
  const u = plan.unassigned_tasks.length
  return `${n} ${n === 1 ? 'asignación' : 'asignaciones'}${u > 0 ? ` · ${u} sin cubrir` : ''}`
}
