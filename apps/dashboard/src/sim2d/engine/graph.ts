// La red de carreteras: Dijkstra sobre los waypoints del escenario.
//
// Puerto de `core/solver.RoadGraph`. El core tiene el suyo propio y NO importa el de
// `sim` a propósito (invariante 4: nadie importa del paquete de otro); aquí pasa lo
// mismo por otra razón, que es que esta página no habla con nadie.
//
// Con diez nodos un montículo binario es más código que ganancia: el Dijkstra recorre
// el array. Si el escenario creciera a cientos, este es el sitio donde cambiarlo.
import type { RoadEdge, Scenario, Waypoint } from '../../types'

import { bearingDeg, cellIdOf, fireEtaS, type Geo } from './geom'

export const DEFAULT_REACH_M = 24.0
export const DEFAULT_BASE_SPREAD = 0.1

interface Arc {
  to: string
  lengthM: number
  edgeId: string
}

export class RoadGraph {
  readonly coords: Map<string, [number, number]>
  readonly geo: Geo
  readonly reachM: number
  readonly baseSpread: number
  private readonly adj: Map<string, Arc[]>

  constructor(
    waypoints: readonly Waypoint[],
    roads: readonly RoadEdge[],
    geo: Geo,
    reachM: number,
    baseSpread: number,
  ) {
    this.geo = geo
    this.reachM = reachM
    this.baseSpread = baseSpread
    this.coords = new Map(waypoints.map((w) => [w.id, [w.x, w.z] as [number, number]]))
    this.adj = new Map(waypoints.map((w) => [w.id, [] as Arc[]]))
    for (const road of roads) {
      // Simétrica: una carretera se recorre en los dos sentidos aunque su id tenga
      // una dirección (`road:wp_a-wp_b`).
      this.adj.get(road.a)?.push({ to: road.b, lengthM: road.length_m, edgeId: road.id })
      this.adj.get(road.b)?.push({ to: road.a, lengthM: road.length_m, edgeId: road.id })
    }
  }

  static fromScenario(scenario: Scenario): RoadGraph {
    return new RoadGraph(
      scenario.waypoints,
      scenario.roads,
      { origin: scenario.origin, cellSize: scenario.hazard.cell_size },
      scenario.hazard.suppress_reach_m || DEFAULT_REACH_M,
      scenario.hazard.base_spread || DEFAULT_BASE_SPREAD,
    )
  }

  /** El mismo grafo sin las aristas cortadas. Se recalcula por plan, no por tick. */
  withCuts(cut: ReadonlySet<string>): RoadGraph {
    if (cut.size === 0) return this
    const copy = Object.create(RoadGraph.prototype) as RoadGraph
    const adj = new Map<string, Arc[]>()
    for (const [node, arcs] of this.adj) adj.set(node, arcs.filter((a) => !cut.has(a.edgeId)))
    return Object.assign(copy, {
      coords: this.coords,
      geo: this.geo,
      reachM: this.reachM,
      baseSpread: this.baseSpread,
      adj,
    })
  }

  get waypointIds(): string[] {
    return [...this.coords.keys()]
  }

  at(id: string): [number, number] | null {
    return this.coords.get(id) ?? null
  }

  /** El camino más corto en metros, incluidos los dos extremos. `null` si no hay. */
  shortestPath(from: string, to: string): string[] | null {
    if (!this.coords.has(from) || !this.coords.has(to)) return null
    if (from === to) return [from]
    const dist = new Map<string, number>([[from, 0]])
    const prev = new Map<string, string>()
    const seen = new Set<string>()
    for (;;) {
      let node: string | null = null
      let best = Infinity
      for (const [id, d] of dist) {
        if (!seen.has(id) && d < best) {
          best = d
          node = id
        }
      }
      if (node === null) return null
      if (node === to) break
      seen.add(node)
      for (const arc of this.adj.get(node) ?? []) {
        if (seen.has(arc.to)) continue
        const next = best + arc.lengthM
        if (next < (dist.get(arc.to) ?? Infinity)) {
          dist.set(arc.to, next)
          prev.set(arc.to, node)
        }
      }
    }
    const route: string[] = [to]
    let cursor = to
    while (cursor !== from) {
      const before = prev.get(cursor)
      if (before === undefined) return null
      route.push(before)
      cursor = before
    }
    return route.reverse()
  }

