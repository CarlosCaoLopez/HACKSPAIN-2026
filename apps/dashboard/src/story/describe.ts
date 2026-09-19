// Un evento → una frase que se entienda sin saber qué es un `EventType`.
//
// Es un `switch` exhaustivo sobre `VelaEvent`, la unión discriminada que genera
// `make types`: cada rama recibe su payload ya estrechado, sin un solo cast. El
// `assertNever` del final es lo que convierte "P1 ha añadido un tipo de evento" en un
// error de compilación en vez de en una fila vacía que nadie ve hasta la demo.
//
// Regla del panel: aquí no se pinta JSON. Si un payload no se puede contar en una
// frase, es que la frase está mal pensada, no que haga falta un `<pre>`.
import type { Event, VelaEvent } from '../types'
import { CALL_OUTCOME, CELL_CAUSE, CELL_STATE, CIV_STATE, OVERRIDE_KIND, UNIT_STATUS } from './labels'
import { factValue, pct, seconds, shortId } from './format'

/** `tone` decide el color. `replan` es el ÚNICO que usa el rojo: está reservado y si
 *  algo más lo usa, el banner del H4 deja de significar nada. */
export type Tone = 'replan' | 'decision' | 'fact' | 'world' | 'call' | 'human' | 'error' | 'muted'

export interface Described {
  label: string
  sentence: string
  tone: Tone
}

/** El sobre del WS llega como `Event` (payload sin estrechar). Este es el único punto
 *  donde se estrecha a la unión, y a partir de aquí nadie castea nada. */
export function describe(ev: Event): Described {
  return describeNarrowed(ev as unknown as VelaEvent)
}

