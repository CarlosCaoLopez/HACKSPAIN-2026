// Qué ha cambiado y por qué. El banner REPLAN vive aquí.
// El texto es `Policy.rationale` y el motivo del replan, tal cual: "pista sur
// cortada, confirmado por llamada entrante".
import type { Event } from '../types'
import { Panel } from '../components/Panel'

export function WhatChangedPanel({ events }: { events: Event[] }) {
  const changes = events.filter(
    (ev) => ev.type.startsWith('world.') || ev.type.startsWith('plan.'),
  )
  return <Panel title="Qué ha cambiado" count={changes.length} />
}