  /** Los metros de una ruta ya resuelta, por sus aristas. */
  routeLengthM(route: readonly string[]): number {
    let total = 0
    for (let i = 0; i + 1 < route.length; i++) {
      const a = route[i]
      const b = route[i + 1]
      if (a === undefined || b === undefined) continue
      const arc = (this.adj.get(a) ?? []).find((x) => x.to === b)
      // Sin arista (ruta de un plan viejo sobre un grafo ya cortado) se cae a la
      // distancia en línea recta: un número grande y honesto, no un cero.
      total += arc ? arc.lengthM : this.straightLine(a, b)
    }
    return total
  }

  /** Los ids de arista que recorre una ruta. Es lo que se convierte en suposiciones. */
  routeEdges(route: readonly string[]): string[] {
    const out: string[] = []
    for (let i = 0; i + 1 < route.length; i++) {
      const a = route[i]
      const b = route[i + 1]
      if (a === undefined || b === undefined) continue
      const arc = (this.adj.get(a) ?? []).find((x) => x.to === b)
      if (arc) out.push(arc.edgeId)
    }
    return out
  }

  straightLine(a: string, b: string): number {
    const pa = this.coords.get(a)
    const pb = this.coords.get(b)
    if (!pa || !pb) return 0
    return Math.hypot(pb[0] - pa[0], pb[1] - pa[1])
  }

  nearestWaypoint(x: number, z: number): string | null {
    let best: string | null = null
    let bestD = Infinity
    // Orden estable por id: con dos waypoints a la misma distancia, el plan no puede
    // depender del orden de iteración del Map.
    for (const id of [...this.coords.keys()].sort()) {
      const p = this.coords.get(id)
      if (!p) continue
      const d = Math.hypot(p[0] - x, p[1] - z)
      if (d < bestD) {
        bestD = d
        best = id
      }
    }
    return best
  }

  /** El waypoint desde el que se puede atacar un punto: a menos de `reachM`. */
  attackableFrom(x: number, z: number): string | null {
    let best: string | null = null
    let bestD = this.reachM
    for (const id of [...this.coords.keys()].sort()) {
      const p = this.coords.get(id)
      if (!p) continue
      const d = Math.hypot(p[0] - x, p[1] - z)
      if (d <= bestD) {
        bestD = d
        best = id
      }
    }
    return best
  }

  /** El waypoint al que el fuego llega antes: donde tiene sentido esperarlo. */
  interceptWaypoint(
    x: number,
    z: number,
    wind: { bearing_deg: number; speed: number },
  ): string | null {
    let best: string | null = null
    let bestEta = Infinity
    for (const id of [...this.coords.keys()].sort()) {
      const p = this.coords.get(id)
      if (!p) continue
      const eta = fireEtaS(x, z, p[0], p[1], wind, this.geo.cellSize, this.baseSpread)
      if (eta < bestEta) {
        bestEta = eta
        best = id
      }
    }
    return best
  }

  /** A dónde se manda un camión por un frente: a tiro si se puede, a interceptar si no. */
  attackWaypoint(
    x: number,
    z: number,
    wind: { bearing_deg: number; speed: number },
  ): string | null {
    return this.attackableFrom(x, z) ?? this.interceptWaypoint(x, z, wind)
  }

  cellOf(x: number, z: number): string {
    return cellIdOf(x, z, this.geo)
  }

  bearingTo(from: string, x: number, z: number): number {
    const p = this.coords.get(from)
    if (!p) return 0
    return bearingDeg(p[0], p[1], x, z)
  }
}
