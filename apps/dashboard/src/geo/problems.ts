// Qué zonas del mapa tienen un problema, y cuánto. SPEC-006 REQ-223…225.
//
// Lógica pura sobre lo que el mapa YA sabe (`WorldView`, la capa y el plan): el halo de
// cada POI y el resumen de zonas salen de la misma función, así que el mapa y su lista no
// pueden contradecirse. No predice nada: «el pueblo está dentro de una celda que arde» es
// una consulta, «el fuego llegará» sería propagación y es de P1 (REQ-072).
import type { CellState, CivState, Plan, POI } from '../types'
import type { CitizenView, CivilianView, WorldView } from '../hooks/useWorldView'
import type { ScenarioLayer } from '../hooks/useScenario'
import { CIV_STATE, UNIT_STATUS } from '../story/labels'
import { shortId } from '../story/format'
import { cellIdAt, type Geo } from './grid'

/** A menos de esto de un pin, una unidad «ha llegado». En unidades del mundo (bloques),
 *  que es la escala en la que el sim mide sus alcances (`suppress_reach_m`): 60 bloques
 *  es media pista de Pueblo B, no 60 metros reales del ancla. */
const REACHED_M = 60

/** Peor primero: es el orden en el que un grupo de civiles preocupa. */
const CIV_RANK: Record<CivState, number> = {
  trapped: 4,
  exposed: 3,
  warned: 2,
  evacuating: 1,
  safe: 0,
}

/** ¿Ya se está atendiendo al vecino del pin? Ver el bucle de `problems`. */
export function citizenAttended(c: CitizenView, view: WorldView): boolean {
  if (c.poiId) {
    let worst: CivilianView | null = null
    for (const g of view.civilians.values()) {
      if (g.poiId !== c.poiId) continue
      if (!worst || CIV_RANK[g.state] > CIV_RANK[worst.state]) worst = g
    }
    if (worst) return worst.state === 'evacuating' || worst.state === 'safe'
  }
  for (const u of view.units.values()) {
    if (Math.hypot(u.x - c.x, u.z - c.z) < REACHED_M) return true
  }
  return false
}

/** 0 = sin problema, 1 = aviso, 2 = grave. Con tres niveles basta para ordenar y pintar. */
export type Level = 0 | 1 | 2

export interface PoiThreat {
  level: Level
  /** La celda que contiene al POI, si es una que importa (`at_risk`, `burning`). */
  cell: CellState | null
  /** Civiles en apuros (`exposed`, `trapped`) de este POI. */
  civ: CivilianView | null
}

export function poiThreat(poi: POI, view: WorldView, geo: Geo): PoiThreat {
  const raw = view.cells.get(cellIdAt(poi.x, poi.z, geo))
  const cell = raw === 'burning' || raw === 'at_risk' ? raw : null
  // Un POI puede tener varios grupos: el peor es el que manda.
  let civ: CivilianView | null = null
  for (const group of view.civilians.values()) {
    if (group.poiId !== poi.id) continue
    if (group.state !== 'exposed' && group.state !== 'trapped') continue
    if (!civ || group.state === 'trapped') civ = group
  }
  const level: Level =
    cell === 'burning' || civ?.state === 'trapped' ? 2 : cell === 'at_risk' || civ ? 1 : 0
  return { level, cell, civ }
}

export interface Problem {
  id: string
  level: Level
  text: string
}

export function problems(view: WorldView, layer: ScenarioLayer, plan: Plan | null): Problem[] {
  const geo: Geo = { origin: layer.origin, cellSize: layer.hazard.cell_size }
  const out: Problem[] = []

  for (const poi of layer.pois) {
    const t = poiThreat(poi, view, geo)
    if (t.level === 0) continue
    const parts = [poi.name]
    if (t.cell) parts.push(t.cell === 'burning' ? 'en zona de fuego' : 'en zona de riesgo')
    if (t.civ) parts.push(`${t.civ.count} ${CIV_STATE[t.civ.state]}`)
    out.push({ id: poi.id, level: t.level, text: parts.join(' · ') })
  }

  for (const [edgeId, cause] of view.cutRoads) {
    out.push({
      id: edgeId,
      level: 1,
      text: `${edgeId} cortada${cause ? ` · ${cause}` : ''}`,
    })
  }

  for (const unit of view.units.values()) {
    if (unit.status === 'unavailable') {
      out.push({ id: unit.id, level: 1, text: `${shortId(unit.id)} ${UNIT_STATUS.unavailable}` })
    }
  }

  // Celdas que no son fuego y no tienen POI encima: se cuentan, no se listan una a una.
  let flooded = 0
  let dark = 0
  for (const state of view.cells.values()) {
    if (state === 'flooded') flooded += 1
    if (state === 'dark') dark += 1
  }
  if (flooded > 0) out.push({ id: 'flooded', level: 1, text: `${flooded} celdas inundadas` })
  if (dark > 0) out.push({ id: 'dark', level: 1, text: `${dark} celdas sin luz` })

  // Un vecino ubicado por Telegram que aún no está atendido es una zona con problema con
  // nombre y apellidos: es la persona a la que hay que mandar una unidad (beat 4:25). Se
  // retira cuando deja de serlo, y «deja de serlo» se mira en lo que el mapa ya sabe:
  //   - pin anclado a un POI con civiles: cuando el PEOR grupo del POI está evacuando o a
  //     salvo (el vecino es uno de ellos, y su grupo ya se mueve);
  //   - pin sin POI (o POI sin grupos): cuando una unidad está a menos de `REACHED_M`
  //     del pin, que es «ya han llegado» sin que nadie lo diga.
  for (const c of view.citizens.values()) {
    if (citizenAttended(c, view)) continue
    out.push({
      id: c.callId,
      level: 1,
      text: `vecino por Telegram · ${c.poiName ?? 'sin anclar'}`,
    })
  }

  // Las tareas sin cubrir solo traen id, sin geometría (REQ-225): se cuentan y no se
  // ubican, y se dice. Ponerlas en un sitio sería inventarlo.
  const uncovered = plan?.unassigned_tasks.length ?? 0
  if (uncovered > 0) {
    out.push({
      id: 'uncovered',
      level: 1,
      text: `${uncovered} ${uncovered === 1 ? 'tarea sin cubrir' : 'tareas sin cubrir'} (sin ubicación)`,
    })
  }

  return out.sort((a, b) => b.level - a.level)
}
