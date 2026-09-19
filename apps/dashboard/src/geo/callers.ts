// Quién está llamando ahora y desde qué pueblo. SPEC-008 REQ-310.
//
// El POI de una llamada NO se inventa: sale de lo que ya dice el journal.
//   - saliente: `call.requested.poi_id`, que `callCards` ya casa por `task_id`;
//   - entrante: `resolved_poi_id` al colgar, o el `location_hint` de Jev si coincide EXACTO
//     con el nombre de un POI.
// Sin POI no se coloca: se cuenta y la leyenda lo dice (`1 llamada sin ubicar`), igual que
// las tareas sin cubrir (SPEC-006 REQ-225).
import type { POI, Event } from '../types'
import { callCards } from '../story/calls'

/** Cuánto se queda una llamada colgada, en gris, antes de irse (segundos de `t_sim`). */
const LINGER_SIM_S = 5

export interface Caller {
  callId: string
  poiId: string
  direction: 'inbound' | 'outbound'
  /** `true` una vez colgada: se pinta en gris hasta que se va. */
  ended: boolean
  /** La última frase de la transcripción parcial, para el tooltip. */
  lastLine: string
}

export interface Callers {
  placed: Caller[]
  /** Llamadas activas de las que no se sabe el pueblo. */
  unplaced: number
}

export function activeCallers(events: Event[], pois: POI[], tSim: number): Callers {
  const byName = new Map(pois.map((p) => [p.name.trim().toLowerCase(), p.id]))
  const known = new Set(pois.map((p) => p.id))
  const placed: Caller[] = []
  let unplaced = 0

  for (const call of callCards(events)) {
    if (call.ended && tSim - call.ended.t_sim > LINGER_SIM_S) continue

    let poiId = call.poiId
    if (!known.has(poiId)) poiId = call.ended?.facts?.resolved_poi_id ?? ''
    if (!known.has(poiId)) {
      const hint = call.completeness?.fields.find((f) => f.key === 'location_hint')?.value
      poiId = (hint ? byName.get(hint.trim().toLowerCase()) : undefined) ?? ''
    }

    if (!known.has(poiId)) {
      // Solo cuentan las que están en curso: una llamada ya colgada y sin POI no es un
      // aviso, es historia.
      if (!call.ended) unplaced += 1
      continue
    }
    placed.push({
      callId: call.callId,
      poiId,
      direction: call.direction ?? 'outbound',
      ended: call.ended !== null,
      lastLine: call.lines.at(-1)?.text ?? '',
    })
  }
  return { placed, unplaced }
}
