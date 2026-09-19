// Las fuentes reales, tal como las cuenta el gateway. `GET /api/feeds` (SPEC-007 REQ-260).
//
// Lo usa la vista Mapa para decidir si hay un ancla real sobre la que pintar (SPEC-008
// REQ-282). Sondea cada 10 s, como `useHealth` en su día: las detecciones de FIRMS solo
// cambian con un sondeo del gateway, que es cada minutos, no por evento.
import { useEffect, useState } from 'react'

import type { FeedAnchor } from '../geo/anchor'

export interface FeedDetection {
  x: number
  z: number
  lat: number
  lon: number
  footprint_blocks: number
  t_real: string
  satellite: string
  confidence: string
}

export interface FeedSource {
  status: 'off' | 'ok' | 'degraded'
  note: string
}

export interface Feeds {
  mode: string
  anchor: FeedAnchor | null
  sources: Record<string, FeedSource>
  detections: FeedDetection[]
}

/** Por qué no hay mapa real, dicho para el chip. `null` cuando sí lo hay. */
export type FeedsState =
  | { kind: 'loading' }
  | { kind: 'unavailable'; reason: string }
  | { kind: 'ready'; feeds: Feeds }

const URL = '/api/feeds'
const POLL_MS = 10_000

export function useFeeds(): FeedsState {
  const [state, setState] = useState<FeedsState>({ kind: 'loading' })

  useEffect(() => {
    let stopped = false
    const load = () => {
      fetch(URL, { cache: 'no-store' })
        .then((res) => {
          if (!res.ok) throw new Error(`/api/feeds ${res.status}`)
          return res.json() as Promise<Feeds>
        })
        .then((feeds) => {
          if (!stopped) setState({ kind: 'ready', feeds })
        })
        .catch((err: unknown) => {
          // Se dice, no se calla: el chip enseña este motivo (REQ-282).
          if (stopped) return
          const reason = err instanceof Error ? err.message : '/api/feeds no disponible'
          setState({ kind: 'unavailable', reason })
        })
    }
    load()
    const timer = window.setInterval(load, POLL_MS)
    return () => {
      stopped = true
      window.clearInterval(timer)
    }
  }, [])

  return state
}

/** El ancla real, si la hay: fijada y con un modo distinto de `off`. */
export function realAnchor(state: FeedsState): FeedAnchor | null {
  if (state.kind !== 'ready') return null
  const anchor = state.feeds.anchor
  return anchor?.fixed ? anchor : null
}

/** El motivo por el que no hay mapa real, para el chip y el aviso. */
export function noMapReason(state: FeedsState): string {
  if (state.kind === 'loading') return 'buscando el ancla'
  if (state.kind === 'unavailable') return 'fuentes no disponibles'
  const { feeds } = state
  if (feeds.mode === 'off') return 'fuentes apagadas'
  if (!feeds.anchor) return 'sin ancla'
  return 'ancla sin fijar'
}
