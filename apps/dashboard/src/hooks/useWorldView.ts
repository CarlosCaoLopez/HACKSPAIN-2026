// Lo mínimo que hace falta para pintar el mapa, derivado de los eventos.
//
// `docs/interfaces.md` lo autoriza en una frase: «no dupliquéis belief.apply a mano en
// TypeScript: reimplementad solo lo que necesitéis pintar». Esto es esa lista, y es
// CERRADA — seis cosas:
//
//   1. posición y rumbo de unidad     (world.unit.position)
//   2. estado de unidad               (world.unit.status Y world.fact.asserted
//                                      con clave `unit:<id>:available`)
//   3. estado de celda y su causa     (world.cell.changed, world.fire.detected)
//   4. corte de carretera             (world.road.changed)
//   5. grupos de civiles              (world.civilians.changed)
//   6. pin del vecino por Telegram    (citizen.location)
//
// Fuera de eso no se deriva NADA: ni tareas, ni acumulación de hechos con confianzas,
// ni propagación de fuego, ni ETAs, ni costes. Eso es el modelo de creencia y es de P1;
// si un panel lo necesita y no está, se pinta vacío y se dice por qué. Con dos verdades
// en pantalla, la falsa siempre es la mía.
//
// Y cuando el core entregue `apply`, el estado del servidor manda: `seed` reconstruye
// la vista desde el snapshot y los eventos solo rellenan lo que el estado no traiga.
import { useMemo, useRef } from 'react'

import type {
  CellChanged,
  CellState,
  CivState,
  Event,
  UnitStatus,
  VelaEvent,
  Wind,
  WorldState,
} from '../types'

export type CellCause = NonNullable<CellChanged['cause']>

/** El pin GPS de un vecino, ya proyectado a (x, z) por el core. Uno por chat: un pin
 *  nuevo del mismo chat sustituye al anterior (es donde está AHORA). */
export interface CitizenView {
  callId: string
  x: number
  z: number
  poiId: string | null
  poiName: string | null
  live: boolean
  tSim: number
}

export interface UnitView {
  id: string
  x: number
  z: number
  heading: number
  status: UnitStatus
  etaS: number | null
  /** El `t_sim` en el que se emitió esta posición. El mapa real lo usa para interpolar entre
   *  dos posiciones con el reloj de la simulación y no con el de llegada de los eventos
   *  (SPEC-008 REQ-304): así el movimiento no depende de cómo se agrupen en la red. */
  t: number
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
  /** Por qué cambió cada celda la última vez, si el evento lo dijo. `extinguished` es la
   *  que importa pintar: una celda que apagó un camión no es una que se quemó sola.
   *
   *  LIMITACIÓN ASUMIDA: el snapshot no trae causa (`Cell` no la lleva), así que tras un
   *  `seed` (recarga, reconexión, hueco en `seq`) las celdas apagadas vuelven a pintarse
   *  como `burnt` a secas. Es pérdida de color, no de verdad: el estado sigue siendo
   *  `burnt`. Inventar la causa desde otro sitio sería pintar lo que no sabemos. */
  cellCauses: Map<string, CellCause>
  /** Pins de vecinos por Telegram, por `call_id` (`tg_<chat>`). Solo los que traen (x, z).
   *  Tras un snapshot se reconstruyen desde `WorldState.facts` (`seed`). */
  citizens: Map<string, CitizenView>
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
  /** El `units` del snapshot con el que se sembró. Es la forma de saber si `state` es un
   *  snapshot NUEVO o solo la copia con el `seq` al día que `useEventStream` hace con cada
   *  evento: esa copia conserva la misma referencia. Comparar `state.seq` (lo que se hacía)
   *  volvía a sembrar en CADA evento, y sembrar pisa las posiciones que traen los eventos
   *  con las del snapshot: las unidades se quedaban clavadas y solo saltaban cuando llegaba
   *  otro snapshot. */
  seededUnits: WorldState['units'] | null
}

