// El autómata del fuego. Puerto de `packages/sim/src/sim/hazard.py` (`Wildfire`).
//
// Una celda ardiendo intenta propagarse a cada vecina con una tasa, en celdas por
// minuto, de `(base_spread + viento · max(0, cos θ)) · fuel / distancia`, donde θ es
// el ángulo entre la dirección a la vecina y hacia donde EMPUJA el viento. La tasa se
// convierte a probabilidad en el intervalo con `1 − e^(−tasa·dt)`.
//
// Dos cosas se conservan del original porque sin ellas no es el mismo incendio:
//   · el orden de recorrido es **por id ordenado**, no el de inserción del Map. Con el
//     RNG bien sembrado pero el orden al azar, el determinismo se pierde igual.
//   · el `fuel` es 1 dentro de la caja quemable y 0 fuera. Es el freno de verdad del
//     incendio; `MAX_RADIUS_CELLS` es solo el cinturón de seguridad.
//
// Y dos cosas se AÑADEN, que son de simulación y no existen en el backend: `ignite` a
// mano (el usuario clica un foco) y `douse` (lo quita). El resto es el mismo modelo.
import type { CellState, Wind } from '../../types'

import { cellId, parseCell, windVector } from './geom'
import type { Rng } from './rng'

export const BURN_DURATION_S = 45.0
export const AT_RISK_P = 0.5
export const MAX_RADIUS_CELLS = 40
export const SECONDS_PER_MINUTE = 60.0

const IGNITABLE: ReadonlySet<CellState> = new Set<CellState>(['intact', 'at_risk'])

const NEIGHBOURS_8: ReadonlyArray<[number, number]> = [
  [-1, -1], [-1, 0], [-1, 1],
  [0, -1], [0, 1],
  [1, -1], [1, 0], [1, 1],
]

export type Cause = 'spread' | 'burnout' | 'extinguished' | 'inject' | 'at_risk'

export interface CellChange {
  cell_id: string
  state: CellState
  hazard: string
  cause: Cause
}

export interface HazardConfig {
  kind: string
  originCell: string
  cellSize: number
  baseSpread: number
  suppressReachM: number
  suppressRate: number
  /** (x1, z1, x2, z2) de lo que puede arder. Fuera no hay nada que quemar. */
  burnable: [number, number, number, number] | null
}

function probability(rate: number, seconds: number): number {
  return 1 - Math.exp(-rate * seconds)
}

export class Wildfire {
  wind: Wind
  private readonly cfg: HazardConfig
  private readonly rng: Rng
  private readonly origin: [number, number]
  private readonly state = new Map<string, CellState>()
  private readonly activeFor = new Map<string, number>()
  private readonly suppressed = new Map<string, number>()
  private pending: CellChange[] = []

  constructor(cfg: HazardConfig, wind: Wind, rng: Rng) {
    this.cfg = cfg
    this.wind = { ...wind }
    this.rng = rng
    this.origin = parseCell(cfg.originCell) ?? [0, 0]
  }

  // --- la interfaz ---

  tick(dt: number): CellChange[] {
    const changes = this.pending
    this.pending = []

    // Primero el consumo: una celda que se apaga este tick ya no propaga.
    for (const cid of [...this.activeFor.keys()].sort()) {
      const t = (this.activeFor.get(cid) ?? 0) + dt
      if (t >= BURN_DURATION_S) {
        this.state.set(cid, 'burnt')
        this.activeFor.delete(cid)
        this.suppressed.delete(cid)
        changes.push(this.change(cid, 'burnt', 'burnout'))
      } else {
        this.activeFor.set(cid, t)
      }
    }

    for (const cid of [...this.activeFor.keys()].sort()) {
      for (const [neighbour, rate] of this.spreadFrom(cid)) {
        if (this.rng.next() < probability(rate, dt)) changes.push(this.activate(neighbour, 'spread'))
      }
    }

    changes.push(...this.markAtRisk())
    return changes
  }

  /** Sofocar desde la carretera: física del mundo, no un verbo.
   *
   *  Cada unidad trabaja las celdas `burning` a menos de `suppress_reach_m`, a
   *  `suppress_rate` celdas por minuto **repartidas entre las que tenga a tiro**: un
   *  camión no apaga más rápido por tener más fuego delante, lo reparte. */
  suppress(positions: ReadonlyArray<[number, number]>, dt: number): CellChange[] {
    if (positions.length === 0 || this.activeFor.size === 0) return []
    const reach = this.cfg.suppressReachM
    const rate = this.cfg.suppressRate / SECONDS_PER_MINUTE

    for (const [x, z] of positions) {
      const inReach = [...this.activeFor.keys()].sort().filter((cid) => {
        const c = this.centerOf(cid)
        return Math.hypot(x - c[0], z - c[1]) <= reach
      })
      if (inReach.length === 0) continue
      const share = (rate * dt) / inReach.length
      for (const cid of inReach) this.suppressed.set(cid, (this.suppressed.get(cid) ?? 0) + share)
    }

    const changes: CellChange[] = []
    for (const cid of [...this.suppressed.keys()].sort()) {
      if ((this.suppressed.get(cid) ?? 0) >= 1.0 && this.activeFor.has(cid)) {
        this.state.set(cid, 'burnt')
        this.activeFor.delete(cid)
        changes.push(this.change(cid, 'burnt', 'extinguished'))
      }
    }
    for (const c of changes) this.suppressed.delete(c.cell_id)
    return changes
  }

