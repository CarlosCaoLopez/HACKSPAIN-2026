// Comprueba que el port de TypeScript reparte IGUAL que el core en Python.
//
// Es la comprobación que justifica todo lo demás: si el solver de esta página se
// desvía del de `packages/core`, la Simulación 2D enseña un reparto que el sistema de
// verdad no haría, y entonces no demuestra nada. Medido: con el mismo estado y la
// misma Policy coinciden las tareas, las unidades, las columnas, la matriz de costes
// celda a celda, las asignaciones (unidad, tarea, ruta, ETA, coste), lo que queda sin
// cubrir y las suposiciones del `PlanContext`.
//
// No entra en el bundle (nadie la importa) y no la corre `make check`: es de mano.
//
//   cd apps/dashboard && npx tsx src/sim2d/__checks__/fidelity.ts > /tmp/ts.json
//
// y el lado Python, desde la raíz del repo:
//
//   uv run python - /tmp/ref.json <<'EOF'
//   import json, sys
//   from pathlib import Path
//   sys.path[:0] = ['packages/sim/src', 'packages/core/src', 'packages/contracts/src']
//   from sim.scenario import load
//   from core.belief import initial_state
//   from core.solver import RoadGraph, cost_matrix, apply_hard_constraints, solve_with_violations
//   from core import tasks as T
//   from contracts.plan import Policy
//   from contracts.world import Cell
//   s = load(Path('scenarios/wildfire_ridge.yaml'))
//   st = initial_state('run_cmp', s)
//   st = st.model_copy(update={'cells': {'cell_13_11': Cell(id='cell_13_11', cx=13, cz=11, state='burning')}})
//   graph = RoadGraph.from_scenario(s)
//   for t in T.sync(st, graph):
//       st = st.model_copy(update={'tasks': {**st.tasks, t.id: t}})
//   policy = Policy(rationale='comparación', weights={'life_safety': 0.9, 'immobile_first': 0.7,
//       'structure_protection': 0.5, 'containment': 0.4, 'response_time': 0.2},
//       hard_constraints=['no_unit_into_burning_cell'])
//   m, units, cols, routes = cost_matrix(st, policy, graph, set(), {}, {})
//   apply_hard_constraints(m, st, policy, graph, units, cols, routes)
//   plan, _ = solve_with_violations(st, policy, graph)
//   json.dump({'units': [u.id for u in units], 'columns': [c.id for c in cols],
//              'matrix': [[('inf' if x == float('inf') else round(x, 6)) for x in r] for r in m],
//              'assignments': sorted([a.unit_id, a.task_id, a.route, round(a.eta_s, 4),
//                                     round(a.cost, 6)] for a in plan.assignments)},
//             open(sys.argv[1], 'w'), indent=1)
//   EOF
//
// Los dos ficheros tienen que salir iguales en `units`, `columns`, `matrix` y
// `assignments`. La Policy se fija a mano a propósito: la de esta página sale de
// reglas y la del core de un LLM, así que lo que se compara es el SOLVER.
import type { Policy, Scenario } from '../../types'
import raw from '../engine/wildfire_ridge.json'
import { RoadGraph } from '../engine/graph'
import { initialState, applyCellChange, setTask } from '../engine/state'
import { syncTasks } from '../engine/tasks'
import { costMatrix, applyHardConstraints, solve } from '../engine/solver'

const scenario = raw as unknown as Scenario
const st = initialState(scenario, 'run_cmp')
applyCellChange(st, { cell_id: 'cell_13_11', state: 'burning', hazard: 'wildfire', cause: 'inject' })
const graph = RoadGraph.fromScenario(scenario)
for (const t of syncTasks(st, graph).changed) setTask(st, t)

const policy: Policy = {
  rationale: 'comparación',
  weights: { life_safety: 0.9, immobile_first: 0.7, structure_protection: 0.5, containment: 0.4, response_time: 0.2 },
  hard_constraints: ['no_unit_into_burning_cell'],
  horizon_s: 600,
  escalate_to_human: false,
  notify: [],
}

const cm = costMatrix(st, policy, graph)
applyHardConstraints(cm, st, policy, graph)
const res = solve(st, policy, graph)

const round = (n: number, d = 6) => Number(n.toFixed(d))
const out = {
  tasks: [...st.tasks.values()]
    .map((t) => [t.id, t.kind, t.severity, t.target_cell, t.target_poi])
    .sort((a, b) => (JSON.stringify(a) < JSON.stringify(b) ? -1 : 1)),
  units: cm.units.map((u) => u.id),
  columns: cm.tasks.map((t) => t.id),
  matrix: cm.matrix.map((r) => r.map((m) => (Number.isFinite(m) ? round(m) : 'inf'))),
  assignments: res.plan.assignments
    .map((a) => [a.unit_id, a.task_id, a.route, round(a.eta_s, 4), round(a.cost)])
    .sort((a, b) => (JSON.stringify(a) < JSON.stringify(b) ? -1 : 1)),
  unassigned: [...res.plan.unassigned_tasks].sort(),
  assumptions: res.plan.context.assumptions
    .map((a) => [a.key, a.expected, a.weight])
    .sort((a, b) => (JSON.stringify(a) < JSON.stringify(b) ? -1 : 1)),
}
// eslint-disable-next-line no-console
console.log(JSON.stringify(out, null, 1))
