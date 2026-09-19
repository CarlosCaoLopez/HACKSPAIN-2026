// El único sitio que habla con el servidor.
//
// Al conectar llega {kind:"snapshot", state, plan, seq} y luego solo
// {kind:"event", event} en orden de seq. Si hay un hueco en seq: GET /api/state y
// reiniciar. Nada de reconciliación fina.
//
// NO dupliquéis belief.apply a mano en TypeScript: reimplementad solo lo que
// haga falta pintar y para lo demás usad los eventos plan.*, que vienen completos.
//
// `../types` lo genera `make types` desde contracts. Nunca se escribe a mano.
import { useEffect, useState } from 'react'

import type { Event, Plan, VelaEvent, WorldState } from '../types'

export interface EventStream {
  state: WorldState | null
  plan: Plan | null
  events: Event[]
  connected: boolean
  /** Socket abierto Y snapshot recibido. No es lo mismo que `connected`: `onopen` llega
   *  antes que el primer frame, y en ese hueco la pantalla no tiene nada que pintar
   *  todavía pero tampoco está vacía de verdad. Es lo que separa el esqueleto de la
   *  reconexión (REQ-202) del estado vacío de un run que aún no ha empezado (REQ-193):
   *  dos cosas distintas que sin esta bandera se ven igual. */
  hydrated: boolean
}

const INERT: EventStream = {
  state: null,
  plan: null,
  events: [],
  connected: false,
  hydrated: false,
}

// --- Los frames del WS ---------------------------------------------------------
//
// El ÚNICO TypeScript escrito a mano del proyecto: estas tres formas no salen de
// `contracts` porque no son modelos Pydantic, son el protocolo del socket. Espejan
// `apps/gateway/src/gateway/ws.py` y no hay un tercer `kind`: el keepalive son los
// ping de protocolo de uvicorn, no un mensaje nuestro.

interface SnapshotFrame {
  kind: 'snapshot'
  state: WorldState | null
  plan: Plan | null
  seq: number
}

interface EventFrame {
  kind: 'event'
  event: Event
}

type WsFrame = SnapshotFrame | EventFrame

/** `GET /api/state` devuelve exactamente un `SnapshotFrame`: un solo parser para el
 *  arranque, el hueco y la reconexión. */
const STATE_URL = '/api/state'

/** Tope de eventos en memoria. Al recortar solo se tiran los dos tipos de alto
 *  volumen: un run de seis minutos trae ~360 ticks y cientos de posiciones, y ningún
 *  panel los necesita en histórico. Un `plan.*`, `call.*`, `action.*` o
 *  `world.fact.asserted` no se pierde nunca: son la historia que cuenta el H3. */
const RING = 2000
const VOLATILE: ReadonlySet<string> = new Set(['world.tick', 'world.unit.position'])

/** La demo no se recarga a mano delante del jurado: reconexión infinita. */
const BACKOFF_MS = [500, 1000, 2000, 4000, 5000]

