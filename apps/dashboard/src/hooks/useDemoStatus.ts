// La /demo autoservicio, vista desde el dashboard: ¿hay run, cuánto le queda, dónde
// está el vídeo del Minecraft y a qué número llama el vecino? `GET /api/demo/status`.
//
// Sí hace polling, y es la excepción consciente: es lo que decide si la pantalla enseña
// la sala de espera o el run, y un `run.started` por el WS no basta —el visitante
// puede abrir la página antes de pulsar «empezar» en la landing, o después de que el
// run haya acabado y el socket ya no traiga nada—. Cada 5 s a un endpoint que no lee
// ficheros: no pesa.
import { useEffect, useState } from 'react'

/** Lo que devuelve `apps/gateway/src/gateway/main.py::get_demo_status`. */
export interface DemoStatus {
  busy: boolean
  run_id: string | null
  scenario_id: string | null
  started_at: string | null
  ends_in_s: number | null
  run_max_s: number
  inbound_number: string
  watch_url: string
  landing_url: string
  /** Vacío = no hay cámara configurada (`VELA_CAM_PLAYER`): la vista Minecraft lo dice. */
  cam_hls_url: string
  minecraft: boolean | null
  calls: 'reales' | 'simuladas' | null
  phone_roles: { key: string; label: string; explica: string }[]
  default_scenario: string
}

const URL = '/api/demo/status'
const EVERY_MS = 5000

export function useDemoStatus(): DemoStatus | null {
  const [status, setStatus] = useState<DemoStatus | null>(null)

  useEffect(() => {
    let stopped = false
    const tick = () => {
      fetch(URL, { cache: 'no-store' })
        .then((res) => (res.ok ? (res.json() as Promise<DemoStatus>) : null))
        .then((body) => {
          if (!stopped && body) setStatus(body)
        })
        .catch(() => {
          // Sin estado no se decide nada: la pantalla sigue como estaba. Un gateway en
          // replay (`make dev-dash`) tampoco tiene por qué contestar a esto.
        })
    }
    tick()
    const timer = window.setInterval(tick, EVERY_MS)
    return () => {
      stopped = true
      window.clearInterval(timer)
    }
  }, [])

  return status
}

/** Cuenta atrás local entre dos polls: el servidor manda `ends_in_s` cada 5 s y aquí se
 *  resta un segundo cada segundo para que el reloj no dé saltos. */
export function useCountdown(endsIn: number | null): number | null {
  const [left, setLeft] = useState<number | null>(endsIn)
  useEffect(() => {
    setLeft(endsIn)
    if (endsIn === null) return
    const timer = window.setInterval(() => setLeft((v) => (v === null ? null : Math.max(0, v - 1))), 1000)
    return () => window.clearInterval(timer)
  }, [endsIn])
  return left
}
