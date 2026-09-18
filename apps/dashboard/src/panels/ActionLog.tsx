// Las órdenes que ha dado el sistema y qué ha hecho el mundo con ellas.
//
// Lo que se mira aquí es la latencia en `t_sim`: es lo que enseña que el mundo responde
// de verdad y no que el dashboard pinta órdenes al vacío.
//
// El agrupado por `action_id` vive en `story/actions.ts`, que es lógica pura y se prueba
// con el fixture entero. Aquí solo se pinta.
import { useEffect, useMemo, useRef } from 'react'

import type { Event } from '../types'
import { Panel } from '../components/Panel'
import { actionRows, isStale } from '../story/actions'
import { mmss, seconds } from '../story/format'
import { VERB } from '../story/labels'

/** Más allá de esto no se lee nada y solo cuesta render. */
const MAX_ROWS = 80

export function ActionLog({ events }: { events: Event[] }) {
  const bodyRef = useRef<HTMLDivElement>(null)
  // El `t_sim` del final del chorro: sin él no se puede decir qué orden lleva mucho
  // abierta, porque el tiempo del dominio no avanza con el reloj de pared.
  const lastT = events.length ? (events[events.length - 1]?.t_sim ?? 0) : 0

  const rows = useMemo(() => actionRows(events).slice(0, MAX_ROWS), [events])
  const abiertas = rows.filter((row) => !row.closed).length

  // Auto-scroll solo si el panel está arriba, igual que el panel de cambios: si me he
  // desplazado a mirar algo, no me lo mueve debajo del dedo mientras lo explico.
  useEffect(() => {
    const el = bodyRef.current
    if (el && el.scrollTop <= 8) el.scrollTop = 0
  }, [rows])

  return (
    <Panel
      title="Acciones"
      count={rows.length}
      note={abiertas ? `${abiertas} en curso` : undefined}
      bodyRef={bodyRef}
    >
      {rows.length === 0 ? (
        <p>sin datos</p>
      ) : (
        <ol className="flex flex-col gap-2">
          {rows.map((row) => (
            <li key={row.actionId} className="border-l-2 border-vela-edge pl-2">
              <div className="flex items-baseline gap-2">
                <span className="tabular-nums text-vela-dim">{mmss(row.t_sim)}</span>
                <span className="text-xs font-bold tracking-wide text-vela-accent">
                  {row.verb ? VERB[row.verb] : 'ORDEN'}
                </span>
                <span className="ml-auto shrink-0 text-xs tabular-nums text-vela-dim">
                  {row.closed
                    ? `${seconds(row.closed.t_sim - row.t_sim)} en responder`
                    : isStale(row, lastT)
                      ? 'sin confirmar'
                      : 'pedida'}
                </span>
              </div>
              <p className="text-vela-ink">{row.what}</p>
              {row.closed && (
                <p
                  className={
                    row.closed.ok ? 'text-xs text-vela-dim' : 'text-xs text-amber-400'
                  }
                >
                  {row.closed.ok
                    ? `hecha${row.closed.text ? ` · ${row.closed.text}` : ''}`
                    : `FALLÓ · ${row.closed.text}`}
                </p>
              )}
              <p className="text-[10px] text-vela-dim">{row.actionId}</p>
            </li>
          ))}
        </ol>
      )}
    </Panel>
  )
}
