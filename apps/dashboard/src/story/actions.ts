// Órdenes del core y qué hizo el mundo con ellas, agrupadas por `action_id`.
//
// Vive aquí y no dentro del panel por lo mismo que `describe` y `significant`: es
// lógica pura, se prueba pasándole el fixture entero y no necesita un navegador.
//
// Una fila POR acción, no por evento: `action.requested` la abre y
// `action.completed` / `action.failed` la cierran. Tres eventos de la misma acción son
// una línea — si fueran tres, el panel se llena de ruido y deja de contar nada.
import type { Event, VelaEvent, Verb } from '../types'
import { shortId } from './format'

export interface Action {
  actionId: string
  /** `null` = se cerró una orden que no estaba en la ventana de eventos. No es un caso
   *  raro: pasa en cuanto hay una reconexión, porque el histórico se vacía (REQ-055). */
  verb: Verb | null
  /** Los `args` ya en una frase: aquí no se pinta JSON. */
  what: string
  t_sim: number
  seq: number
  closed: { ok: boolean; t_sim: number; text: string } | null
  /** Si la unidad salió con el «vamos» de su dotación al teléfono.
   *
   *  `null` es el caso normal: la orden no dependía de ninguna llamada. `false` es el
   *  que hay que ver — se agotó el plazo y la unidad salió igual. Se pinta porque un
   *  despacho sin confirmar no puede parecerse a uno confirmado. */
  dispatchConfirmed: boolean | null
}

/** `unknown_verb` es el fallo de integración más probable entre core y sim: se
 *  reconoce de un vistazo en vez de leer un identificador. */
const ERRORS: Record<string, string> = {
  unknown_verb: 'verbo que el sim no conoce',
}

export function actionRows(events: Event[]): Action[] {
  const byAction = new Map<string, Action>()

  for (const envelope of events) {
    if (!envelope.type.startsWith('action.')) continue
    const ev = envelope as unknown as VelaEvent

    if (ev.type === 'action.requested') {
      byAction.set(ev.payload.action_id, {
        actionId: ev.payload.action_id,
        verb: ev.payload.verb,
        what: sentence(ev.payload.args),
        t_sim: ev.t_sim,
        seq: ev.seq,
        closed: null,
        dispatchConfirmed: ev.payload.dispatch_confirmed ?? null,
      })
      continue
    }

    if (ev.type === 'action.completed') {
      const row = open(byAction, ev.payload.action_id, ev.seq, ev.t_sim)
      row.closed = { ok: true, t_sim: ev.t_sim, text: sentence(ev.payload.result) }
      continue
    }

    if (ev.type === 'action.failed') {
      const row = open(byAction, ev.payload.action_id, ev.seq, ev.t_sim)
      const { error } = ev.payload
      row.closed = { ok: false, t_sim: ev.t_sim, text: ERRORS[error] ?? error }
    }
  }

  // Lo último arriba: es donde miro mientras estoy hablando.
  return [...byAction.values()].sort((a, b) => b.seq - a.seq)
}

/** Sin cerrar tras estos segundos de `t_sim`, la orden se marca. El sim confirma un
 *  `goto` al llegar, y ninguna ruta del escenario pasa de dos minutos. Una orden que el
 *  mundo nunca confirmó es justo lo que hay que ver. */
export const STALE_S = 120

export function isStale(row: Action, lastT: number): boolean {
  return !row.closed && lastT - row.t_sim > STALE_S
}

/** La fila de esa acción, creándola si el `action.requested` no está.
 *
 *  Una acción que se cierra sin que hayamos visto pedirla NO se descarta: se pinta
 *  igual, diciendo que la orden no está en la ventana. Pasa constantemente —tras cada
 *  reconexión el histórico se vacía— y pasa en el fixture con `act_0003`, que falla sin
 *  haber sido pedida. Tirarla sería esconder justo el fallo que hay que ver. */
function open(
  byAction: Map<string, Action>,
  actionId: string,
  seq: number,
  t_sim: number,
): Action {
  const existing = byAction.get(actionId)
  if (existing) return existing
  const fresh: Action = {
    actionId,
    verb: null,
    what: 'orden anterior a la ventana de eventos',
    t_sim,
    seq,
    closed: null,
    dispatchConfirmed: null,
  }
  byAction.set(actionId, fresh)
  return fresh
}

function sentence(a: Record<string, unknown>): string {
  const unit = typeof a.unit_id === 'string' ? shortId(a.unit_id) : null
  const to = typeof a.to === 'string' ? a.to : null
  if (unit && to) return `${unit} → ${to}`
  const parts = Object.entries(a).map(([k, v]) => `${k} ${String(v)}`)
  return parts.join(', ')
}
