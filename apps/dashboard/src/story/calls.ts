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
//
// Y desde el beat 4:25, un **chat de Telegram es una tarjeta más** (`call_id` `tg_<chat>`):
// el vecino que no supo ubicarse por voz manda el pin, y el pin, el texto y el aviso
// que se le devuelve por el bot se pintan igual que una llamada. Si el `citizen.location`
// declara en `causes` la llamada de voz de la que viene, el pin también cierra el hueco
// `location_hint` de ESA llamada: es el «dónde» que la voz dejó abierto.
//
// **`causes` manda si viene.** Si ningún `cause` resuelve a otra llamada (el backend
// suele apuntar al `call.started` del propio chat), el pin se cuelga de la llamada de
// voz entrante más reciente cuyo `location_hint` no esté observado: es la única lectura
// del beat que no inventa nada, porque el vecino manda el pin justo porque por voz no
// supo decir dónde estaba. Si esa llamada no existe, el pin se queda solo en su chat.
import type {
  CallCompleteness,
  CallFacts,
  CallOutcome,
  CallStarted,
  CitizenLocation,
  Event,
  FactAsserted,
  VelaEvent,
} from '../types'
import { factValue, shortId } from './format'

export interface Line {
  speaker: string
  text: string
  /** `signal`: lo que el core mandó decir (`call.signal.sent`), no lo que se transcribió.
   *  `pin`: la ubicación que mandó el vecino (`citizen.location`), contada en una línea. */
  kind?: 'signal' | 'pin'
}

/** El pin del vecino tal cual llegó: `(x, z)` del mundo si el core lo proyectó, y el
 *  POI al que se ancló si cayó dentro de `snap_m`. Sin `poi_id` es un pin «sin anclar». */
export interface Pin {
  x: number | null
  z: number | null
  poiId: string | null
  poiName: string | null
  text: string | null
  live: boolean
  t_sim: number
}

/** De dónde sale la ubicación cuando NO la dio Jev en la llamada: un pin de Telegram
 *  anclado a un POI, o un hecho `poi:<id>:confirmed` observado. Solo `observed` puede
 *  poner un hueco en sólido (invariante 8): un asumido nunca entra aquí. */
export interface Located {
  poiId: string
  via: 'telegram' | 'fact'
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
  /** La entrante viene del móvil que el visitante de la /demo declaró como «vecino»:
   *  la tarjeta dice «tu llamada». Un journal anterior al campo no lo trae: `false`. */
  knownCaller: boolean
  /** `voice` salvo que lo diga `call.started` o que la tarjeta la abra un pin de Telegram. */
  channel: CallStarted['channel']
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
  /** Pins de Telegram recibidos en este chat (o colgados de esta llamada). */
  pins: Pin[]
  /** La ubicación que cerró el hueco de `location_hint` fuera de Jev, si la hubo. */
  located: Located | null
  /** La llamada de voz de la que viene este chat de Telegram: la que declaró `causes` o,
   *  si no declaró ninguna, la entrante más reciente sin `location_hint` observado. */
  afterCall: string | null
  /** El texto que se enseña como destinatario. Para un chat de Telegram NUNCA es el
   *  `chat_id` (no le dice nada a nadie y es un dato personal): es el POI del último
   *  pin o «vecino (Telegram)». */
  recipient: string
}

