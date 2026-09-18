// La línea que sube y cruza el umbral (0,25) justo antes del banner rojo.
// Se alimenta solo de eventos `plan.divergence`.
import type { Event } from '../types'
import { Panel } from '../components/Panel'

export function DivergenceChart({ events }: { events: Event[] }) {
  const points = events.filter((ev) => ev.type === 'plan.divergence')
  return <Panel title="Divergencia" count={points.length} />
}