function socketUrl(url: string): string {
  if (/^wss?:\/\//.test(url)) return url
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const path = url.startsWith('/') ? url : `/${url}`
  // En `pnpm dev` esto sale a :5173 y lo reenvía el proxy de Vite; servido desde el
  // gateway, sale directo a :8000. El hook no sabe en cuál de los dos está.
  return `${proto}//${window.location.host}${path}`
}

function trim(events: Event[]): Event[] {
  if (events.length < RING) return events
  const kept = events.filter((ev) => !VOLATILE.has(ev.type))
  return kept.length >= RING ? kept.slice(kept.length - RING + 1) : kept
}

export function useEventStream(url = '/ws'): EventStream {
  const [stream, setStream] = useState<EventStream>(INERT)

  useEffect(() => {
    if (!url) throw new Error('useEventStream necesita una url')

    // React 19 en StrictMode monta dos veces en desarrollo. Cada efecto tiene su
    // propio socket y su propio `stopped`: el que se desmonta deja de escribir
    // estado y cierra lo suyo, así que no hay dos sockets ni eventos duplicados.
    let stopped = false
    let ws: WebSocket | null = null
    let timer: number | undefined
    let attempt = 0
    let lastSeq = -1

    const applySnapshot = (snap: SnapshotFrame) => {
      lastSeq = snap.seq
      // Se descartan los eventos anteriores a propósito: tras un hueco o una
      // reconexión, el histórico que hay en pantalla ya no es de fiar y enseñar un
      // log incompleto como si fuera completo es peor que no enseñarlo.
      setStream({
        state: snap.state,
        plan: snap.plan,
        events: [],
        connected: true,
        hydrated: true,
      })
    }

    const applyEvent = (ev: Event) => {
      lastSeq = ev.seq
      // El único cast del hook, justo en la frontera con el JSON: a partir de aquí
      // el payload está estrechado por `type` y nadie más castea nada.
      const narrowed = ev as unknown as VelaEvent
      setStream((prev) => ({
        // `plan.emitted` trae el plan completo, así que se guarda tal cual. Es la
        // razón por la que no hace falta reimplementar `belief.apply` aquí.
        plan: narrowed.type === 'plan.emitted' ? narrowed.payload : prev.plan,
        // `state` solo lo pone el snapshot; de los eventos se derivan `seq` y
        // `t_sim` para que la cabecera no mienta, y nada más.
        state: prev.state ? { ...prev.state, seq: ev.seq, t_sim: ev.t_sim } : null,
        events: [...trim(prev.events), ev],
        connected: true,
        // Si llega un evento en orden es que el snapshot ya pasó: un hueco en `seq` no
        // llega aquí, lo desvía `recover()`.
        hydrated: true,
      }))
    }

    /** Hueco en `seq`: el estado de pantalla ya no vale. Se pide entero. */
    const recover = async () => {
      try {
        const res = await fetch(STATE_URL, { cache: 'no-store' })
        const snap = (await res.json()) as SnapshotFrame
        if (!stopped && snap.kind === 'snapshot') applySnapshot(snap)
      } catch {
        // Si /api/state tampoco responde, el servidor está caído: la reconexión
        // traerá un snapshot nuevo. No hay nada que hacer aquí.
      }
    }

    const scheduleReconnect = () => {
      if (stopped) return
      const wait = BACKOFF_MS[Math.min(attempt, BACKOFF_MS.length - 1)]
      attempt += 1
      timer = window.setTimeout(open, wait)
    }

    function open() {
      if (stopped) return
      ws = new WebSocket(socketUrl(url))

      ws.onopen = () => {
        attempt = 0
        // Conectado pero todavía sin snapshot: es el hueco del esqueleto, y dura lo que
        // tarde el servidor en mandar el primer frame.
        if (!stopped) setStream((prev) => ({ ...prev, connected: true, hydrated: false }))
      }

      ws.onmessage = (msg) => {
        if (stopped) return
        let frame: WsFrame
        try {
          frame = JSON.parse(msg.data as string) as WsFrame
        } catch {
          return // un frame ilegible no puede romper el dashboard
        }
        if (frame.kind === 'snapshot') {
          applySnapshot(frame)
          return
        }
        if (frame.kind === 'event') {
          const ev = frame.event
          if (lastSeq >= 0 && ev.seq !== lastSeq + 1) {
            void recover() // hueco: snapshot nuevo y a empezar
            return
          }
          applyEvent(ev)
          return
        }
        // `kind` desconocido (un servidor más nuevo que este dashboard): se ignora.
      }

      ws.onclose = () => {
        if (stopped) return
        // Socket caído: se apaga también el esqueleto. Uno que se queda pulsando mientras
        // el servidor no vuelve se lee como *roto*, que es el fallo que los estados
        // vacíos de REQ-193 existen para evitar. Aquí manda el vacío, y el aviso de la
        // cabecera dice por qué no llega nada.
        setStream((prev) => ({ ...prev, connected: false, hydrated: false }))
        scheduleReconnect()
      }

      // `onerror` siempre viene seguido de `onclose`: la reconexión se programa allí
      // para no programarla dos veces.
      ws.onerror = () => {}
    }

    open()

    return () => {
      stopped = true
      window.clearTimeout(timer)
      ws?.close()
      ws = null
    }
  }, [url])

  return stream
}
