// Cómo arrancó el proceso y en qué modo está corriendo. `GET /api/health`.
//
// Existe por una sola razón: **una demo degradada tiene que anunciarse sola**. Si las
// llamadas son simuladas (plan B nivel 2) o Minecraft está apagado (nivel 3), eso va en
// la cabecera y no en mi memoria — en el minuto cuatro del pitch, hablando, no me voy a
// acordar de decirlo, y una pantalla que calla lo que es simulado miente.
//
// No hace polling: se pide al montar y cuando empieza un run, que son los dos momentos
// en los que el modo puede cambiar. `/api/health` lee ficheros y el dashboard no puede
// permitirse un `setInterval` contra él.
import { useEffect, useState } from 'react'

import type { Event } from '../types'

/** Lo que devuelve `apps/gateway/src/gateway/runtime.py::health`. Solo los campos que
 *  se pintan: el resto del cuerpo es para diagnosticar a las tres de la mañana. */
export interface Health {
  mode: string
  run_id: string | null
  scenario_id: string | null
  paused: boolean
  minecraft: 'encendido' | 'apagado'
  calls: 'reales' | 'simuladas'
  components: Record<string, string>
  notes: Record<string, string>
}

const URL = '/api/health'

export function useHealth(events: Event[]): Health | null {
  const [health, setHealth] = useState<Health | null>(null)

  // El disparador es el último `run.started` visto: un run nuevo puede traer otro modo
  // (ensayo con llamadas reales después de uno con `--mock-calls`).
  const runs = events.filter((ev) => ev.type === 'run.started').length

  useEffect(() => {
    let stopped = false
    fetch(URL, { cache: 'no-store' })
      .then((res) => (res.ok ? (res.json() as Promise<Health>) : null))
      .then((body) => {
        if (!stopped && body) setHealth(body)
      })
      .catch(() => {
        // Sin health no se pintan insignias. Es el caso correcto: no sé en qué modo
        // está, así que no afirmo nada.
      })
    return () => {
      stopped = true
    }
  }, [runs])

  return health
}
