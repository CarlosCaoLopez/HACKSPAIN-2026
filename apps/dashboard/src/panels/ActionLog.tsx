// Las órdenes que ha dado el sistema y qué ha hecho el mundo con ellas.
//
// Lo que se mira aquí es la latencia en `t_sim`: es lo que enseña que el mundo responde
// de verdad y no que el dashboard pinta órdenes al vacío.
//
// El agrupado por `action_id` vive en `story/actions.ts`, que es lógica pura y se prueba
// con el fixture entero. Aquí solo se pinta.
import { useEffect, useMemo, useRef } from 'react'

import type { Event } from '../types'
import { Empty, Panel, Row, Rows, Skeleton, Truncated, type DataTone } from '../components/Panel'
import { actionRows, isStale, type Action } from '../story/actions'
import { mmss, seconds } from '../story/format'
import { VERB } from '../story/labels'

/** Más allá de esto no se lee nada y solo cuesta render. Lo cortado se dice al pie
 *  (REQ-195). */
const MAX_ROWS = 80

export function ActionLog({
  events,
  awaitingSnapshot,
}: {
  events: Event[]
  awaitingSnapshot: boolean
}) {
  const bodyRef = useRef<HTMLDivElement>(null)
  // El `t_sim` del final del chorro: sin él no se puede decir qué orden lleva mucho
  // abierta, porque el tiempo del dominio no avanza con el reloj de pared.
  const lastT = events.length ? (events[events.length - 1]?.t_sim ?? 0) : 0

  const all = useMemo(() => actionRows(events), [events])
  const rows = all.slice(0, MAX_ROWS)
  const enCurso = all.filter((row) => !row.closed).length
  const hechas = all.filter((row) => row.closed?.ok).length
  const fallidas = all.filter((row) => row.closed && !row.closed.ok).length

  // Auto-scroll solo si el panel está arriba, igual que el panel de cambios: si me he
  // desplazado a mirar algo, no me lo mueve debajo del dedo mientras lo explico.
  useEffect(() => {
    const el = bodyRef.current
    if (el && el.scrollTop <= 8) el.scrollTop = 0
  }, [rows])

  return (
    <Panel
      title="Acciones"
      subtitle={`${all.length} ${all.length === 1 ? 'orden' : 'órdenes'}${enCurso ? ` · ${enCurso} en curso` : ''}`}
      stats={[
        { label: 'en curso', value: enCurso },
        { label: 'confirmadas', value: hechas, tone: hechas ? 'done' : undefined },
        { label: 'fallidas', value: fallidas, tone: fallidas ? 'urgent' : undefined },
      ]}
      bodyRef={bodyRef}
    >
      {awaitingSnapshot ? (
        <Skeleton rows={4} />
      ) : rows.length === 0 ? (
        <Empty>Sin órdenes emitidas. Aparecen al publicarse el primer plan.</Empty>
      ) : (
        <>
          <Rows>
            {rows.map((row) => {
              const { status, tone } = outcome(row, lastT)
              return (
                <Row
                  key={row.actionId}
                  time={mmss(row.t_sim)}
                  // Sin verbo conocido no se inventa `ORDEN`: la frase basta (REQ-327).
                  primary={row.verb ? `${capital(VERB[row.verb])} · ${row.what}` : row.what}
                  status={status}
                  tone={tone}
                  meta={row.closed?.text || undefined}
                  hint={row.actionId}
                />
              )
            })}
          </Rows>
          <Truncated n={all.length - rows.length} />
        </>
      )}
    </Panel>
  )
}

/** Lo que el mundo ha hecho con la orden. Lo que se mira es la latencia en `t_sim`: es lo
 *  que enseña que el mundo responde de verdad y no que se pintan órdenes al vacío. */
function outcome(row: Action, lastT: number): { status: string; tone?: DataTone } {
  if (row.closed) {
    return row.closed.ok
      ? { status: `hecha en ${seconds(row.closed.t_sim - row.t_sim)}`, tone: 'done' }
      : { status: 'falló', tone: 'urgent' }
  }
  return { status: isStale(row, lastT) ? 'sin confirmar' : 'pedida' }
}

function capital(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1)
}
