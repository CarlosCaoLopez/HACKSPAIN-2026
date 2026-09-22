// Cortar una carretera tiene que significar algo: nadie la atraviesa después.
//
// Existe porque no lo significaba. El plan y el solver siempre estuvieron bien —tras
// el corte salta el REPLAN y las rutas rodean por el desvío norte—, pero una unidad
// EN MARCHA seguía un recorrido calculado antes, y nadie lo revisaba. El fallo no se
// ve en un test de reparto: las asignaciones eran correctas. Solo se ve mirando por
// dónde pasan las unidades, así que eso es lo que se mide aquí.
//
// **Qué cuenta como atravesar**, que costó tres intentos y conviene dejarlo escrito:
//
//   · Contar los ticks que pasa sobre el tramo NO vale. A una unidad a la que el
//     corte pilla dentro no le queda otra que recorrerlo para salir, y eso no es
//     ignorar el corte: es obedecerlo. Con esa métrica el retroceso legítimo salía
//     marcado como fallo.
//   · Mirar el mínimo y el máximo de la ventana TAMPOCO. Quien rodea por el desvío
//     norte toca `wp_sur_01` al principio y `wp_sur_02` al final, en momentos
//     distintos y sin pisar el tramo; salía marcado igual.
//   · Lo que de verdad distingue las dos cosas es **por qué extremo sale comparado
//     con por cuál entró**. Y el extremo de entrada es casi siempre anterior al
//     corte, así que hay que seguir a la unidad desde el principio del run.
//
//   cd apps/dashboard && npx tsx src/sim2d/__checks__/roadcut.ts
//
// Sale con código distinto de cero si alguien atraviesa.
import type { Scenario } from '../../types'
import raw from '../engine/wildfire_ridge.json'
import { SimEngine, TICK_S } from '../engine/loop'

const scenario = raw as unknown as Scenario
const CUT = 'road:wp_sur_01-wp_sur_02'
const CAUSE = 'árbol caído'
/** Cada cuánto se prueba a cortar, y cuánto se observa después. */
const EVERY_S = 20
const UNTIL_S = 300
const WATCH_S = 150
/** Cuánto se puede separar de la calzada y seguir contando como «sobre el tramo». */
const CORRIDOR_M = 6
/** Cerca de un extremo: por debajo de esto se considera que está EN el extremo. */
const NEAR_END = 0.2

const edge = scenario.roads.find((r) => r.id === CUT)
const wa = scenario.waypoints.find((w) => w.id === edge?.a)
const wb = scenario.waypoints.find((w) => w.id === edge?.b)
if (!edge || !wa || !wb) throw new Error(`no encuentro ${CUT} en el escenario`)

const ax = wa.x
const az = wa.z
const dx = wb.x - ax
const dz = wb.z - az
const L2 = dx * dx + dz * dz

/** Posición a lo largo del tramo: 0 en un extremo, 1 en el otro. `null` si va lejos. */
function along(x: number, z: number): number | null {
  const t = ((x - ax) * dx + (z - az) * dz) / L2
  const d = Math.hypot(x - (ax + t * dx), z - (az + t * dz))
  return d <= CORRIDOR_M ? t : null
}

const side = (t: number): 'A' | 'B' | null => (t < NEAR_END ? 'A' : t > 1 - NEAR_END ? 'B' : null)

const mmss = (t: number) =>
  `${String(Math.floor(t / 60)).padStart(2, '0')}:${String(Math.floor(t % 60)).padStart(2, '0')}`

const crossings: string[] = []
const badRoutes: string[] = []

for (let cutAt = EVERY_S; cutAt <= UNTIL_S; cutAt += EVERY_S) {
  const eng = new SimEngine(scenario)
  eng.ignite(scenario.hazard.origin_cell)

  /** Por qué extremo entró cada unidad al corredor, y dónde se la vio por última vez. */
  const entry = new Map<string, 'A' | 'B'>()
  const last = new Map<string, 'A' | 'B'>()
  const inside = new Set<string>()
  const flagged = new Set<string>()
  let cut = false
  let ordersBefore = 0

  const sample = () => {
    for (const u of eng.frame().state.units.values()) {
      const t = along(u.x, u.z)
      if (t === null || t < -0.1 || t > 1.1) {
        // Ha salido del corredor. Si sale por el extremo contrario al de entrada, lo
        // ha atravesado.
        const from = entry.get(u.id)
        const to = last.get(u.id)
        if (cut && from && to && from !== to && !flagged.has(u.id)) {
          flagged.add(u.id)
          crossings.push(
            `  corte ${mmss(cutAt)}  ${u.id} entra por ${from} y sale por ${to}: atraviesa el tramo cortado`,
          )
        }
        entry.delete(u.id)
        last.delete(u.id)
        inside.delete(u.id)
        continue
      }
      const sd = side(t)
      inside.add(u.id)
      // El extremo de entrada se fija la primera vez que se la ve en uno de ellos.
      if (sd && !entry.has(u.id)) entry.set(u.id, sd)
      if (sd) last.set(u.id, sd)
    }
  }

  // Se sigue a las unidades DESDE EL PRINCIPIO: el extremo por el que una unidad se
  // metió en el tramo es casi siempre anterior al corte.
  for (let t = 0; t < cutAt + WATCH_S; t++) {
    if (t === cutAt) {
      ordersBefore = eng.frame().actions.length
      eng.applyInject('road_cut', { edge: CUT, cause: CAUSE })
      cut = true
    }
    eng.tick(TICK_S)
    sample()
  }

  // De decisión: ninguna ruta ordenada después del corte usa la arista cortada.
  for (const order of eng.frame().actions.slice(ordersBefore)) {
    for (let i = 0; i + 1 < order.route.length; i++) {
      const p = order.route[i]
      const q = order.route[i + 1]
      if ((p === edge.a && q === edge.b) || (p === edge.b && q === edge.a)) {
        badRoutes.push(
          `  corte ${mmss(cutAt)}  ${order.unitId} recibe una ruta que usa ${CUT}: ${order.route.join(' → ')}`,
        )
      }
    }
  }
}

console.log(`cortes probados: ${Math.floor(UNTIL_S / EVERY_S)}  (cada ${EVERY_S} s, observando ${WATCH_S} s)`)
console.log(`\nunidades que atraviesan el tramo cortado: ${crossings.length}`)
for (const c of crossings) console.log(c)
console.log(`\nrutas ordenadas que usan la arista cortada: ${badRoutes.length}`)
for (const r of badRoutes) console.log(r)

const ok = crossings.length === 0 && badRoutes.length === 0
console.log(ok ? '\nOK: cortar la carretera significa algo' : '\nFALLO')
if (!ok) throw new Error('alguien ignora el corte de carretera')
