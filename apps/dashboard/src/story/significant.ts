// Qué es un cambio y qué es ruido.
//
// El panel de cambios no es un log: es *qué ha cambiado y qué lo ha provocado*. Un run
// de seis minutos trae ~360 `world.tick` y cientos de `world.unit.position`; si entran
// en la lista, tapan la historia y el panel deja de servir para lo único que sirve.
//
// La lista es explícita y no una regla ("todo menos ticks"): cuando el catálogo crezca,
// quiero decidir a mano si el tipo nuevo cuenta una parte de la historia o no.
import type { Event, EventType } from '../types'

const SIGNIFICANT: ReadonlySet<EventType> = new Set<EventType>([
  'plan.replan.started',
  'plan.policy.emitted',
  'plan.violation',
  'plan.emitted',
  'plan.divergence',
  'world.fact.asserted',
  'world.fire.detected',
  'world.road.changed',
  'world.inject',
  'world.unit.status',
  'world.civilians.changed',
  'call.started',
  'call.ended',
  // El pin del vecino por Telegram: es el «dónde» que la voz no pudo dar (beat 4:25).
  'citizen.location',
  // La señal del core al agente en vivo es un cambio con causa (cuelga del plan) y es la
  // latencia del pitch. `call.affect` NO entra: es ambiente, tres por llamada.
  'call.signal.requested',
  'call.signal.sent',
  'human.override',
  'action.failed',
  'run.started',
  'run.ended',
  'event.malformed',
])

/** Ruido de alto volumen: el mapa los usa, la historia no.
 *
 *  `world.cell.changed` es ruido (cientos de `spread`/`at_risk` por run) con UNA
 *  excepción: `cause: extinguished` es un camión que ha sofocado una celda, es decir,
 *  el efecto de nuestra propia orden. Eso es historia y entra. */
export function isSignificant(ev: Event): boolean {
  if (ev.type === 'world.cell.changed') return ev.payload.cause === 'extinguished'
  return SIGNIFICANT.has(ev.type)
}
