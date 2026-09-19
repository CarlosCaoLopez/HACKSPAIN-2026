// Llamadas en curso y terminadas, con transcripción en vivo.
//
// El montaje de las tarjetas está en `story/calls.ts`, que es lógica pura. Aquí solo se
// pinta, y lo que se pinta responde a dos requisitos del reto: que ningún hecho aparezca
// sin decir de qué llamada viene, y que una extracción fallida (`facts: null`) se vea
// como lo que es —la transcripción cruda, marcada *sin extraer*— y no como una fila
// vacía que parezca una llamada que no ocurrió.
import { useEffect, useMemo, useRef } from 'react'

import type { CallOutcome, Event } from '../types'
import { Empty, Panel, Row, Rows, Skeleton } from '../components/Panel'
import { callCards, extracted, type Call } from '../story/calls'
import { CompletenessPanel } from './CompletenessPanel'
import { CALL_OUTCOME } from '../story/labels'
import { factValue, mmss, pct, shortId } from '../story/format'

/** Los desenlaces en rojo: la llamada no llegó a hablarse (REQ-321, «no contestada»). */
const UNANSWERED: ReadonlySet<CallOutcome> = new Set<CallOutcome>(['no_answer', 'busy', 'failed'])

export function CallsPanel({
  events,
  awaitingSnapshot,
  focusCallId = null,
}: {
  events: Event[]
  awaitingSnapshot: boolean
  /** La llamada a la que hay que llevar la vista: se llega aquí con un clic en la persona
   *  que llama del mapa (SPEC-008 REQ-310). Aditiva: sin ella el panel es el de siempre. */
  focusCallId?: string | null
}) {
  const calls = useMemo(() => callCards(events), [events])
  const enCurso = calls.filter((call) => !call.ended).length
  const sinExtraer = calls.filter((call) => call.ended && !call.ended.facts).length

  return (
    <Panel
      title="Llamadas"
      subtitle={`${calls.length} ${calls.length === 1 ? 'llamada' : 'llamadas'}${enCurso ? ` · ${enCurso} en curso` : ''}`}
      stats={[
        { label: 'en curso', value: enCurso },
        { label: 'cerradas', value: calls.length - enCurso },
        { label: 'sin extraer', value: sinExtraer, tone: sinExtraer ? 'urgent' : undefined },
      ]}
    >
      {awaitingSnapshot ? (
        <Skeleton rows={3} />
      ) : calls.length === 0 ? (
        <Empty>
          Sin llamadas. Las órdenes del agente y los avisos del vecino se listan aquí con su transcripción.
        </Empty>
      ) : (
        <Rows>
          {calls.map((call) => (
            <CallRow key={call.callId} call={call} focused={call.callId === focusCallId} />
          ))}
        </Rows>
      )}
    </Panel>
  )
}

function CallRow({ call, focused }: { call: Call; focused: boolean }) {
  const entrante = call.direction === 'inbound'
  // Que una llamada no se contestara es información de primera, no una nota al pie. `hung_up`
  // no cuenta: se habló y alguien colgó al acabar, que es como termina una llamada que fue bien.
  const fallida = call.ended != null && UNANSWERED.has(call.ended.outcome)
  const ref = useRef<HTMLLIElement>(null)

  useEffect(() => {
    if (focused) ref.current?.scrollIntoView({ block: 'center' })
  }, [focused])

  const meta = [call.intent.replace(/_/g, ' '), call.poiId && shortId(call.poiId), call.urgency]
    .filter(Boolean)
    .join(' · ')

  return (
    <Row
      rowRef={ref}
      // La llamada enfocada desde el mapa se marca con fondo, no con un color de acento.
      className={focused ? '-mx-5 bg-vela-bg px-5' : ''}
      time={mmss(call.t_sim)}
      primary={
        <>
          <span className="text-vela-dim">
            {entrante ? 'Aviso del vecino' : 'Orden del agente'} ·{' '}
          </span>
          {call.to || shortId(call.callId)}
        </>
      }
      status={call.ended ? CALL_OUTCOME[call.ended.outcome] : 'en curso'}
      tone={fallida ? 'urgent' : undefined}
      meta={meta || undefined}
      // La procedencia, literal, al pasar el ratón: la fila ya ES la llamada (REQ-328).
      hint={`call:${call.callId}`}
    >
      {/* La transcripción viva es lo que hace que la llamada parezca una llamada y no un
          registro. Scroll propio: una conversación larga no puede empujar la rejilla. */}
      {call.lines.length > 0 && (
        <ul className="mt-1.5 max-h-24 overflow-auto text-sm">
          {call.lines.map((line, i) => (
            <li key={i}>
              <span className="text-vela-dim">{line.speaker}: </span>
              <span className="text-vela-ink">{line.text}</span>
            </li>
          ))}
        </ul>
      )}

      {call.completeness && <CompletenessPanel completeness={call.completeness} />}

      {call.ended && !call.ended.facts && (
        <div className="mt-1.5 text-sm">
          <p className="text-vela-replan">Sin extraer</p>
          <p className="max-h-20 overflow-auto text-vela-ink italic">«{call.ended.transcript}»</p>
        </div>
      )}

      <CallFacts call={call} />
    </Row>
  )
}

/** Una sola lista de hechos por llamada (REQ-328). Los asertados llevan `kind` y confianza,
 *  que es lo que importa (invariante 8); la extracción cruda solo se enseña si no hubo
 *  ninguno, porque repetiría las mismas claves. */
function CallFacts({ call }: { call: Call }) {
  if (call.facts.length > 0) {
    return (
      <ul className="mt-1.5 text-sm">
        {call.facts.map((fact) => (
          <li
            key={fact.key}
            className={fact.kind === 'observed' ? 'text-vela-ink' : 'text-vela-dim italic'}
          >
            {fact.key} = {factValue(fact.value)} · {pct(fact.confidence)}
            {fact.kind !== 'observed' &&
              ` · ${fact.kind === 'assumed_default' ? 'asumido' : 'inferido'}`}
          </li>
        ))}
      </ul>
    )
  }
  if (!call.ended?.facts) return null
  return (
    <ul className="mt-1.5 text-sm">
      {extracted(call.ended.facts).map(([field, value]) => (
        <li key={field}>
          <span className="text-vela-dim">{field}: </span>
          <span className="text-vela-ink">{value}</span>
        </li>
      ))}
      <li className="text-vela-dim">
        confianza de la extracción · {pct(call.ended.facts.confidence)}
      </li>
    </ul>
  )
}
