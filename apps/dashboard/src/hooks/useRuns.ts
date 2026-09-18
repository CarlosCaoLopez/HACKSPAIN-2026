// Los runs pasados con su puntuación. `GET /api/runs`.
//
// Se pide en **dos momentos**, no en bucle: al abrir el modo comparación y al llegar
// `run.ended`. Son los dos únicos instantes en los que la lista cambia, y el endpoint
// recorre ficheros del disco: un `setInterval` contra él es trabajo que no se ve en la
// pantalla y sí se nota en el portátil, que es el que tiene que aguantar el pitch.
import { useCallback, useEffect, useState } from 'react'

import type { Event } from '../types'

/** Espeja `RunScore` de `packages/journal/src/journal/score.py`, con `total` anulable:
 *  cuando cuenta el gateway (`score_fallback`), la fórmula del total es de P1 y el hueco
 *  se queda vacío a propósito. */
export interface RunScore {
  run_id: string
  scenario_id: string
  civilians_safe: number
  civilians_exposed_end: number
  cells_burnt: number
  replans: number
  llm_calls: number
  calls_placed: number
  mean_hangup_to_turn_s: number | null
  total: number | null
}

/** Una fila de `GET /api/runs`. TypeScript a mano, como los frames del WS y la respuesta
 *  de control: es un protocolo mío, no un modelo de `contracts`. */
export interface RunRow {
  run_id: string
  path: string
  bytes: number
  score: RunScore | null
  /** Faltan campos: o no hay puntuador, o lo ha contado el gateway. */
  partial: boolean
  /** Lo ha contado `gateway/score_fallback.py`, no `journal.score`. Se dice en pantalla. */
  provisional: boolean
  /** El journal no llega a `run.ended`. Cada Ctrl-C deja uno. */
  incomplete: boolean
  /** Lo ha fabricado `scripts/fake_journal.py --runs`. **Nunca se enseña como real.** */
  synthetic: boolean
  notes: string[]
  error?: string
}

const URL = '/api/runs'

export interface Runs {
  rows: RunRow[]
  loading: boolean
  error: string | null
  reload: () => void
}

export function useRuns(events: Event[], enabled: boolean): Runs {
  const [rows, setRows] = useState<RunRow[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // El otro disparador: cada `run.ended` que pasa por la ventana de eventos añade un
  // run a la lista, y es justo cuando se quiere mirar la comparación.
  const ended = events.filter((ev) => ev.type === 'run.ended').length

  const load = useCallback(() => {
    setLoading(true)
    fetch(URL, { cache: 'no-store' })
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        return res.json() as Promise<RunRow[]>
      })
      .then((body) => {
        setRows(body)
        setError(null)
      })
      // El error se enseña en el panel, no en una consola que en el proyector no existe.
      .catch((err: unknown) => setError(`no se pudo leer ${URL} · ${String(err)}`))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    if (!enabled) return
    load()
  }, [enabled, ended, load])

  return { rows, loading, error, reload: load }
}
