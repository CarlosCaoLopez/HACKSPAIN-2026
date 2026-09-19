// Qué ha cambiado y por qué. El banner REPLAN vive aquí.
// El texto es `Policy.rationale` y el motivo del replan, tal cual: "pista sur
// cortada, confirmado por llamada entrante".
//
// Este panel es el que contesta "¿por qué has hecho eso?" sin que yo lo narre, y por
// eso NO es un log: filtra el ruido (`significant.ts`), cuenta cada cambio en una frase
// (`describe.ts`) y le cuelga debajo de dónde vino (`causes.ts`).
import { useEffect, useMemo, useRef } from 'react'

import type { Event } from '../types'
import { Empty, Panel, Skeleton, Truncated } from '../components/Panel'
import { chain, extend, type CauseIndex } from '../story/causes'
import { describe, type Tone } from '../story/describe'
import { mmss } from '../story/format'
import { isSignificant } from '../story/significant'

/** Filas en pantalla. Más allá de esto no se lee nada y solo cuesta render. Lo que se
 *  corta se dice al pie: cortar en silencio deja abierta la pregunta «¿lo estás
 *  enseñando todo?» (REQ-195). */
const MAX_ROWS = 120

const TONE: Record<Tone, string> = {
  // Un color, un trabajo (REQ-200). El rojo está reservado al replan —si algo más lo usa,
  // el banner deja de significar nada—, el cian es la voz del sistema (decidir) y las
  // llamadas, que son la otra voz de la historia, tienen la suya.
  replan: 'text-vela-replan',
  decision: 'text-vela-accent',
  fact: 'text-vela-ink',
  world: 'text-vela-ink',
  call: 'text-vela-call',
  human: 'text-vela-ink',
  error: 'text-vela-warn',
  muted: 'text-vela-dim',
}

export function WhatChangedPanel({
  events,
  awaitingSnapshot,
}: {
  events: Event[]
  awaitingSnapshot: boolean
}) {
  // El índice vive en un ref y se extiende con lo nuevo. `extend` solo añade, así que
  // es idempotente y el doble render de StrictMode no lo estropea.
  const indexRef = useRef<CauseIndex>(new Map())
  const bodyRef = useRef<HTMLDivElement>(null)

  const { rows, total } = useMemo(() => {
    const index = extend(indexRef.current, events)
    const significant = events.filter(isSignificant)
    return {
      total: significant.length,
      rows: significant
        .slice(-MAX_ROWS)
        .reverse() // lo último arriba: es donde miro cuando estoy hablando
        .map((ev) => ({ ev, ...describe(ev), causes: chain(ev, index) })),
    }
  }, [events])

  // Auto-scroll SOLO si el panel está arriba: si me he desplazado a mirar algo, no me
  // lo mueve debajo del dedo mientras lo estoy explicando.
  useEffect(() => {
    const el = bodyRef.current
    if (el && el.scrollTop <= 8) el.scrollTop = 0
  }, [rows])

  return (
    // El contador cuenta el TOTAL, no lo que cabe: si dice 120 cuando hay 157, el
    // truncado sigue siendo silencioso aunque haya un número (REQ-195).
    <Panel title="Qué ha cambiado" count={total} bodyRef={bodyRef}>
      {awaitingSnapshot ? (
        <Skeleton rows={5} />
      ) : rows.length === 0 ? (
        <Empty>
          Aquí aparece cada cambio con su causa: llamada → hecho → replan → orden.
        </Empty>
      ) : (
        <>
          <ol className="flex flex-col gap-2">
          {rows.map(({ ev, label, sentence, tone, causes }) => (
            <li key={ev.seq} className="border-l-2 border-vela-edge pl-2">
              <div className="flex items-baseline gap-2">
                <span className="tabular-nums text-vela-dim">{mmss(ev.t_sim)}</span>
                <span className={`text-xs font-bold tracking-wide ${TONE[tone]}`}>
                  {label}
                </span>
              </div>
              {/* Nivel *sala* (REQ-197): esta frase es la que tiene que leerse a diez
                  metros mientras narro, y la del replan un escalón por encima. El cuerpo
                  del panel ya es `text-base`, así que aquí solo sube el replan. */}
              <p
                className={
                  tone === 'replan'
                    ? 'text-lg font-semibold text-vela-replan'
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
              {/* Nivel *registro*: es para mí y para la grabación, pero no se quita —
                  ningún hecho se pinta sin su procedencia (REQ-090). */}
              <p className="text-xs text-vela-dim">
                seq {ev.seq} · {ev.source}
              </p>
            </li>
          ))}
          </ol>
          <Truncated n={total - rows.length} />
        </>
      )}
    </Panel>
  )
}