export function callCards(events: Event[]): Call[] {
  const byId = new Map<string, Call>()
  // `call.requested` no trae `call_id` —la llamada aún no existe—, así que la intención
  // se guarda por `task_id` y la recoge el `call.started` que venga con ese task.
  const requested = new Map<string, { intent: string; urgency: string; poi_id: string }>()

  // `seq` del `call.started` → `call_id`: es lo que permite que un `citizen.location`
  // con `causes: [seq]` se cuelgue de la llamada de voz que lo motivó.
  const startedAt = new Map<number, string>()

  const get = (callId: string, seq: number, t_sim: number): Call => {
    const existing = byId.get(callId)
    if (existing) return existing
    const fresh: Call = {
      callId,
      seq,
      t_sim,
      direction: null,
      knownCaller: false,
      channel: 'voice',
      to: '',
      intent: '',
      urgency: '',
      poiId: '',
      lines: [],
      ended: null,
      facts: [],
      completeness: null,
      pins: [],
      located: null,
      afterCall: null,
      recipient: '',
    }
    byId.set(callId, fresh)
    return fresh
  }

  /** ¿Sabe la llamada dónde está quien llama? Solo cuenta un `observed` de Jev: un
   *  `assumed_default` es una hipótesis y un chat sin vector de Jev no sabe nada. */
  const locationObserved = (call: Call): boolean =>
    call.completeness?.fields.some((f) => f.key === 'location_hint' && f.status === 'observed') ??
    false

  /** La llamada de voz de la que sale un pin cuando `causes` no lo dice: la entrante más
   *  reciente (mayor `seq`) que aún no tenga ubicación. `direction` desconocida cuenta
   *  como entrante: una orden del agente siempre trae `call.started` con su tarea, así
   *  que una tarjeta sin dirección es una llamada que entró por otra vía (plan B). */
  const voiceWithoutLocation = (): Call | null => {
    let best: Call | null = null
    for (const c of byId.values()) {
      if (c.channel === 'telegram' || c.direction === 'outbound') continue
      if (locationObserved(c) || c.located) continue
      if (!best || c.seq > best.seq) best = c
    }
    return best
  }

  /** Una línea nueva, salvo que repita la última palabra por palabra: el `call.signal.sent`
   *  y el `call.transcript.partial` del agente traen el mismo texto (uno es la orden,
   *  el otro lo que se dijo), y en pantalla es UNA frase. */
  const say = (call: Call, line: Line) => {
    const last = call.lines[call.lines.length - 1]
    if (last && last.text === line.text) return
    call.lines.push(line)
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
        startedAt.set(ev.seq, ev.payload.call_id)
        call.direction = ev.payload.direction
        call.knownCaller = ev.payload.known_caller ?? false
        // Un journal anterior al canal no trae `channel`: era voz.
        call.channel = ev.payload.channel ?? 'voice'
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
        say(call, { speaker: ev.payload.speaker, text: ev.payload.text })
        break
      }

      case 'call.signal.sent': {
        // El aviso que el core manda decir (`unit_dispatched`…) se pinta como una línea
        // del agente, venga por voz o por Telegram. Sin `message` no hay nada que leer.
        if (!ev.payload.message) break
        const call = get(ev.payload.call_id, ev.seq, ev.t_sim)
        if (call.callId.startsWith('tg_')) {
          call.channel = 'telegram'
          call.direction ??= 'inbound'
        }
        say(call, { speaker: 'agent', text: ev.payload.message, kind: 'signal' })
        break
      }

      case 'citizen.location': {
        const call = get(ev.payload.call_id, ev.seq, ev.t_sim)
        const p = ev.payload
        call.channel = 'telegram'
        call.direction ??= 'inbound'
        if (!call.to) call.to = `chat ${p.chat_id}`
        const pin = pinOf(p, ev.t_sim)
        call.pins.push(pin)
        if (p.text) say(call, { speaker: 'vecino', text: p.text })
        // El pin es una línea más de la conversación: es lo que el vecino «dijo».
        const pinLine: Line = { speaker: 'vecino', text: pinText(pin), kind: 'pin' }
        say(call, pinLine)
        if (p.poi_id) call.located ??= { poiId: p.poi_id, via: 'telegram' }
        // La llamada de voz que motivó el pin: primero lo que diga `causes`; si no
        // resuelve a OTRA llamada, la heurística de arriba. Un chat que ya se colgó
        // de una llamada no cambia de llamada con un segundo pin (`live`).
        let origin: string | null = call.afterCall
        if (!origin) {
          for (const seq of envelope.causes ?? []) {
            const started = startedAt.get(seq)
            if (started && started !== call.callId) {
              origin = started
              break
            }
          }
        }
        if (!origin) origin = voiceWithoutLocation()?.callId ?? null
        if (origin) {
          call.afterCall = origin
          const voice = byId.get(origin)
          if (voice) {
            voice.pins.push(pin)
            say(voice, { speaker: 'vecino (Telegram)', text: pinText(pin), kind: 'pin' })
            if (p.poi_id) voice.located ??= { poiId: p.poi_id, via: 'telegram' }
          }
        }
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
        const { source, key, value, confidence, kind, call_id } = ev.payload
        // La procedencia va en `source` (`call:<id>`); `call_id` es el mismo dato
        // explícito y vale como respaldo cuando el source es otro (`telegram`).
        const id = source.startsWith('call:') ? source.slice('call:'.length) : call_id
        const call = id ? byId.get(id) : undefined
        if (!call) break
        // Un journal anterior a Jev no trae `kind`: eran observados.
        const k = kind ?? 'observed'
        call.facts.push({ key, value, confidence, kind: k })
        // `poi:<id>:confirmed` observado ubica al que llama en ese POI: es la otra
        // forma de cerrar `location_hint` sin Jev. Solo observado (invariante 8).
        const confirmed = /^poi:([^:]+):confirmed$/.exec(key)
        if (confirmed?.[1] && k === 'observed' && value === true) {
          call.located ??= { poiId: confirmed[1], via: 'fact' }
        }
        break
      }

      default:
        break
    }
  }

  for (const call of byId.values()) call.recipient = recipientOf(call)

  // En curso arriba: es la que está pasando y la que hay que mirar.
  return [...byId.values()].sort((a, b) => {
    if (!a.ended !== !b.ended) return a.ended ? 1 : -1
    return b.seq - a.seq
  })
}

function pinOf(p: CitizenLocation, t_sim: number): Pin {
  return {
    x: p.x ?? null,
    z: p.z ?? null,
    poiId: p.poi_id ?? null,
    poiName: p.poi_name ?? null,
    text: p.text ?? null,
    live: p.live,
    t_sim,
  }
}

/** El pin en una frase: el POI al que se ancló o «sin anclar» con las coordenadas
 *  reales. `(x, z)` no se dice: es de la maqueta, no del vecino. */
function pinText(pin: Pin): string {
  const where = pin.poiName ? `junto a ${pin.poiName}` : 'sin anclar a ningún pueblo'
  return `ubicación ${where}${pin.live ? ' · en vivo' : ''}`
}

/** A quién va la tarjeta. Voz: el número que dijo `call.started` (o el id si no llegó).
 *  Telegram: el POI del último pin anclado o «vecino (Telegram)»; el `chat_id` no se
 *  pinta nunca. */
function recipientOf(call: Call): string {
  if (call.channel === 'telegram') {
    const anchored = [...call.pins].reverse().find((p) => p.poiName)
    return anchored?.poiName ? `vecino · ${anchored.poiName}` : 'vecino (Telegram)'
  }
  return call.to || shortId(call.callId)
}

/** Una llamada «en curso» es una de voz sin `call.ended`. Un chat de Telegram no cuelga
 *  nunca, así que no cuenta: si contara, el contador diría «1 en curso» toda la demo. */
export function inProgress(call: Call): boolean {
  return call.ended === null && call.channel !== 'telegram'
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
