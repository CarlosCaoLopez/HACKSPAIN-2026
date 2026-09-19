// Qué ha cambiado y por qué. El banner REPLAN vive aquí.
// El texto es `Policy.rationale` y el motivo del replan, tal cual: "pista sur
// cortada, confirmado por llamada entrante".
//
// Este panel es el que contesta "¿por qué has hecho eso?" sin que yo lo narre, y por
// eso NO es un log: filtra el ruido (`significant.ts`), cuenta cada cambio en una frase
// (`describe.ts`) y le cuelga debajo de dónde vino (`causes.ts`).
import { useEffect, useMemo, useRef } from 'react'

import type { Event } from '../types'
import { Empty, Panel, Row, Rows, Skeleton, Truncated } from '../components/Panel'
import { chain, extend, type CauseIndex } from '../story/causes'
import { describe } from '../story/describe'
import { mmss } from '../story/format'
import { isSignificant } from '../story/significant'

/** Filas en pantalla. Más allá de esto no se lee nada y solo cuesta render. Lo que se
 *  corta se dice al pie: cortar en silencio deja abierta la pregunta «¿lo estás
 *  enseñando todo?» (REQ-195). */
const MAX_ROWS = 120

/** `describe` rotula en versales (`PLAN NUEVO`); el panel las baja a caja de frase
 *  (SPEC-009 REQ-319). Se hace aquí y no en `story/`, que es lógica y no presentación. */
function sentenceCase(label: string): string {
  const lower = label.toLowerCase()
  return lower.charAt(0).toUpperCase() + lower.slice(1)
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

  const { rows, total, counts } = useMemo(() => {
    const index = extend(indexRef.current, events)
    const significant = events.filter(isSignificant)
    // Las cifras cuentan lo que dispara la historia: por qué se replanificó (hechos), cuántas
    // veces, y qué se ordenó después. `action.requested` no es un cambio del panel, pero sí
    // su consecuencia, y por eso se cuenta sobre `events` y no sobre `significant`.
    const count = (type: string) => events.filter((ev) => ev.type === type).length
    return {
      total: significant.length,
      counts: {
        replans: count('plan.replan.started'),
        facts: count('world.fact.asserted'),
        orders: count('action.requested'),
      },
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
    // El subtítulo cuenta el TOTAL, no lo que cabe: si dice 120 cuando hay 157, el truncado
    // sigue siendo silencioso aunque haya un número (REQ-195).
    <Panel
      title="Qué ha cambiado"
      subtitle={`${total} ${total === 1 ? 'cambio' : 'cambios'}`}
      stats={[
        { label: 'replans', value: counts.replans },
        { label: 'hechos', value: counts.facts },
        { label: 'órdenes', value: counts.orders },
      ]}
      bodyRef={bodyRef}
    >
      {awaitingSnapshot ? (
        <Skeleton rows={5} />
      ) : rows.length === 0 ? (
        <Empty>
          Aquí aparece cada cambio con su causa: llamada → hecho → replan → orden.
        </Empty>
      ) : (
        <>
          <Rows>
            {rows.map(({ ev, label, sentence, tone, causes }) => (
              <Row
                key={ev.seq}
                time={mmss(ev.t_sim)}
                // El replan sube un peso y no un color: el rojo es del banner que lo anuncia.
                primary={<p className={tone === 'replan' ? 'font-semibold' : ''}>{sentence}</p>}
                status={sentenceCase(label)}
                // De la lista cerrada de REQ-321, aquí solo cae la orden fallida.
                tone={ev.type === 'action.failed' ? 'urgent' : undefined}
                hint={`seq ${ev.seq}`}
                meta={
                  <>
                    {causes.links.map((cause) => (
                      <p key={cause.seq}>← {describe(cause).sentence}</p>
                    ))}
                    {causes.outOfWindow && (
                      // Honestidad: hubo una causa declarada pero ya no está en memoria.
                      <p className="italic">← causa fuera de ventana</p>
                    )}
                    {/* Ningún hecho se pinta sin su procedencia (REQ-090, SPEC-009 REQ-326). */}
                    <p>{ev.source}</p>
                  </>
                }
              />
            ))}
          </Rows>
          <Truncated n={total - rows.length} />
        </>
      )}
    </Panel>
  )
}
