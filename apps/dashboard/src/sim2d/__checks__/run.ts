// Seis minutos de simulación por consola: la bitácora, las llamadas, las órdenes y el
// plan final. Sirve para ver de un vistazo que la historia sale —que las llamadas de
// despacho salen ANTES que los `goto`, que el corte de carretera dispara un REPLAN por
// restricción dura y la avería otro por hecho crítico— sin abrir el navegador.
//
// No entra en el bundle. Se corre de mano:
//
//   cd apps/dashboard && npx tsx src/sim2d/__checks__/run.ts

import type { Scenario } from '../../types'
import raw from '../engine/wildfire_ridge.json'
import { SimEngine } from '../engine/loop'

const eng = new SimEngine(raw as unknown as Scenario)
eng.ignite(eng.scenario.hazard.origin_cell)
for (let i = 0; i < 360; i++) eng.tick(1)

const f = eng.frame()
const mmss = (t: number) => `${String(Math.floor(t / 60)).padStart(2, '0')}:${String(Math.floor(t % 60)).padStart(2, '0')}`
console.log('=== BITÁCORA ===')
for (const e of f.log) console.log(` ${mmss(e.tSim)} [${e.kind}] ${e.text}`)
console.log('\n=== LLAMADAS ===')
for (const c of f.calls) {
  console.log(` ${mmss(c.startedT)} ${c.role} → ${c.callee}  [${c.phase}/${c.outcome ?? '—'}]  retiene: ${c.heldUnits.join(', ') || 'nadie'}`)
  console.log(`      por qué: ${c.why}`)
  for (const l of c.said.slice(0, 3)) console.log(`      ${l.speaker}: ${l.text.slice(0, 150)}`)
}
console.log('\n=== ÓRDENES goto ===')
for (const a of f.actions) console.log(` ${mmss(a.tSim)} ${a.unitId} → ${a.route[a.route.length - 1]} confirmado=${a.dispatchConfirmed}`)
console.log('\n=== PLAN FINAL ===')
console.log(' policy:', JSON.stringify(f.plan?.policy.weights), f.plan?.policy.hard_constraints)
console.log(' rationale:', f.plan?.policy.rationale)
for (const a of f.plan?.assignments ?? []) console.log(`  ${a.unit_id} → ${a.task_id} eta=${a.eta_s.toFixed(0)}s coste=${a.cost.toFixed(2)}`)
console.log(' sin cubrir:', f.plan?.unassigned_tasks.length)
console.log(' divergencia:', f.divergence?.value.toFixed(3), 'roto:', f.divergence?.broken)
let burning = 0, burnt = 0
for (const c of f.state.cells.values()) { if (c.state === 'burning') burning++; if (c.state === 'burnt') burnt++ }
console.log(` celdas: ${burning} ardiendo, ${burnt} quemadas`)
let safe = 0, exposed = 0
for (const g of f.state.civilians.values()) { if (g.state === 'safe') safe += g.count; else exposed += g.count }
console.log(` civiles: ${safe} a salvo, ${exposed} expuestos`)
