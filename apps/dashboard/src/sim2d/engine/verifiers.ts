// Los verificadores: qué es inviable en un plan ya construido.
//
// Puerto de `packages/core/src/core/verifiers.py`. Son puros y no arreglan nada: solo
// dicen qué está mal, en una frase legible, porque esa frase va al dashboard y al
// banner. Una violación `hard` es lo que dispara el replan antes que ninguna otra cosa.
import type { Plan, Violation } from '../../types'

import type { RoadGraph } from './graph'
import type { SimState } from './state'

/** La ruta existe en el grafo VIVO (sin cortes) y no cruza fuego. */
function routeFeasible(state: SimState, plan: Plan): Violation[] {
  const out: Violation[] = []
  for (const a of plan.assignments) {
    for (let i = 0; i + 1 < a.route.length; i++) {
      const from = a.route[i]
      const to = a.route[i + 1]
      if (from === undefined || to === undefined) continue
      const edge = [...state.roads.values()].find(
        (r) => (r.a === from && r.b === to) || (r.a === to && r.b === from),
      )
      if (!edge) {
        out.push({
          verifier: 'route_feasible',
          severity: 'hard',
          message: `ruta ${from}→${to} no existe (unidad ${a.unit_id})`,
          involved: [a.unit_id, a.task_id],
        })
        break
      }
      if (edge.cut) {
        out.push({
          verifier: 'route_feasible',
          severity: 'hard',
          message: `la ruta de ${a.unit_id} cruza ${edge.id}, cortado${edge.cut_cause ? ` por ${edge.cut_cause}` : ''}`,
          involved: [a.unit_id, edge.id, a.task_id],
        })
        break
      }
    }
  }
  return out
}

/** Ningún POI baja de su cobertura mínima. En `wildfire_ridge` es 0 a propósito. */
function coverageMaintained(state: SimState, plan: Plan, graph: RoadGraph): Violation[] {
  const out: Violation[] = []
  const leaving = new Set(plan.assignments.map((a) => a.unit_id))
  for (const poi of state.pois.values()) {
    if (poi.min_coverage <= 0) continue
    const wp = graph.at(poi.waypoint_id)
    if (!wp) continue
    let staying = 0
    for (const unit of state.units.values()) {
      if (unit.status === 'unavailable' || leaving.has(unit.id)) continue
      if (Math.hypot(unit.x - wp[0], unit.z - wp[1]) <= 30) staying++
    }
    if (staying < poi.min_coverage) {
      out.push({
        verifier: 'coverage_maintained',
        severity: 'hard',
        message: `${poi.name} se queda con ${staying} unidades y necesita ${poi.min_coverage}`,
        involved: [poi.id],
      })
    }
  }
  return out
}

/** Una unidad, una tarea. Dos asignaciones con el MISMO `task_id` sí son legales:
 *  es lo que permite mandar dos camiones al mismo frente. */
function noDoubleBooking(plan: Plan): Violation[] {
  const seen = new Set<string>()
  const out: Violation[] = []
  for (const a of plan.assignments) {
    if (seen.has(a.unit_id)) {
      out.push({
        verifier: 'no_double_booking',
        severity: 'hard',
        message: `${a.unit_id} está asignada dos veces`,
        involved: [a.unit_id],
      })
    }
    seen.add(a.unit_id)
  }
  return out
}

/** Una evacuación sin ruta desde el pueblo a un sitio seguro es una promesa vacía. */
function civilianReachable(state: SimState, plan: Plan, graph: RoadGraph): Violation[] {
  const out: Violation[] = []
  const cut = new Set([...state.roads.values()].filter((r) => r.cut).map((r) => r.id))
  const live = graph.withCuts(cut)
  for (const a of plan.assignments) {
    const task = state.tasks.get(a.task_id)
    if (!task || task.kind !== 'evacuate' || !task.target_poi) continue
    const poi = state.pois.get(task.target_poi)
    if (!poi) continue
    const shelters = [...state.pois.values()].filter((p) => p.kind === 'shelter' || p.kind === 'village')
    const reachable = shelters.some(
      (s) => s.id !== poi.id && live.shortestPath(poi.waypoint_id, s.waypoint_id) !== null,
    )
    if (!reachable) {
      out.push({
        verifier: 'civilian_reachable',
        severity: 'hard',
        message: `desde ${poi.name} no se llega a ningún sitio seguro`,
        involved: [poi.id, a.unit_id],
      })
    }
  }
  return out
}

export function verify(state: SimState, plan: Plan, graph: RoadGraph): Violation[] {
  return [
    ...routeFeasible(state, plan),
    ...coverageMaintained(state, plan, graph),
    ...noDoubleBooking(plan),
    ...civilianReachable(state, plan, graph),
  ]
}
