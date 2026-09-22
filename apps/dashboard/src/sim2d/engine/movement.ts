// El movimiento de las unidades. Puerto de `sim/movement.py`.
//
// Se interpola en línea recta entre waypoints a `UNIT_SPEED_MPS`, que es el MISMO 4.0
// que usa el solver para su `eta_s`. Si los dos números divergen, el minuto que la
// llamada promete por teléfono no es el que se ve andar por el mapa.
//
// Ojo a una diferencia que el backend también tiene: el coste de ruta usa
// `RoadEdge.length_m` (lo que dice el YAML) y el movimiento usa la distancia
// euclídea entre waypoints. En `wildfire_ridge` son parecidas a propósito, pero no
// idénticas.
//
// Un recorrido se guarda como una lista de PUNTOS, no de waypoints. El primero puede
// ser la posición real de la unidad al arrancar, que no tiene por qué caer sobre un
// waypoint: en el YAML están aparcadas unos metros al lado para no solaparse en
// Minecraft, y una unidad reencaminada a mitad de camino arranca donde la pille la
// noticia. Tuve esto como un `origin` que SUSTITUÍA al primer waypoint, y estaba mal
// en el caso que importa: al dar media vuelta por una carretera cortada, saltarse el
// waypoint de vuelta significa irse en línea recta campo a través en lugar de
// retroceder por la calzada. Prependiendo el punto de partida, la unidad siempre
// conduce hasta el primer waypoint de su ruta y desde ahí sigue la carretera.
import { bearingDeg } from './geom'
import type { RoadGraph } from './graph'
import { setUnit, type SimState } from './state'
import { UNIT_SPEED_MPS } from './solver'

/** El mismo 3.0 que `core.loop.AT_WAYPOINT_M`: estar «en» un waypoint. Lo usa
 *  `loop.returnHome` para no mandar a casa a quien ya está en casa. */
export const AT_WAYPOINT_M = 3.0

interface Motion {
  /** El waypoint de cada punto, o `null` para el punto de partida real. */
  nodes: Array<string | null>
  /** Por dónde pasa, en orden. `points[i] → points[i+1]` es el tramo `i`. */
  points: Array<[number, number]>
  /** Tramo que se está recorriendo. */
  leg: number
  /** Metros recorridos del tramo actual. */
  done: number
  taskId: string
}

export interface Arrival {
  unitId: string
  waypointId: string
  taskId: string
}

export class Movement {
  private readonly motions = new Map<string, Motion>()

  constructor(private readonly graph: RoadGraph) {}

  /** `from` es dónde está la unidad ahora. Se antepone como punto de partida salvo
   *  que ya esté prácticamente encima del primer waypoint. */
  start(unitId: string, route: readonly string[], taskId: string, from?: [number, number]): void {
    const nodes: Array<string | null> = []
    const points: Array<[number, number]> = []

    const head = route[0] !== undefined ? this.graph.at(route[0]) : null
    if (from && (!head || Math.hypot(from[0] - head[0], from[1] - head[1]) > 0.5)) {
      nodes.push(null)
      points.push([from[0], from[1]])
    }
    for (const id of route) {
      const p = this.graph.at(id)
      if (!p) continue
      nodes.push(id)
      points.push([p[0], p[1]])
    }

    if (points.length < 2) {
      this.motions.delete(unitId)
      return
    }
    this.motions.set(unitId, { nodes, points, leg: 0, done: 0, taskId })
  }

  stop(unitId: string): void {
    this.motions.delete(unitId)
  }

  isMoving(unitId: string): boolean {
    return this.motions.has(unitId)
  }

  taskOf(unitId: string): string | null {
    return this.motions.get(unitId)?.taskId ?? null
  }

  destinationOf(unitId: string): string | null {
    const m = this.motions.get(unitId)
    if (!m) return null
    for (let i = m.nodes.length - 1; i >= 0; i--) {
      const id = m.nodes[i]
      if (id) return id
    }
    return null
  }

  /** Los dos waypoints del tramo que se está recorriendo, si los dos lo son.
   *
   *  Es lo que permite saber que una unidad está **dentro** de la carretera que
   *  acaban de cortar, y por cuál de sus dos extremos entró. */
  currentLeg(unitId: string): [string, string] | null {
    const m = this.motions.get(unitId)
    if (!m) return null
    const a = m.nodes[m.leg]
    const b = m.nodes[m.leg + 1]
    return a && b ? [a, b] : null
  }

  /** Lo que le queda por recorrer, en waypoints. Es contra esto contra lo que hay que
   *  comparar una ruta nueva para saber si de verdad cambia algo: comparar con la
   *  ruta ORDENADA da falsos negativos en cuanto la unidad avanza un tramo, y
   *  entonces se reemite el mismo `goto` cada pocos segundos. */
  remainingRoute(unitId: string): string[] | null {
    const m = this.motions.get(unitId)
    if (!m) return null
    const out: string[] = []
    for (let i = m.leg; i < m.nodes.length; i++) {
      const id = m.nodes[i]
      if (id) out.push(id)
    }
    return out
  }

  step(state: SimState, dt: number): Arrival[] {
    const arrivals: Arrival[] = []
    for (const [unitId, m] of [...this.motions.entries()].sort((a, b) => (a[0] < b[0] ? -1 : 1))) {
      const unit = state.units.get(unitId)
      if (!unit || unit.status === 'unavailable') {
        this.motions.delete(unitId)
        continue
      }
      let budget = dt * UNIT_SPEED_MPS
      while (budget > 0) {
        const a = m.points[m.leg]
        const b = m.points[m.leg + 1]
        if (!a || !b) break
        const legLen = Math.hypot(b[0] - a[0], b[1] - a[1])
        const left = legLen - m.done
        if (budget < left) {
          m.done += budget
          budget = 0
        } else {
          budget -= left
          m.leg++
          m.done = 0
          const reached = m.nodes[m.leg]
          if (reached) arrivals.push({ unitId, waypointId: reached, taskId: m.taskId })
          if (m.leg + 1 >= m.points.length) {
            this.motions.delete(unitId)
            setUnit(state, unitId, { x: b[0], z: b[1], status: 'idle', task_id: m.taskId })
            budget = 0
          }
        }
      }
      const cur = this.motions.get(unitId)
      if (!cur) continue
      const a = cur.points[cur.leg]
      const b = cur.points[cur.leg + 1]
      if (!a || !b) continue
      const legLen = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1
      const f = Math.min(1, cur.done / legLen)
      setUnit(state, unitId, {
        x: a[0] + (b[0] - a[0]) * f,
        z: a[1] + (b[1] - a[1]) * f,
        status: 'moving',
        task_id: cur.taskId,
      })
    }
    return arrivals
  }

  /** El rumbo al que apunta la unidad, para girar su triángulo en el mapa. */
  headingOf(unitId: string): number {
    const m = this.motions.get(unitId)
    if (!m) return 0
    const a = m.points[m.leg]
    const b = m.points[m.leg + 1]
    if (!a || !b) return 0
    return bearingDeg(a[0], a[1], b[0], b[1])
  }
}
