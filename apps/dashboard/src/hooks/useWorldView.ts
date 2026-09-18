// Lo mínimo que hace falta para pintar el mapa, derivado de los eventos.
//
// `docs/interfaces.md` lo autoriza en una frase: «no dupliquéis belief.apply a mano en
// TypeScript: reimplementad solo lo que necesitéis pintar». Esto es esa lista, y es
// CERRADA — cinco cosas:
//
//   1. posición y rumbo de unidad     (world.unit.position)
//   2. estado de unidad               (world.unit.status)
//   3. estado de celda                (world.cell.changed, world.fire.detected)
//   4. corte de carretera             (world.road.changed)
//   5. grupos de civiles              (world.civilians.changed)
//
// Fuera de eso no se deriva NADA: ni tareas, ni acumulación de hechos con confianzas,
// ni propagación de fuego, ni ETAs, ni costes. Eso es el modelo de creencia y es de P1;
// si un panel lo necesita y no está, se pinta vacío y se dice por qué. Con dos verdades
// en pantalla, la falsa siempre es la mía.
//
// Y cuando el core entregue `apply`, el estado del servidor manda: `seed` reconstruye
// la vista desde el snapshot y los eventos solo rellenan lo que el estado no traiga.
import { useMemo, useRef } from 'react'

import type { CellState, CivState, Event, UnitStatus, VelaEvent, Wind, WorldState } from '../types'

export interface UnitView {
  id: string
  x: number
  z: number
  heading: number
  status: UnitStatus
  etaS: number | null
}

export interface CivilianView {
  id: string
  poiId: string
  count: number
  state: CivState
}

export interface WorldView {
  units: Map<string, UnitView>
  cells: Map<string, CellState>
  /** `edge_id` → causa del corte (`null` si no se dijo). Solo los cortados. */
  cutRoads: Map<string, string | null>
  civilians: Map<string, CivilianView>
  wind: Wind | null
  tSim: number
  /** Cuántos eventos se han plegado: sirve para saber si hay algo que pintar. */
  applied: number
}

interface Derived extends WorldView {
  lastSeq: number
  seededFrom: number | null
}

function empty(): Derived {
  return {
    units: new Map(),
    cells: new Map(),
    cutRoads: new Map(),
    civilians: new Map(),
    wind: null,
    tSim: 0,
    applied: 0,
    lastSeq: -1,
    seededFrom: null,
  }
}

function reset(d: Derived): void {
  const fresh = empty()
  Object.assign(d, fresh)
}

/** El estado del servidor gana: la vista se rehace con él y los eventos siguen desde
 *  su `seq`. Es lo que evita tener dos verdades cuando P1 entregue `belief.apply`. */
function seed(d: Derived, state: WorldState): void {
  reset(d)
  for (const unit of Object.values(state.units)) {
    d.units.set(unit.id, {
      id: unit.id,
      x: unit.x,
      z: unit.z,
      heading: 0,
      status: unit.status,
      etaS: null,
    })
  }
  for (const cell of Object.values(state.cells)) d.cells.set(cell.id, cell.state)
  for (const road of Object.values(state.roads)) {
    if (road.cut) d.cutRoads.set(road.id, road.cut_cause ?? null)
  }
  for (const group of Object.values(state.civilians)) {
    d.civilians.set(group.id, {
      id: group.id,
      poiId: group.poi_id,
      count: group.count,
      state: group.state,
    })
  }
  d.wind = state.wind
  d.tSim = state.t_sim
  d.lastSeq = state.seq
  d.seededFrom = state.seq
}

function fold(d: Derived, envelope: Event): void {
  const ev = envelope as unknown as VelaEvent
  switch (ev.type) {
    case 'world.unit.position': {
      const p = ev.payload
      const before = d.units.get(p.unit_id)
      d.units.set(p.unit_id, {
        id: p.unit_id,
        x: p.x,
        z: p.z,
        heading: p.heading,
        // La posición no dice el estado, así que se conserva el que hubiera. Si no
        // hubiera ninguno, `moving` es lo único coherente con estar publicando
        // posiciones.
        status: before?.status ?? 'moving',
        etaS: p.eta_s ?? null,
      })
      break
    }
    case 'world.unit.status': {
      const p = ev.payload
      const before = d.units.get(p.unit_id)
      if (before) d.units.set(p.unit_id, { ...before, status: p.status })
      break
    }
    case 'world.cell.changed':
      d.cells.set(ev.payload.cell_id, ev.payload.state)
      break
    case 'world.fire.detected':
      d.cells.set(ev.payload.cell_id, 'burning')
      break
    case 'world.road.changed': {
      const p = ev.payload
      if (p.cut) d.cutRoads.set(p.edge_id, p.cause ?? null)
      else d.cutRoads.delete(p.edge_id)
      break
    }
    case 'world.civilians.changed': {
      const p = ev.payload
      d.civilians.set(p.group_id, {
        id: p.group_id,
        poiId: p.poi_id,
        count: p.count,
        state: p.state,
      })
      break
    }
    case 'world.tick':
      d.wind = ev.payload.wind
      break
    default:
      // Todo lo demás es historia, plan o telefonía: lo pintan otros paneles.
      return
  }
  d.applied += 1
}

export function useWorldView(state: WorldState | null, events: Event[]): WorldView {
  const ref = useRef<Derived>(empty())

  return useMemo(() => {
    const d = ref.current

    // El hook del WS vacía `events` al aplicar un snapshot (hueco o reconexión): es la
    // señal de que el histórico ya no vale y la vista se rehace desde cero.
    if (events.length === 0 && d.lastSeq >= 0) reset(d)
    if (state && state.seq !== d.seededFrom) seed(d, state)

    for (const ev of events) {
      if (ev.seq <= d.lastSeq) continue
      fold(d, ev)
      d.lastSeq = ev.seq
      d.tSim = ev.t_sim
    }

    // Wrapper nuevo con los mismos Maps: identidad nueva para React, cero copias de
    // datos. Con 500 eventos y un tick por segundo, copiar los Maps sería tirar tiempo.
    return {
      units: d.units,
      cells: d.cells,
      cutRoads: d.cutRoads,
      civilians: d.civilians,
      wind: d.wind,
      tSim: d.tSim,
      applied: d.applied,
    }
  }, [state, events])
}
