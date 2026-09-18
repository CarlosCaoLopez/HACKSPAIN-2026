// El único sitio que le manda algo al servidor. `POST /control/*`.
//
// El WS es unidireccional a propósito (H2): el control va por HTTP y el efecto vuelve
// por el chorro de eventos, que sigue siendo la única fuente de verdad de la pantalla.
// Aquí NO se toca el estado del mundo: si pintase el veto como aplicado y el core lo
// ignorara, la pantalla mentiría y me enteraría en el pitch.
//
// `ControlResponse` es TypeScript escrito a mano, el segundo del proyecto junto a los
// frames del WS: los dos son protocolos míos (gateway ↔ dashboard) y ninguno es un
// modelo de `contracts`, así que `make types` no los genera ni debe generarlos.
import { useCallback, useState } from 'react'

import type { OverrideKind } from '../types'

/** Espeja `ControlResponse` de `apps/gateway/src/gateway/control.py`. */
export interface ControlResponse {
  accepted: boolean
  published: boolean
  /** Aceptado pero no publicado: en replay el override no entra en el chorro. */
  echo: boolean
  run_id: string | null
  seq: number | null
  detail: string
}

export interface Sent {
  at: number
  response: ControlResponse
}

export interface Control {
  /** `key` agrupa por botón (`unit:task:kind`): cada uno tiene su vuelo y su error. */
  override: (key: string, body: OverrideRequest) => Promise<void>
  pending: ReadonlySet<string>
  sent: ReadonlyMap<string, Sent>
  failed: ReadonlyMap<string, string>
}

export interface OverrideRequest {
  kind: OverrideKind
  target: string
  value?: string | number | boolean | null
  note?: string
}

const URL = '/control/override'

export function useControl(): Control {
  const [pending, setPending] = useState<ReadonlySet<string>>(new Set())
  const [sent, setSent] = useState<ReadonlyMap<string, Sent>>(new Map())
  const [failed, setFailed] = useState<ReadonlyMap<string, string>>(new Map())

  const override = useCallback(async (key: string, body: OverrideRequest) => {
    // Un doble clic nervioso no puede publicar dos overrides. El botón ya se
    // deshabilita, pero la guarda va aquí porque es donde no depende del render.
    let already = false
    setPending((prev) => {
      already = prev.has(key)
      if (already) return prev
      return new Set(prev).add(key)
    })
    if (already) return

    try {
      const res = await fetch(URL, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ note: '', value: null, ...body }),
      })
      const payload: unknown = await res.json().catch(() => null)
      if (!res.ok) {
        // El `detail` del servidor, literal: es lo que explica por qué no ha ido.
        const detail =
          payload && typeof payload === 'object' && 'detail' in payload
            ? String((payload as { detail: unknown }).detail)
            : `HTTP ${res.status}`
        setFailed((prev) => new Map(prev).set(key, detail))
        return
      }
      setFailed((prev) => {
        const next = new Map(prev)
        next.delete(key)
        return next
      })
      setSent((prev) =>
        new Map(prev).set(key, { at: Date.now(), response: payload as ControlResponse }),
      )
    } catch (err) {
      // En el proyector no hay consola: el error se enseña en la tarjeta o no existe.
      setFailed((prev) => new Map(prev).set(key, `sin respuesta del gateway · ${err}`))
    } finally {
      setPending((prev) => {
        const next = new Set(prev)
        next.delete(key)
        return next
      })
    }
  }, [])

  return { override, pending, sent, failed }
}
