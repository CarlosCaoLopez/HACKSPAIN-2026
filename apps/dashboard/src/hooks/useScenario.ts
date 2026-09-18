// La capa estática del mapa: geometría que no viaja por eventos.
//
// `world.road.changed` trae un `edge_id` y nada más, y una celda necesita `origin` y
// `cell_size` para proyectarse. Eso vive en el escenario, así que se pide por HTTP una
// vez por run y se cachea: el dashboard no hace polling de nada.
import { useEffect, useMemo, useState } from 'react'

import type { Event, Scenario, VelaEvent } from '../types'

/** La respuesta de `GET /api/scenario`: el escenario tal cual lo genera `make types`,
 *  más lo que el gateway añade para ser honesto sobre lo que es inventado. */
export interface ScenarioLayer extends Scenario {
  /** Alguna lista la ha rellenado `gateway/scenario_fallback.py` porque el YAML de P2
   *  la tiene vacía. El mapa lo avisa en pantalla: una geometría de prueba presentada
   *  como real se cae en cuanto alguien pregunta por el pueblo. */
  provisional: boolean
  provisional_lists: string[]
  of_run: string | null
}

const URL = '/api/scenario'

/** Caché de proceso: con dos escenarios y una demo de seis minutos, un Map basta. Si
 *  hiciera falta invalidar, el `run_id` ya viene en la respuesta. */
const cache = new Map<string, ScenarioLayer>()

export function useScenario(scenarioId: string | null): ScenarioLayer | null {
  const key = scenarioId ?? '@current'
  const [layer, setLayer] = useState<ScenarioLayer | null>(() => cache.get(key) ?? null)

  useEffect(() => {
    const cached = cache.get(key)
    if (cached) {
      setLayer(cached)
      return
    }
    let stopped = false
    const url = scenarioId ? `${URL}/${scenarioId}` : URL
    fetch(url, { cache: 'no-store' })
      .then((res) => (res.ok ? (res.json() as Promise<ScenarioLayer>) : null))
      .then((body) => {
        if (stopped || !body) return
        cache.set(key, body)
        setLayer(body)
      })
      .catch(() => {
        // Sin capa, el mapa pinta lo que puede (unidades y celdas necesitan `origin`,
        // así que en la práctica se queda vacío y lo dice). No se reintenta en bucle:
        // al reconectar el WS se vuelve a montar el hook.
      })
    return () => {
      stopped = true
    }
  }, [key, scenarioId])

  return layer
}

/** El escenario del run que se está viendo, sacado del último `run.started`.
 *
 *  Sirve para que un run nuevo con otro escenario traiga su geometría en vez de seguir
 *  con la cacheada. Si no ha pasado ningún `run.started` por la ventana de eventos,
 *  `null` y `GET /api/scenario` decide (el del run en curso, o el por defecto). */
export function useScenarioId(events: Event[]): string | null {
  return useMemo(() => {
    for (let i = events.length - 1; i >= 0; i -= 1) {
      const envelope = events[i]
      if (!envelope || envelope.type !== 'run.started') continue
      const ev = envelope as unknown as VelaEvent
      if (ev.type === 'run.started') return ev.payload.scenario_id
    }
    return null
  }, [events])
}
