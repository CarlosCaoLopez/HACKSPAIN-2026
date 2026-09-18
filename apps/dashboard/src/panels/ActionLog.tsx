// El feed de eventos en crudo, con `causes` dibujando la cadena
// llamada → hecho → violación → replan → orden.
// Es lo que se enseña cuando el jurado pregunta por qué el sistema hizo algo.
import type { Event } from '../types'
import { Panel } from '../components/Panel'

export function ActionLog({ events }: { events: Event[] }) {
  const actions = events.filter((ev) => ev.type.startsWith('action.'))
  return <Panel title="Acciones" count={actions.length} />
}
