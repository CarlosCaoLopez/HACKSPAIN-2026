// El solver no lo compromete todo: guarda medios por si entra otra emergencia.
//
// Existe porque no lo hacía. Medido antes del arreglo: **2/2 camiones y 4/4
// ambulancias**, siempre que hubiera trabajo. Y `reserve_capability` era una
// restricción fantasma —declarada en el contrato, ofrecida al LLM en el prompt,
// emitida por la Policy, explicada en el panel— que no aplicaba nadie.
//
// Se comprueban las dos mitades, porque una reserva mal hecha falla por los dos lados:
//
//   · **que se guarde**: mientras no haya más tareas críticas que medios, tiene que
//     quedar al menos una unidad libre de cada capacidad con más de una unidad;
//   · **que ceda**: si la reserva se suelta, tiene que haber de verdad más tareas
//     críticas que medios. Una reserva que se suelta sin motivo es tan inútil como una
//     que no se suelta nunca y deja un pueblo ardiendo.
//
//   cd apps/dashboard && npx tsx src/sim2d/__checks__/reserve.ts
//
// Sale con código distinto de cero si alguna de las dos falla.
import type { Scenario, Task } from '../../types'
import raw from '../engine/wildfire_ridge.json'
import { SimEngine, TICK_S } from '../engine/loop'

const scenario = raw as unknown as Scenario
const TICKS = 360
/** Las mismas de `policy.RESERVABLE`. El dron queda fuera: hay uno solo. */
const CAPS = ['extinguish', 'transport'] as const

const mmss = (t: number) =>
  `${String(Math.floor(t / 60)).padStart(2, '0')}:${String(Math.floor(t % 60)).padStart(2, '0')}`

const overCommitted: string[] = []
const releasedWithoutCause: string[] = []
const unexplained: string[] = []
let heldTicks = 0
let releasedTicks = 0

const eng = new SimEngine(scenario)
eng.ignite(scenario.hazard.origin_cell)

for (let i = 0; i < TICKS; i++) {
  eng.tick(TICK_S)
  const f = eng.frame()
  if (!f.plan) continue

  const assigned = new Set(f.plan.assignments.map((a) => a.unit_id))
  const open = [...f.state.tasks.values()].filter((t: Task) => !t.done)

  for (const cap of CAPS) {
    const active = [...f.state.units.values()].filter(
      (u) => u.status !== 'unavailable' && u.capabilities.includes(cap),
    )
    // Con un solo medio no hay reserva posible: guardarlo es no cubrir nada.
    if (active.length <= 1) continue
    const critical = open.filter((t) => t.severity === 'critical' && t.required_capability === cap)
    // Sin nada que hacer, que estén todos libres no dice nada de la reserva.
    if (!open.some((t) => t.required_capability === cap)) continue

    const free = active.filter((u) => !assigned.has(u.id)).length
    const shouldHold = critical.length <= active.length - 1

    if (shouldHold && free === 0) {
      overCommitted.push(
        `  ${mmss(f.tSim)}  ${cap}: ${active.length} medios activos y 0 libres, con ${critical.length} tareas críticas — se podía guardar uno`,
      )
    }
    if (shouldHold) heldTicks++
    else releasedTicks++

    const declared = f.reserves.some((r) => r.capability === cap)
    if (!shouldHold && declared) {
      releasedWithoutCause.push(
        `  ${mmss(f.tSim)}  ${cap}: se declara reserva con ${critical.length} críticas y solo ${active.length} medios`,
      )
    }
  }

  // Toda tarea sin cubrir por reserva tiene que decirlo con su motivo, o en pantalla
  // se lee como un fallo del solver — que es exactamente lo que parecía.
  for (const taskId of f.plan.unassigned_tasks) {
    const why = f.infeasible.filter((x) => x.taskId === taskId)
    if (why.length === 0) unexplained.push(`  ${mmss(f.tSim)}  ${taskId} sin cubrir y sin motivo`)
  }
}

// --- segunda fase: forzar que la reserva CEDA ---
//
// En `wildfire_ridge` la rama de ceder no se alcanza sola: nunca hay más de tres
// tareas críticas de transporte a la vez para cuatro ambulancias, así que la reserva
// siempre se sostiene. Una rama que no se ejerce es exactamente el pecado que este
// arreglo vino a corregir —una regla escrita que no actúa nunca—, así que se
// construye el caso: cuatro rescates simultáneos, uno por POI.
const forced = new SimEngine(scenario)
forced.ignite(scenario.hazard.origin_cell)
for (let i = 0; i < 40; i++) forced.tick(TICK_S)
for (const poi of ['poi_pueblo_a', 'poi_pueblo_b', 'poi_molino', 'poi_refugio']) {
  forced.assert({
    key: `poi:${poi}:immobile`,
    value: 2,
    confidence: 0.9,
    source: 'call:check',
    severity: 'critical',
    t_sim: forced.frame().tSim,
    kind: 'observed',
    call_id: null,
  })
}
for (let i = 0; i < 8; i++) forced.tick(TICK_S)

const ff = forced.frame()
const criticalTransport = [...ff.state.tasks.values()].filter(
  (t: Task) => !t.done && t.severity === 'critical' && t.required_capability === 'transport',
).length
const ambulances = [...ff.state.units.values()].filter(
  (u) => u.status !== 'unavailable' && u.capabilities.includes('transport'),
)
const releasedTransport = (ff.policy?.released ?? []).some((r) => r.capability === 'transport')
const heldExtinguish = ff.reserves.some((r) => r.capability === 'extinguish')
const assignedAll =
  ambulances.filter((u) => (ff.plan?.assignments ?? []).some((a) => a.unit_id === u.id)).length ===
  ambulances.length

console.log(`
con ${criticalTransport} tareas críticas de transporte y ${ambulances.length} ambulancias:`)
console.log(`  suelta la reserva de transporte: ${releasedTransport ? 'sí' : 'NO'}`)
console.log(`  salen todas las ambulancias:     ${assignedAll ? 'sí' : 'NO'}`)
console.log(`  mantiene la de extinción:        ${heldExtinguish ? 'sí' : 'NO'}`)

const cedes = criticalTransport > ambulances.length - 1 && releasedTransport && assignedAll && heldExtinguish

console.log(`
ticks con reserva guardada: ${heldTicks}   ticks con reserva soltada: ${releasedTicks}`)
console.log(`\ncompromete todo pudiendo guardar: ${overCommitted.length}`)
for (const x of overCommitted.slice(0, 10)) console.log(x)
console.log(`\nreserva declarada sin poder sostenerla: ${releasedWithoutCause.length}`)
for (const x of releasedWithoutCause.slice(0, 10)) console.log(x)
console.log(`\ntareas sin cubrir y sin motivo: ${unexplained.length}`)
for (const x of unexplained.slice(0, 5)) console.log(x)

const ok =
  overCommitted.length === 0 &&
  releasedWithoutCause.length === 0 &&
  unexplained.length === 0 &&
  heldTicks > 0 &&
  cedes
console.log(ok ? '\nOK: el solver guarda reserva y la suelta solo cuando toca' : '\nFALLO')
if (!ok) throw new Error('la reserva no se comporta como debe')