  setWind(wind: Wind): void {
    this.wind = { ...wind }
  }

  /** Un foco puesto a mano. No existe en el backend: es la herramienta 🔥. */
  ignite(cid: string): CellChange[] {
    if (this.activeFor.has(cid)) return []
    if (!parseCell(cid)) return []
    return [this.activate(cid, 'inject'), ...this.markAtRisk()]
  }

  /** Quitar un foco: la celda vuelve a estar intacta, con su progreso a cero. */
  douse(cid: string): CellChange[] {
    if (!this.state.has(cid)) return []
    this.state.delete(cid)
    this.activeFor.delete(cid)
    this.suppressed.delete(cid)
    return [{ cell_id: cid, state: 'intact', hazard: this.cfg.kind, cause: 'extinguished' }]
  }

  /** ¿Hay alguna celda ardiendo dentro del alcance de sofocación de este punto?
   *  Es la diferencia entre un camión «trabajando el fuego» y uno «disponible». */
  activeNear(x: number, z: number): boolean {
    for (const cid of this.activeFor.keys()) {
      const c = this.centerOf(cid)
      if (Math.hypot(x - c[0], z - c[1]) <= this.cfg.suppressReachM) return true
    }
    return false
  }

  stateOf(cid: string): CellState {
    return this.state.get(cid) ?? 'intact'
  }

  get active(): string[] {
    return [...this.activeFor.keys()].sort()
  }

  centerOf(cid: string): [number, number] {
    const parsed = parseCell(cid)
    if (!parsed) return [0, 0]
    const size = this.cfg.cellSize
    const [cx, cz] = parsed
    return [cx * size + (size - 1) / 2, cz * size + (size - 1) / 2]
  }

  // --- interno ---

  private activate(cid: string, cause: Cause): CellChange {
    this.state.set(cid, 'burning')
    this.activeFor.set(cid, 0)
    return this.change(cid, 'burning', cause)
  }

  private change(cid: string, state: CellState, cause: Cause): CellChange {
    return { cell_id: cid, state, hazard: this.cfg.kind, cause }
  }

  /** Vecinas candidatas con su tasa POR SEGUNDO.
   *
   *  Devuelve tasa y no probabilidad a propósito: con probabilidades directas, un
   *  horizonte largo las satura todas a 1 y `cellsAtRisk` pierde el orden justo
   *  cuando más falta hace. */
  private spreadFrom(cid: string): Array<[string, number]> {
    const parsed = parseCell(cid)
    if (!parsed) return []
    const [cx, cz] = parsed
    const { dx: wx, dz: wz } = windVector(this.wind.bearing_deg)
    const out: Array<[string, number]> = []
    for (const [dx, dz] of NEIGHBOURS_8) {
      const nx = cx + dx
      const nz = cz + dz
      const neighbour = cellId(nx, nz)
      if (!IGNITABLE.has(this.stateOf(neighbour)) || this.tooFar(nx, nz)) continue
      const fuel = this.fuelAt(nx, nz)
      if (fuel <= 0) continue
      const distance = Math.hypot(dx, dz)
      const cosine = (dx * wx + dz * wz) / distance
      const rate = (this.cfg.baseSpread + this.wind.speed * Math.max(0, cosine)) * fuel
      out.push([neighbour, Math.max(0, rate) / distance / SECONDS_PER_MINUTE])
    }
    return out
  }

  /** Las que probablemente caigan dentro del horizonte, combinando las vecinas. */
  private markAtRisk(): CellChange[] {
    const risk = new Map<string, number>()
    for (const cid of [...this.activeFor.keys()].sort()) {
      for (const [neighbour, rate] of this.spreadFrom(cid)) {
        const p = probability(rate, BURN_DURATION_S)
        const survives = (1 - p) * (1 - (risk.get(neighbour) ?? 0))
        risk.set(neighbour, 1 - survives)
      }
    }
    const changes: CellChange[] = []
    for (const cid of [...risk.keys()].sort()) {
      if ((risk.get(cid) ?? 0) < AT_RISK_P) continue
      if (this.stateOf(cid) !== 'intact') continue
      this.state.set(cid, 'at_risk')
      changes.push(this.change(cid, 'at_risk', 'at_risk'))
    }
    return changes
  }

  private fuelAt(cx: number, cz: number): number {
    if (!this.cfg.burnable) return 1
    const [x1, z1, x2, z2] = this.cfg.burnable
    const x = cx * this.cfg.cellSize
    const z = cz * this.cfg.cellSize
    return x1 <= x && x <= x2 && z1 <= z && z <= z2 ? 1 : 0
  }

  private tooFar(cx: number, cz: number): boolean {
    return Math.hypot(cx - this.origin[0], cz - this.origin[1]) > MAX_RADIUS_CELLS
  }
}
