// Una tarjeta por llamada, montada con los cuatro eventos de telefonía.
//
// Lógica pura, como `actions.ts`: se prueba pasándole el fixture entero.
//
// Dos cosas que este montaje tiene que hacer bien porque son criterio del reto:
//
// - **Ningún hecho sin procedencia.** Los `world.fact.asserted` cuyo `source` es
//   `call:<id>` se cuelgan de su llamada. La procedencia es parte del contrato del
//   hecho, así que no hace falta resolver `causes` para esto.
// - **`facts: null` es un caso normal**, no un error: la extracción falla o tarda más
//   de la cuenta. Entonces lo único que hay es lo que se dijo, y se enseña.
import type { CallCompleteness, CallFacts, CallOutcome, Event, FactAsserted, VelaEvent } from '../types'
import { factValue } from './format'

export interface Line {
  speaker: string
  text: string
}

export interface Ended {
  outcome: CallOutcome
  transcript: string
  /** `null` = no se extrajo nada. La tarjeta lo dice y enseña la transcripción. */
  facts: CallFacts | null
  t_sim: number
}

export interface Call {
  callId: string
  seq: number
  t_sim: number
  direction: 'inbound' | 'outbound' | null
  to: string
  intent: string
  urgency: string
  poiId: string
  lines: Line[]
  ended: Ended | null
  /** Hechos que entraron al estado con esta llamada como `source`. `kind` es la regla 4:
   *  un hecho asumido nunca se disfraza de observado. */
  facts: {
    key: string
    value: string | number | boolean
    confidence: number
    kind: FactAsserted['kind']
  }[]
  /** El último vector de completitud de Jev (`call.completeness`), o `null` si la
   *  llamada no pasó por Jev (mock, plan B). */
  completeness: CallCompleteness | null
}

export function callCards(events: Event[]): Call[] {
  const byId = new Map<string, Call>()
  // `call.requested` no trae `call_id` —la llamada aún no existe—, así que la intención
  // se guarda por `task_id` y la recoge el `call.started` que venga con ese task.
  const requested = new Map<string, { intent: string; urgency: string; poi_id: string }>()

  const get = (callId: string, seq: number, t_sim: number): Call => {
    const existing = byId.get(callId)
    if (existing) return existing
    const fresh: Call = {
      callId,
      seq,
      t_sim,
      direction: null,
      to: '',
      intent: '',
      urgency: '',
      poiId: '',
      lines: [],
      ended: null,
      facts: [],
      completeness: null,
    }
    byId.set(callId, fresh)
    return fresh
  }

  for (const envelope of events) {
    const ev = envelope as unknown as VelaEvent

    switch (ev.type) {
      case 'call.requested': {
        const { task_id, intent, urgency, poi_id } = ev.payload
        requested.set(task_id, { intent, urgency, poi_id })
        break
      }

      case 'call.started': {
        const call = get(ev.payload.call_id, ev.seq, ev.t_sim)
        call.direction = ev.payload.direction
        call.to = ev.payload.to
        const asked = ev.payload.task_id ? requested.get(ev.payload.task_id) : undefined
        if (asked) {
          call.intent = asked.intent
          call.urgency = asked.urgency
          call.poiId = asked.poi_id
        }
        break
      }

      case 'call.transcript.partial': {
        const call = get(ev.payload.call_id, ev.seq, ev.t_sim)
        call.lines.push({ speaker: ev.payload.speaker, text: ev.payload.text })
        break
      }

      case 'call.ended': {
        const call = get(ev.payload.call_id, ev.seq, ev.t_sim)
        call.direction = ev.payload.direction
        call.ended = {
          outcome: ev.payload.outcome,
          transcript: ev.payload.transcript,
          // El campo es opcional además de anulable: ausente y `null` son lo mismo
          // aquí — no se extrajo nada.
          facts: ev.payload.facts ?? null,
          t_sim: ev.t_sim,
        }
        break
      }

      case 'call.completeness': {
        // Cada tick sustituye al anterior: lo que se pinta es el estado de ahora.
        get(ev.payload.call_id, ev.seq, ev.t_sim).completeness = ev.payload
        break
      }

      case 'world.fact.asserted': {
        const { source, key, value, confidence, kind } = ev.payload
        if (!source.startsWith('call:')) break
        const call = byId.get(source.slice('call:'.length))
        // Un journal anterior a Jev no trae `kind`: eran observados.
        if (call) call.facts.push({ key, value, confidence, kind: kind ?? 'observed' })
        break
      }

      default:
        break
    }
  }

  // En curso arriba: es la que está pasando y la que hay que mirar.
  return [...byId.values()].sort((a, b) => {
    if (!a.ended !== !b.ended) return a.ended ? 1 : -1
    return b.seq - a.seq
  })
}

/** Los campos no nulos de `CallFacts`, ya legibles. Un campo nulo no se pinta: es
 *  ausencia de información, no información. `confidence` va aparte, con su etiqueta. */
export function extracted(facts: CallFacts): [string, string][] {
  const out: [string, string][] = []
  for (const [field, value] of Object.entries(facts)) {
    if (value === null || value === undefined) continue
    if (field === 'confidence') continue
    if (field === 'contradicts_known' && value === false) continue
    out.push([field.replace(/_/g, ' '), factValue(value as string | number | boolean)])
  }
  return out
}
