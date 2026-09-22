// El invariante del movimiento: **nadie se mueve más rápido de lo que puede conducir**.
//
// Existe porque se rompió. `syncMotions` recorría el histórico entero de órdenes en
// cada tick, y en cuanto una unidad terminaba su ruta cualquier orden vieja se
// relanzaba desde su primer waypoint: el camión llegaba al frente y reaparecía en el
// parque, la ambulancia llegaba a Pueblo A y reaparecía en el hospital, en bucle cada
// treinta y cuatro segundos. Treinta y siete saltos por run, de hasta 185 m.
//
// Es un fallo que vuelve en cuanto alguien toque el bucle, y que no se ve en un test
// de reparto: el plan era correcto, lo que mentía era el mapa. Así que la comprobación
// vale más que el arreglo.
//
//   cd apps/dashboard && npx tsx src/sim2d/__checks__/teleport.ts
//
// Sale con código 1 si alguna unidad se teletransporta.
import type { Scenario } from '../../types'
import raw from '../engine/wildfire_ridge.json'
import { SimEngine, TICK_S } from '../engine/loop'
import { UNIT_SPEED_MPS } from '../engine/solver'

const TICKS = 360
/** Lo máximo que se puede recorrer en un tick, más un pelo por el coma flotante. */
const MAX_STEP_M = UNIT_SPEED_MPS * TICK_S + 0.01

const mmss = (t: number) =>
  `${String(Math.floor(t / 60)).padStart(2, '0')}:${String(Math.floor(t % 60)).padStart(2, '0')}`

const scenario = raw as unknown as Scenario
const eng = new SimEngine(scenario)
eng.ignite(scenario.hazard.origin_cell)

const prev = new Map<string, [number, number]>()
for (const u of eng.frame().state.units.values()) prev.set(u.id, [u.x, u.z])

const jumps: string[] = []
for (let i = 0; i < TICKS; i++) {
  eng.tick(TICK_S)
  const f = eng.frame()
  for (const u of f.state.units.values()) {
    const p = prev.get(u.id)
    if (p) {
      const d = Math.hypot(u.x - p[0], u.z - p[1])
      if (d > MAX_STEP_M) {
        jumps.push(
          `${mmss(f.tSim)}  ${u.id} salta ${d.toFixed(1)} m` +
            `  (${p[0].toFixed(0)},${p[1].toFixed(0)}) → (${u.x.toFixed(0)},${u.z.toFixed(0)})`,
        )
      }
    }
    prev.set(u.id, [u.x, u.z])
  }
}

const frame = eng.frame()

// Las vueltas a casa: tienen que ser rutas de verdad y ordenarse una sola vez.
const returns = frame.actions.filter((a) => a.taskId === 'return_to_base')
const badRoute = returns.filter((a) => a.route.length < 2)
// Volver a casa dos veces en un run es legítimo: se sale a un pueblo y se vuelve. Lo
// que no vale es ordenarlo DOS VECES SEGUIDAS sin un viaje de por medio, que es lo que
// pasaría si el guardia de «una sola vez» no funcionara.
const repeated = frame.actions.filter((a, i) => {
  if (a.taskId !== 'return_to_base') return false
  const before = frame.actions.slice(0, i).reverse().find((b) => b.unitId === a.unitId)
  return before !== undefined && before.taskId === 'return_to_base'
})

console.log(`saltos imposibles: ${jumps.length}  (máximo legítimo ${MAX_STEP_M.toFixed(2)} m por tick)`)
for (const j of jumps.slice(0, 15)) console.log(`  ${j}`)
if (jumps.length > 15) console.log(`  … y ${jumps.length - 15} más`)

console.log(`\nvueltas a base: ${returns.length}`)
for (const a of returns) {
  console.log(`  ${mmss(a.tSim)}  ${a.unitId} → ${a.route[a.route.length - 1]} por ${a.route.join(' → ')}`)
}
if (badRoute.length > 0) console.log(`  ✗ ${badRoute.length} sin ruta de verdad (menos de 2 waypoints)`)
if (repeated.length > 0) console.log(`  ✗ ${repeated.length} repetidas para la misma unidad y destino`)

let safe = 0
for (const g of frame.state.civilians.values()) if (g.state === 'safe') safe += g.count
console.log(`\ncivicos a salvo al final: ${safe}`)

const ok = jumps.length === 0 && badRoute.length === 0 && repeated.length === 0
console.log(ok ? '\nOK: nadie se teletransporta' : '\nFALLO')
// `throw` en vez de `process.exit`: sale con código 1 igual y no hace falta @types/node
// para una comprobación que se corre a mano.
if (!ok) throw new Error('el invariante del movimiento no se cumple')
