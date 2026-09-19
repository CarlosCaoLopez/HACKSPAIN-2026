// Qué celdas merecen un icono y cuáles no. SPEC-008 REQ-301.
//
// Un mapa lleno de insignias no enseña movimiento, enseña ruido. Con cien celdas ardiendo
// el interior del incendio es una mancha naranja sin nada encima: los iconos van solo en el
// FRENTE (lo que avanza) y en el centro de cada zona (lo que hay que nombrar una vez).
//
// Lógica pura sobre `WorldView.cells`, sin Leaflet ni React.
import type { CellState } from '../types'

const CELL_ID = /^cell_(-?\d+)_(-?\d+)$/

/** Tope de llamas en pantalla (REQ-301.1). */
export const MAX_FRONT_FLAMES = 12

interface Cell {
  id: string
  cx: number
  cz: number
}

function parse(id: string): Cell | null {
  const m = CELL_ID.exec(id)
  if (!m) return null
  return { id, cx: Number(m[1]), cz: Number(m[2]) }
}

const NEIGHBOURS: ReadonlyArray<readonly [number, number]> = [
  [1, 0],
  [-1, 0],
  [0, 1],
  [0, -1],
]

/** Las celdas `burning` con algún vecino que no arde ni está quemado: el borde del
 *  incendio. Si hay más de `MAX_FRONT_FLAMES`, se reparten a lo largo del frente en vez de
 *  cortarlo por un lado, para que el frente se siga leyendo entero. */
export function fireFront(cells: ReadonlyMap<string, CellState>): string[] {
  const front: Cell[] = []
  for (const [id, state] of cells) {
    if (state !== 'burning') continue
    const cell = parse(id)
    if (!cell) continue
    const exposed = NEIGHBOURS.some(([dx, dz]) => {
      const around = cells.get(`cell_${cell.cx + dx}_${cell.cz + dz}`)
      return around !== 'burning' && around !== 'burnt'
    })
    if (exposed) front.push(cell)
  }
  // Orden estable: el mismo estado da siempre las mismas llamas, así no bailan de un
  // evento al siguiente.
  front.sort((a, b) => a.cx - b.cx || a.cz - b.cz)
  if (front.length <= MAX_FRONT_FLAMES) return front.map((c) => c.id)
  const step = front.length / MAX_FRONT_FLAMES
  return Array.from({ length: MAX_FRONT_FLAMES }, (_, i) => front[Math.floor(i * step)]?.id).filter(
    (id): id is string => id !== undefined,
  )
}

export type ZoneState = Exclude<CellState, 'intact' | 'burning'>

export interface Zone {
  state: ZoneState
  /** La celda de la zona más cercana a su centro: ahí va el icono. */
  cellId: string
  size: number
}

const ZONE_STATES: ReadonlySet<CellState> = new Set(['burnt', 'at_risk', 'flooded', 'dark'])

/** Un icono por zona contigua de `burnt`, `at_risk`, `flooded` o `dark` (REQ-301.2), no uno
 *  por celda: son manchas o texturas, y basta nombrarlas una vez. */
export function zones(cells: ReadonlyMap<string, CellState>): Zone[] {
  const seen = new Set<string>()
  const out: Zone[] = []
  for (const [id, state] of cells) {
    if (!ZONE_STATES.has(state) || seen.has(id)) continue
    const start = parse(id)
    if (!start) continue

    // Componente conexo por 4 vecinos, con la pila explícita para no recursar.
    const members: Cell[] = []
    const stack = [start]
    seen.add(id)
    while (stack.length > 0) {
      const cell = stack.pop()
      if (!cell) break
      members.push(cell)
      for (const [dx, dz] of NEIGHBOURS) {
        const next = `cell_${cell.cx + dx}_${cell.cz + dz}`
        if (seen.has(next) || cells.get(next) !== state) continue
        const parsed = parse(next)
        if (!parsed) continue
        seen.add(next)
        stack.push(parsed)
      }
    }

    const cx = members.reduce((s, c) => s + c.cx, 0) / members.length
    const cz = members.reduce((s, c) => s + c.cz, 0) / members.length
    let best = members[0]
    let bestD = Infinity
    for (const c of members) {
      const d = (c.cx - cx) ** 2 + (c.cz - cz) ** 2
      if (d < bestD) {
        bestD = d
        best = c
      }
    }
    if (best) out.push({ state: state as ZoneState, cellId: best.id, size: members.length })
  }
  return out
}

/** Un desfase estable en [0, 1,3) s a partir de un id, para que las llamas no latan todas
 *  a la vez (REQ-307). */
export function delayOf(id: string): number {
  let h = 0
  for (let i = 0; i < id.length; i += 1) h = (h * 31 + id.charCodeAt(i)) >>> 0
  return (h % 130) / 100
}