function empty(): Derived {
  return {
    units: new Map(),
    cells: new Map(),
    cellCauses: new Map(),
    citizens: new Map(),
    cutRoads: new Map(),
    civilians: new Map(),
    wind: null,
    tSim: 0,
    applied: 0,
    lastSeq: -1,
    seededUnits: null,
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
      t: state.t_sim,
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
  d.seededUnits = state.units
  seedCitizens(d, state)
}

/** El pin de Telegram no está en el snapshot como tal, pero sí su huella: el hecho
 *  `poi:<id>:confirmed` con `source: call:tg_<chat>` y `kind: observed` que el core
 *  asertó al anclarlo (`voice/telegram.py`). De ahí se rehace el pin en las coordenadas
 *  del POI, para que una recarga a mitad de demo no borre al vecino del mapa. Solo
 *  `observed` (invariante 8), solo `true`, y solo si el POI existe en el estado. Un pin
 *  que no se ancló a ningún POI no deja hecho y no se rehace: no se sabe dónde ponerlo.
 *  Si luego llega un `citizen.location` del mismo chat, `fold` lo sustituye. */
function seedCitizens(d: Derived, state: WorldState): void {
  for (const fact of state.facts) {
    if (fact.kind !== 'observed' || fact.value !== true) continue
    const confirmed = /^poi:([^:]+):confirmed$/.exec(fact.key)
    if (!confirmed?.[1]) continue
    const callId = fact.source.startsWith('call:tg_')
      ? fact.source.slice('call:'.length)
      : fact.call_id?.startsWith('tg_')
        ? fact.call_id
        : null
    if (!callId) continue
    const poi = state.pois[confirmed[1]]
    if (!poi) continue
    // El hecho más reciente del mismo chat gana: `facts` está en orden de aserción.
    d.citizens.set(callId, {
      callId,
      x: poi.x,
      z: poi.z,
      poiId: poi.id,
      poiName: poi.name,
      live: false,
      tSim: fact.t_sim,
    })
  }
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
        t: ev.t_sim,
      })
      break
    }
    case 'world.unit.status': {
      const p = ev.payload
      const before = d.units.get(p.unit_id)
      if (before) d.units.set(p.unit_id, { ...before, status: p.status })
      break
    }
    // La OTRA mitad del punto 2. El estado de una unidad no siempre cambia por
    // `world.unit.status`: cuando una dotación dice por teléfono que no puede salir,
    // `voice/webhooks.py` asierta `unit:<id>:available=false` y quien la pone
    // `unavailable` es `belief.apply_fact` DENTRO del core, sin emitir ningún
    // `world.unit.status`. Sin esto, el camión que acaba de decir que no sigue
    // pintándose en el mapa como si estuviera disponible y *Hechos* lo lista libre,
    // justo en el minuto del guion en el que se está hablando de él.
    //
    // No es un séptimo punto de la lista cerrada ni es rehacer `belief`: es la misma
    // línea de `belief.apply_fact` (`idle` si `true`, `unavailable` si no) para el
    // único campo que este hook ya lleva. Los demás hechos siguen siendo de P1 y los
    // pinta *Hechos* como texto.
    case 'world.fact.asserted': {
      const m = /^unit:([^:]+):available$/.exec(ev.payload.key)
      if (!m?.[1]) return
      const before = d.units.get(m[1])
      if (!before) return
      d.units.set(m[1], { ...before, status: ev.payload.value ? 'idle' : 'unavailable' })
      break
    }
    case 'world.cell.changed': {
      const p = ev.payload
      d.cells.set(p.cell_id, p.state)
      if (p.cause) d.cellCauses.set(p.cell_id, p.cause)
      else d.cellCauses.delete(p.cell_id)
      break
    }
    case 'world.fire.detected':
      d.cells.set(ev.payload.cell_id, 'burning')
      d.cellCauses.delete(ev.payload.cell_id)
      break
    case 'citizen.location': {
      const p = ev.payload
      // Sin (x, z) el core no supo proyectar el pin: no se pinta en un sitio inventado.
      // La tarjeta de la llamada sí lo enseña (lat/lon y «sin anclar»).
      if (p.x == null || p.z == null) return
      d.citizens.set(p.call_id, {
        callId: p.call_id,
        x: p.x,
        z: p.z,
        poiId: p.poi_id ?? null,
        poiName: p.poi_name ?? null,
        live: p.live,
        tSim: ev.t_sim,
      })
      break
    }
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
    if (state && state.units !== d.seededUnits) seed(d, state)

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
      cellCauses: d.cellCauses,
      citizens: d.citizens,
      cutRoads: d.cutRoads,
      civilians: d.civilians,
      wind: d.wind,
      tSim: d.tSim,
      applied: d.applied,
    }
  }, [state, events])
}
