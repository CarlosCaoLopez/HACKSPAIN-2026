// Cuánto se ha alejado el mundo de lo que el plan daba por cierto.
//
// Puerto de `packages/core/src/core/divergence.py`. Es el número que decide cuándo se
// tira el plan, y el que cruza 0,25 en el gráfico del dashboard.
//
// Una suposición que no se puede evaluar NO cuenta: ni en el numerador ni en el
// denominador. Si contara como rota, un plan viejo con claves que ya no existen daría
// divergencia 1 y replanificaría para siempre.
import { DIVERGENCE_THRESHOLD } from '../../types'
import type { Assumption, PlanContext } from '../../types'

import { angularGap } from './geom'
import { latestFact, type SimState } from './state'

export const WIND_TOLERANCE_DEG = 20.0

export interface Term {
  key: string
  expected: string | number | boolean
  actual: string | number | boolean | null
  weight: number
  /** `null` = no evaluable: no suma ni en roto ni en total. */
  broken: boolean | null
}

export interface DivergenceResult {
  value: number
  terms: Term[]
  broken: string[]
  brokenWeight: number
  totalWeight: number
}

function evaluate(state: SimState, a: Assumption): { actual: Term['actual']; broken: boolean | null } {
  const road = /^road:(.+):open$/.exec(a.key)
  if (road?.[1] !== undefined) {
    const edge = state.roads.get(`road:${road[1]}`) ?? state.roads.get(road[1])
    if (!edge) return { actual: null, broken: null }
    const open = !edge.cut
    return { actual: open, broken: open !== a.expected }
  }
  if (a.key === 'wind:bearing_deg') {
    const actual = state.wind.bearing_deg
    const expected = Number(a.expected)
    // Circular, con tolerancia: 350° y 10° distan 20°, no 340°.
    return { actual, broken: angularGap(actual, expected) > WIND_TOLERANCE_DEG }
  }
  if (a.key === 'wind:speed') {
    const actual = state.wind.speed
    return { actual, broken: !closeEnough(actual, Number(a.expected)) }
  }
  const cell = /^cell:(.+):state$/.exec(a.key)
  if (cell?.[1] !== undefined) {
    const c = state.cells.get(cell[1])
    const actual = c?.state ?? 'intact'
    return { actual, broken: actual !== a.expected }
  }
  const fact = latestFact(state, a.key)
  if (!fact) return { actual: null, broken: null }
  if (typeof fact.value === 'number' && typeof a.expected === 'number') {
    return { actual: fact.value, broken: !closeEnough(fact.value, a.expected) }
  }
  return { actual: fact.value, broken: fact.value !== a.expected }
}

function closeEnough(a: number, b: number): boolean {
  return Math.abs(a - b) <= 1e-6 * Math.max(1, Math.abs(b))
}

export function divergence(state: SimState, context: PlanContext): DivergenceResult {
  const terms: Term[] = []
  let brokenWeight = 0
  let totalWeight = 0
  const broken: string[] = []

  for (const a of context.assumptions) {
    const { actual, broken: isBroken } = evaluate(state, a)
    terms.push({ key: a.key, expected: a.expected, actual, weight: a.weight, broken: isBroken })
    if (isBroken === null) continue
    totalWeight += a.weight
    if (isBroken) {
      brokenWeight += a.weight
      broken.push(a.key)
    }
  }

  return {
    value: totalWeight > 0 ? brokenWeight / totalWeight : 0,
    terms,
    broken,
    brokenWeight,
    totalWeight,
  }
}

export interface ReplanDecision {
  flag: boolean
  reason: string
}

/** El mismo orden que `divergence.should_replan`: una violación dura gana a un hecho
 *  crítico, y los dos ganan al umbral. El orden importa porque es el que se enseña. */
export function shouldReplan(
  value: number,
  hardViolations: number,
  criticalFacts: readonly string[],
): ReplanDecision {
  if (hardViolations > 0) return { flag: true, reason: 'restricción dura violada' }
  if (criticalFacts.length > 0) return { flag: true, reason: `hecho crítico: ${criticalFacts.join(', ')}` }
  if (value > DIVERGENCE_THRESHOLD) {
    return {
      flag: true,
      reason: `divergencia ${value.toFixed(2).replace('.', ',')} > ${String(DIVERGENCE_THRESHOLD).replace('.', ',')}`,
    }
  }
  return { flag: false, reason: '' }
}
