// El adaptador entre el estado simulado y lo que las capas del mapa saben leer.
//
// `map/layers.tsx`, `geo/problems.ts` y `geo/declutter.ts` están escritos contra el
// tipo `WorldView` de `hooks/useWorldView.ts`, que es lo que el dashboard de verdad
// deriva del chorro de eventos. Esta función produce esa misma forma desde el
// `SimState`, y es lo único que hace falta para reutilizar los tres módulos sin
// tocarles una línea.
//
// Los tipos se redeclaran aquí en vez de importarse de `hooks/useWorldView` a
// propósito: ese hook abre el WebSocket, y esta página no habla con nadie. Son
// estructuras de datos, no comportamiento, así que la copia no arrastra nada.
import type { CellState, CivState, UnitStatus, Wind } from '../types'

import type { SimState } from './engine/state'
import type { CellChange } from './engine/fire'

export interface UnitView {
  id: string
  x: number
  z: number
  heading: number
  status: UnitStatus
  etaS: number | null
  t: number
}

export interface CivilianView {
  id: string
  poiId: string
  count: number
  state: CivState
}

export interface CitizenView {
  callId: string
  x: number
  z: number
  poiId: string | null
  poiName: string | null
  live: boolean
  tSim: number
}

export interface WorldView {
  units: Map<string, UnitView>
  cells: Map<string, CellState>
  cellCauses: Map<string, NonNullable<CellChange['cause']>>
  citizens: Map<string, CitizenView>
  cutRoads: Map<string, string | null>
  civilians: Map<string, CivilianView>
  wind: Wind | null
  tSim: number
  applied: number
}

export function toWorldView(s: SimState, headingOf: (unitId: string) => number): WorldView {
  const units = new Map<string, UnitView>()
  for (const u of s.units.values()) {
    units.set(u.id, {
      id: u.id,
      x: u.x,
      z: u.z,
      heading: headingOf(u.id),
      status: u.status,
      etaS: null,
      t: s.t_sim,
    })
  }

  const cells = new Map<string, CellState>()
  for (const [id, c] of s.cells) cells.set(id, c.state)

  const cutRoads = new Map<string, string | null>()
  for (const r of s.roads.values()) if (r.cut) cutRoads.set(r.id, r.cut_cause ?? null)

  const civilians = new Map<string, CivilianView>()
  for (const g of s.civilians.values()) {
    civilians.set(g.id, { id: g.id, poiId: g.poi_id, count: g.count, state: g.state })
  }

  return {
    units,
    cells,
    cellCauses: s.cellCauses as Map<string, NonNullable<CellChange['cause']>>,
    citizens: new Map(),
    cutRoads,
    civilians,
    wind: s.wind,
    tSim: s.t_sim,
    applied: s.seq,
  }
}