function describeNarrowed(ev: VelaEvent): Described {
  switch (ev.type) {
    // --- la historia que cuenta el panel ---------------------------------------
    case 'plan.replan.started':
      return {
        label: 'REPLAN',
        tone: 'replan',
        sentence: ev.payload.reason,
      }

    case 'plan.policy.emitted':
      return {
        label: 'POLÍTICA',
        tone: 'decision',
        sentence: ev.payload.rationale,
      }

    case 'plan.violation':
      return {
        label: 'VIOLACIÓN',
        tone: 'error',
        sentence: `${ev.payload.message} · ${ev.payload.verifier}`,
      }

    case 'plan.emitted': {
      const { assignments, unassigned_tasks } = ev.payload
      const sinCubrir = unassigned_tasks.length
        ? ` · ${unassigned_tasks.length} sin cubrir`
        : ''
      return {
        label: 'PLAN NUEVO',
        tone: 'decision',
        sentence: `${assignments.length} asignaciones${sinCubrir}`,
      }
    }

    case 'task.changed': {
      const { task } = ev.payload
      const target = task.target_poi ?? task.target_cell
      return {
        label: task.done ? 'TAREA ✓' : 'TAREA',
        tone: 'decision',
        sentence: `${task.kind}${target ? ` · ${shortId(target)}` : ''} · ${task.severity}`,
      }
    }

    case 'plan.divergence':
      return {
        label: 'DIVERGENCIA',
        tone: ev.payload.broken.length ? 'error' : 'muted',
        sentence: ev.payload.broken.length
          ? `${ev.payload.value.toFixed(2)} · roto: ${ev.payload.broken.join(', ')}`
          : ev.payload.value.toFixed(2),
      }

    case 'world.fact.asserted': {
      const { key, value, confidence, source } = ev.payload
      return {
        label: 'HECHO',
        tone: 'fact',
        // Clave, valor, confianza y procedencia SIEMPRE juntos: ningún hecho aparece
        // en pantalla sin decir de qué llamada viene.
        sentence: `${key} = ${factValue(value)} · ${pct(confidence)} · ${source}`,
      }
    }

    case 'human.override':
      return {
        label: 'HUMANO',
        tone: 'human',
        sentence: `${OVERRIDE_KIND[ev.payload.kind]} · ${ev.payload.target}${
          ev.payload.note ? ` · «${ev.payload.note}»` : ''
        }`,
      }

    // --- el mundo --------------------------------------------------------------
    case 'world.fire.detected':
      return {
        label: 'FUEGO',
        tone: 'error',
        sentence: `ignición en ${ev.payload.cell_id} · ${ev.payload.hazard}`,
      }

    case 'world.road.changed':
      return {
        label: ev.payload.cut ? 'CARRETERA CORTADA' : 'CARRETERA ABIERTA',
        tone: 'world',
        sentence: `${ev.payload.edge_id}${ev.payload.cause ? ` · ${ev.payload.cause}` : ''}`,
      }

    case 'world.inject':
      return {
        label: 'INJECT',
        tone: 'world',
        sentence: `${ev.payload.inject_type.replace(/_/g, ' ')}${detail(ev.payload.detail)}`,
      }

    case 'world.unit.status':
      return {
        label: 'UNIDAD',
        tone: ev.payload.status === 'unavailable' ? 'error' : 'world',
        sentence: `${shortId(ev.payload.unit_id)} · ${UNIT_STATUS[ev.payload.status]} · ${
          ev.payload.reason
        }`,
      }

    case 'world.civilians.changed':
      return {
        label: 'CIVILES',
        tone: 'world',
        sentence: `${ev.payload.count} en ${shortId(ev.payload.poi_id)} · ${
          CIV_STATE[ev.payload.state]
        }`,
      }

    case 'world.cell.changed': {
      const { cell_id, state, cause } = ev.payload
      // Una celda apagada es el efecto de una orden nuestra (un camión en alcance), no
      // el fuego haciendo lo suyo: etiqueta propia y la causa en la frase.
      if (cause === 'extinguished') {
        return {
          label: 'CELDA APAGADA',
          tone: 'decision',
          sentence: `celda ${CELL_CAUSE.extinguished} · ${cell_id}`,
        }
      }
      return {
        label: 'CELDA',
        tone: 'world',
        sentence: `${cell_id} · ${CELL_STATE[state]}${cause ? ` · ${CELL_CAUSE[cause]}` : ''}`,
      }
    }

    // --- telefonía -------------------------------------------------------------
    case 'call.requested':
      return {
        label: 'LLAMADA PEDIDA',
        tone: 'call',
        sentence: `${ev.payload.intent.replace(/_/g, ' ')} a ${shortId(ev.payload.poi_id)}`,
      }

    case 'call.started': {
      // El canal se dice: un aviso por Telegram no es una llamada de voz, y en el
      // pitch la diferencia es el beat entero («la voz da el qué, Telegram el dónde»).
      // Por Telegram no se pinta ni `to` ni `call_id`: los dos SON el `chat_id`, que
      // no le dice nada a quien mira y es un dato personal del vecino.
      const label = ev.payload.direction === 'inbound' ? 'AVISO DEL VECINO' : 'ORDEN DEL AGENTE'
      if (ev.payload.channel === 'telegram') {
        return { label, tone: 'call', sentence: 'vecino por Telegram · chat abierto' }
      }
      return {
        label,
        tone: 'call',
        sentence: `${ev.payload.call_id} · teléfono ${ev.payload.to}`,
      }
    }

    case 'citizen.location': {
      // El pin GPS del vecino, ya anclado (o no) a un POI por el core. `sin anclar` se
      // dice en voz alta: un pin lejos de todo es información, no un fallo que esconder.
      // Sin `call_id`: es el `chat_id`, y la frase ya dice quién y por dónde.
      const { poi_name, text, live } = ev.payload
      const donde = poi_name
        ? `el vecino manda su ubicación: junto a ${poi_name}`
        : 'el vecino manda su ubicación: sin anclar a ningún pueblo'
      return {
        label: 'TELEGRAM',
        tone: 'call',
        sentence: `${donde}${live ? ' · en vivo' : ''}${text ? ` · «${text}»` : ''}`,
      }
    }

    case 'call.ended': {
      const { call_id, outcome, facts, transcript } = ev.payload
      // `facts: null` pasa de verdad (la extracción falla o tarda más de 4 s) y no
      // puede dejar la fila vacía: se enseña la transcripción y se marca sin extraer.
      const extra = facts
        ? ` · ${pct(facts.confidence)}`
        : ` · SIN EXTRAER · «${transcript.slice(0, 60)}…»`
      return {
        label: 'LLAMADA FIN',
        tone: 'call',
        sentence: `${call_id} · ${CALL_OUTCOME[outcome]}${extra}`,
      }
    }

    case 'call.transcript.partial':
      return {
        label: 'TRANSCRIPCIÓN',
        tone: 'call',
        sentence: `${ev.payload.speaker}: ${ev.payload.text}`,
      }

    // --- voz en vivo de P3 (SPEC-006 · fixture v3) ---------------------------------
    case 'call.affect': {
      // Lo que Humalike lee del interlocutor en mitad de la llamada. Son ambiente, no un
      // cambio: no entra en el panel de cambios (significant.ts), pero la frase existe
      // porque el tipo está en el catálogo y `describe` los cubre todos.
      const emotions = ev.payload.emotions
        .map((e) => `${EMOTION[e.type] ?? e.type} ${pct(e.intensity)}`)
        .join(' · ')
      return {
        label: 'ÁNIMO',
        tone: 'call',
        sentence: `${ev.payload.call_id} · ${emotions || 'sin lectura'}`,
      }
    }

    case 'call.completeness': {
      // Ambiente, como `call.affect`: un tick cada 5 s no es un cambio del mundo. Lo
      // pinta `CompletenessPanel` dentro de la tarjeta de la llamada.
      const { fields, call_id } = ev.payload
      const done = fields.filter((f) => f.status === 'observed').length
      const assumed = fields.filter((f) => f.status === 'assumed_default').length
      return {
        label: 'COMPLETITUD',
        tone: 'call',
        sentence: `${call_id} · ${done}/${fields.length} observados${
          assumed ? ` · ${assumed} asumidos` : ''
        }`,
      }
    }

    case 'call.signal.requested':
      return {
        label: 'SEÑAL PEDIDA',
        tone: 'decision',
        sentence: `${SIGNAL[ev.payload.key] ?? ev.payload.key} → ${ev.payload.call_id}`,
      }

    case 'call.signal.sent':
      return {
        label: 'SEÑAL ENVIADA',
        tone: 'call',
        // Lo que se le pidió decir al agente y cuánto tardó: es la latencia del pitch.
        sentence: `${ev.payload.message ?? SIGNAL[ev.payload.key] ?? ev.payload.key}${
          ev.payload.latency_ms != null
            ? ` · ${(ev.payload.latency_ms / 1000).toFixed(1).replace('.', ',')} s`
            : ''
        }`,
      }

    // --- acciones (las pinta el ActionLog del H4, la frase ya está aquí) --------
    case 'action.requested':
      return {
        label: 'ORDEN',
        tone: 'decision',
        sentence: `${ev.payload.verb} ${args(ev.payload.args)}`,
      }

    case 'action.completed':
      return { label: 'HECHA', tone: 'muted', sentence: ev.payload.action_id }

    case 'action.failed':
      return {
        label: 'FALLÓ',
        tone: 'error',
        sentence: `${ev.payload.action_id} · ${ev.payload.error}`,
      }

    // --- posición y latido: el mapa los usa, la historia no --------------------
    case 'world.unit.position':
      return {
        label: 'POSICIÓN',
        tone: 'muted',
        sentence: `${shortId(ev.payload.unit_id)} · ${ev.payload.x.toFixed(0)}, ${ev.payload.z.toFixed(0)}`,
      }

    case 'world.unit.arrived':
      return {
        label: 'LLEGADA',
        tone: 'world',
        sentence: `${shortId(ev.payload.unit_id)} en ${ev.payload.waypoint_id}`,
      }

    case 'world.tick':
      return {
        label: 'TICK',
        tone: 'muted',
        sentence: `viento ${ev.payload.wind.bearing_deg}° · ${ev.payload.wind.speed}`,
      }

    // --- run y errores ---------------------------------------------------------
    case 'run.started':
      return { label: 'RUN', tone: 'muted', sentence: `arranca ${ev.payload.scenario_id}` }

    case 'run.ended':
      return {
        label: 'RUN',
        tone: 'muted',
        sentence: `termina ${ev.payload.scenario_id}${
          ev.payload.score != null ? ` · ${ev.payload.score.toFixed(2)}` : ''
        }`,
      }

    case 'event.malformed':
      return {
        label: 'MALFORMADO',
        tone: 'error',
        sentence: `${ev.payload.type} · ${ev.payload.error}`,
      }

    default:
      return assertNever(ev)
  }
}

