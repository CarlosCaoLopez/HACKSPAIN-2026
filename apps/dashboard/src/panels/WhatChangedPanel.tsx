// Qué ha cambiado y por qué. El banner REPLAN vive aquí.
// El texto es `Policy.rationale` y el motivo del replan, tal cual: "pista sur
// cortada, confirmado por llamada entrante".
//
// Este panel es el que contesta "¿por qué has hecho eso?" sin que yo lo narre, y por
// eso NO es un log: filtra el ruido (`significant.ts`), cuenta cada cambio en una frase
// (`describe.ts`) y le cuelga debajo de dónde vino (`causes.ts`).
import { useEffect, useMemo, useRef } from 'react'

import type { Event } from '../types'
import { Panel } from '../components/Panel'
import { chain, extend, type CauseIndex } from '../story/causes'
import { describe, type Tone } from '../story/describe'
import { mmss } from '../story/format'
import { isSignificant } from '../story/significant'

/** Filas en pantalla. Más allá de esto no se lee nada y solo cuesta render. */
const MAX_ROWS = 120

const TONE: Record<Tone, string> = {
  // El rojo está reservado al replan. Si algo más lo usa, el banner deja de significar
  // nada (index.css lo dice y esta tabla es donde se respeta).
  replan: 'text-vela-replan',
  decision: 'text-vela-accent',
  fact: 'text-vela-ink',
  world: 'text-vela-ink',
  call: 'text-vela-accent',
  human: 'text-vela-ink',
  error: 'text-amber-400',
  muted: 'text-vela-dim',
}

export function WhatChangedPanel({ events }: { events: Event[] }) {
  // El índice vive en un ref y se extiende con lo nuevo. `extend` solo añade, así que
  // es idempotente y el doble render de StrictMode no lo estropea.
  const indexRef = useRef<CauseIndex>(new Map())
  const bodyRef = useRef<HTMLDivElement>(null)

  const rows = useMemo(() => {
    const index = extend(indexRef.current, events)
    return events
      .filter(isSignificant)
      .slice(-MAX_ROWS)
      .reverse() // lo último arriba: es donde miro cuando estoy hablando
      .map((ev) => ({ ev, ...describe(ev), causes: chain(ev, index) }))
  }, [events])

  // Auto-scroll SOLO si el panel está arriba: si me he desplazado a mirar algo, no me
  // lo mueve debajo del dedo mientras lo estoy explicando.
  useEffect(() => {
    const el = bodyRef.current
    if (el && el.scrollTop <= 8) el.scrollTop = 0
  }, [rows])

  return (
    <Panel title="Qué ha cambiado" count={rows.length} bodyRef={bodyRef}>
      {rows.length === 0 ? (
        <p>sin datos</p>
      ) : (
        <ol className="flex flex-col gap-2">
          {rows.map(({ ev, label, sentence, tone, causes }) => (
            <li key={ev.seq} className="border-l-2 border-vela-edge pl-2">
              <div className="flex items-baseline gap-2">
                <span className="tabular-nums text-vela-dim">{mmss(ev.t_sim)}</span>
                <span className={`text-xs font-bold tracking-wide ${TONE[tone]}`}>
                  {label}
                </span>
              </div>
              <p
                className={
                  tone === 'replan'
                    ? 'text-base font-semibold text-vela-replan'
                    : 'text-vela-ink'
                }
              >
                {sentence}
              </p>
              {causes.links.map((cause) => (
                <p key={cause.seq} className="text-xs text-vela-dim">
                  ← {describe(cause).sentence}
                </p>
              ))}
              {causes.outOfWindow && (
                // Honestidad: hubo una causa declarada pero ya no está en memoria.
                <p className="text-xs text-vela-dim italic">← causa fuera de ventana</p>
              )}
              <p className="text-[10px] text-vela-dim">
                seq {ev.seq} · {ev.source}
              </p>
            </li>
          ))}
        </ol>
      )}
    </Panel>
  )
}
