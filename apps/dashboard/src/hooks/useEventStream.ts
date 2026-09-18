// El único sitio que habla con el servidor.
//
// Al conectar llega {kind:"snapshot", state, plan, seq} y luego solo
// {kind:"event", event} en orden de seq. Si hay un hueco en seq: GET /api/state y
// reiniciar. Nada de reconciliación fina.
//
// NO dupliquéis belief.apply a mano en TypeScript: reimplementad solo lo que
// haga falta pintar y para lo demás usad los eventos plan.*, que vienen completos.
//
// `../types` lo genera `make types` desde contracts. Nunca se escribe a mano.
import type { Event, Plan, WorldState } from '../types'

export interface EventStream {
  state: WorldState | null
  plan: Plan | null
  events: Event[]
  connected: boolean
}

export function useEventStream(url = '/ws'): EventStream {
  throw new Error('not implemented')
}
