// El replan vigente, derivado del chorro.
//
// Es una función pura y no dos `useState` sincronizados a mano por una razón: un par de
// estados que se encienden con un evento y se apagan con otro se desincroniza en cuanto
// llega una reconexión (el histórico se vacía, REQ-055), y el banner se queda puesto —o
// no entra— sin que se sepa por qué. Derivado de `events`, eso no puede pasar.
import type { Event, VelaEvent } from '../types'

export interface Replan {
  seq: number
  t_sim: number
  reason: string
  trigger: string
}

/** El último `plan.replan.started` que todavía no tiene plan.
 *
 *  Un `plan.emitted` posterior lo cierra: ya hay respuesta, el replan se acabó. */
export function currentReplan(events: Event[]): Replan | null {
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const envelope = events[i]
    if (!envelope) continue
    if (envelope.type === 'plan.emitted') return null
    if (envelope.type !== 'plan.replan.started') continue

    const ev = envelope as unknown as VelaEvent
    if (ev.type !== 'plan.replan.started') continue
    return {
      seq: ev.seq,
      t_sim: ev.t_sim,
      reason: ev.payload.reason,
      trigger: ev.payload.trigger,
    }
  }
  return null
}