/** Las emociones y señales que hoy emite P3, en castellano. Claves abiertas a propósito
 *  (`string`, no `Literal`): el contrato las deja libres, y una desconocida se enseña tal
 *  cual en vez de romper la fila. */
const EMOTION: Record<string, string> = {
  fear: 'miedo',
  frustration: 'frustración',
  relief: 'alivio',
  anger: 'enfado',
  calm: 'calma',
}
const SIGNAL: Record<string, string> = {
  unit_dispatched: 'unidad en camino',
  coach: 'consejo al agente',
}

/** Un tipo de evento nuevo en `contracts` rompe la compilación aquí. Es el objetivo. */
function assertNever(ev: never): never {
  throw new Error(`tipo de evento sin describir: ${JSON.stringify(ev)}`)
}

function detail(d: Record<string, unknown>): string {
  const parts = Object.entries(d).map(([k, v]) => `${k} ${String(v)}`)
  return parts.length ? ` · ${parts.join(', ')}` : ''
}

function args(a: Record<string, unknown>): string {
  const unit = typeof a.unit_id === 'string' ? shortId(a.unit_id) : null
  const to = typeof a.to === 'string' ? a.to : null
  if (unit && to) return `${unit} → ${to}`
  const eta = typeof a.eta_s === 'number' ? ` (${seconds(a.eta_s)})` : ''
  return `${Object.entries(a)
    .map(([k, v]) => `${k} ${String(v)}`)
    .join(', ')}${eta}`
}
