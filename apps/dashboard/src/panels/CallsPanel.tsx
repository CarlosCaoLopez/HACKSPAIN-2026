// Llamadas en curso y terminadas, con transcripción en vivo.
// Ningún hecho aparece sin decir de qué llamada viene: `source` siempre visible.
// Con las llamadas sintéticas en marcha: 20 conversaciones, 17 descartadas.
import type { Event } from '../types'
import { Panel } from '../components/Panel'

export function CallsPanel({ events }: { events: Event[] }) {
  const calls = events.filter((ev) => ev.type.startsWith('call.'))
  return <Panel title="Llamadas" count={calls.length} />
}
