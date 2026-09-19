// Llamadas en curso y terminadas, con transcripción en vivo.
//
// El montaje de las tarjetas está en `story/calls.ts`, que es lógica pura. Aquí solo se
// pinta, y lo que se pinta responde a dos requisitos del reto: que ningún hecho aparezca
// sin decir de qué llamada viene, y que una extracción fallida (`facts: null`) se vea
// como lo que es —la transcripción cruda, marcada *sin extraer*— y no como una tarjeta
// vacía que parezca una llamada que no ocurrió.
import { useMemo } from 'react'

import type { Event } from '../types'
import { Empty, Panel, Skeleton } from '../components/Panel'
import { callCards, extracted, type Call } from '../story/calls'
import { CALL_OUTCOME } from '../story/labels'
import { factValue, mmss, pct, shortId } from '../story/format'

export function CallsPanel({
  events,
  awaitingSnapshot,
}: {
  events: Event[]
  awaitingSnapshot: boolean
}) {
  const calls = useMemo(() => callCards(events), [events])
  const enCurso = calls.filter((call) => !call.ended).length

  return (
    <Panel
      title="Llamadas"
      count={calls.length}
      note={enCurso ? `${enCurso} en curso` : undefined}
    >
      {awaitingSnapshot ? (
        <Skeleton rows={3} />
      ) : calls.length === 0 ? (
        <Empty>
          Sin llamadas. Las órdenes del agente y los avisos del vecino se listan aquí con su transcripción.
        </Empty>
      ) : (
        <ol className="flex flex-col gap-3">
          {calls.map((call) => (
            <CallCard key={call.callId} call={call} />
          ))}
        </ol>
      )}
    </Panel>
  )
}

function CallCard({ call }: { call: Call }) {
  const entrante = call.direction === 'inbound'
  // Que una llamada no se contestara es información de primera, no una nota al pie.
  const fallida = call.ended != null && call.ended.outcome !== 'answered'

  return (
    <li className="rounded-[9px] border border-vela-edge p-2.5">
      <div className="flex items-baseline gap-2">
        <span className="tabular-nums text-vela-dim">{mmss(call.t_sim)}</span>
        {/* Las llamadas tienen su propio tono (REQ-200): el cian era suyo y de las
            decisiones a la vez, y son las dos voces que cuentan la historia. */}
        <span className="text-xs font-bold tracking-wide text-vela-call">
          {entrante ? 'AVISO DEL VECINO' : 'ORDEN DEL AGENTE'}
        </span>
        <span className="truncate text-vela-ink">{call.to || call.callId}</span>
        <span
          className={`ml-auto shrink-0 text-xs ${
            fallida ? 'font-bold text-vela-warn' : 'text-vela-dim'
          }`}
        >
          {call.ended ? CALL_OUTCOME[call.ended.outcome] : 'en curso'}
        </span>
      </div>

      {call.intent && (
        <p className="text-xs text-vela-dim">
          {call.intent.replace(/_/g, ' ')}
          {call.poiId && ` · ${shortId(call.poiId)}`}
          {call.urgency && ` · ${call.urgency}`}
        </p>
      )}

      {/* La transcripción viva es lo que hace que la llamada parezca una llamada y no un
          registro. Scroll propio: una conversación larga no puede empujar la rejilla. */}
      {call.lines.length > 0 && (
        <ul className="mt-1 max-h-24 overflow-auto border-l-2 border-vela-edge pl-2">
          {call.lines.map((line, i) => (
            <li key={i} className="text-xs">
              <span className="text-vela-dim">{line.speaker}: </span>
              <span className="text-vela-ink">{line.text}</span>
            </li>
          ))}
        </ul>
      )}

      {call.ended && !call.ended.facts && (
        <div className="mt-1">
          <p className="text-xs font-bold tracking-wide text-vela-warn">SIN EXTRAER</p>
          <p className="max-h-20 overflow-auto text-xs text-vela-ink italic">
            «{call.ended.transcript}»
          </p>
        </div>
      )}

      {call.ended?.facts && (
        <ul className="mt-1 flex flex-col gap-0.5">
          {extracted(call.ended.facts).map(([field, value]) => (
            <li key={field} className="text-xs">
              <span className="text-vela-dim">{field}: </span>
              <span className="text-vela-ink">{value}</span>
            </li>
          ))}
          <li className="text-xs text-vela-dim">
            confianza de la extracción · {pct(call.ended.facts.confidence)}
          </li>
        </ul>
      )}

      {call.facts.length > 0 && (
        <ul className="mt-1 flex flex-col gap-0.5 border-t border-vela-edge pt-1">
          {call.facts.map((fact) => (
            <li key={fact.key} className="text-xs text-vela-ink">
              {fact.key} = {factValue(fact.value)} · {pct(fact.confidence)}
            </li>
          ))}
        </ul>
      )}

      {/* La procedencia, literal y siempre: es lo que separa esto de una demo de mentira. */}
      <p className="mt-1 text-xs text-vela-dim">source: call:{call.callId}</p>
    </li>
  )
}
