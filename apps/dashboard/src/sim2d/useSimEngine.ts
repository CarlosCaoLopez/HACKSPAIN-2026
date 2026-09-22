// El motor, visto desde React.
//
// Contrato de rendimiento, que es el mismo que el del mapa real (REQ-227): **React
// repinta UNA vez por tick de simulación**, nunca por fotograma. Lo que se mueve entre
// dos ticks lo interpola el navegador con `transition`, no un `requestAnimationFrame`.
//
// El multiplicador de velocidad cambia el intervalo de PARED, no el `dt`: así el mismo
// número de ticks produce siempre el mismo run, vaya a ×0,5 o a ×4. Es lo que permite
// enseñar dos veces lo mismo en un ensayo.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import type { Scenario, Wind } from '../types'

import scenarioJson from './engine/wildfire_ridge.json'
import { SimEngine, TICK_S, type Frame } from './engine/loop'
import { toWorldView, type WorldView } from './worldview'

export const SCENARIO = scenarioJson as unknown as Scenario

export const SPEEDS = [0.5, 1, 2, 4] as const
export type Speed = (typeof SPEEDS)[number]

export interface SimControls {
  play(): void
  pause(): void
  toggle(): void
  reset(): void
  setSpeed(s: Speed): void
  ignite(cellId: string): void
  douse(cellId: string): void
  setWind(wind: Wind): void
  setAnswersPhone(v: boolean): void
  inject(type: string, payload: Record<string, unknown>): void
  step(): void
}

export interface Sim {
  frame: Frame
  view: WorldView
  scenario: Scenario
  speed: Speed
  running: boolean
  answersPhone: boolean
  controls: SimControls
  headingOf(unitId: string): number
}

export function useSimEngine(): Sim {
  const engineRef = useRef<SimEngine | null>(null)
  if (engineRef.current === null) engineRef.current = new SimEngine(SCENARIO)
  const engine = engineRef.current

  const [frame, setFrame] = useState<Frame>(() => engine.frame())
  const [speed, setSpeed] = useState<Speed>(1)
  const [running, setRunning] = useState(false)
  const [answersPhone, setAnswers] = useState(true)

  const publish = useCallback(() => {
    engine.running = running
    setFrame(engine.frame())
  }, [engine, running])

  useEffect(() => {
    if (!running) return
    const id = window.setInterval(() => {
      engine.tick(TICK_S)
      setFrame(engine.frame())
    }, 1000 / speed)
    return () => window.clearInterval(id)
  }, [engine, running, speed])

  const controls = useMemo<SimControls>(
    () => ({
      play: () => setRunning(true),
      pause: () => setRunning(false),
      toggle: () => setRunning((v) => !v),
      reset: () => {
        setRunning(false)
        engine.reset()
        setFrame(engine.frame())
      },
      setSpeed: (s) => setSpeed(s),
      ignite: (cellId) => {
        engine.ignite(cellId)
        setFrame(engine.frame())
      },
      douse: (cellId) => {
        engine.douse(cellId)
        setFrame(engine.frame())
      },
      setWind: (wind) => {
        engine.setWind(wind)
        setFrame(engine.frame())
      },
      setAnswersPhone: (v) => {
        engine.answersPhone = v
        setAnswers(v)
        setFrame(engine.frame())
      },
      inject: (type, payload) => {
        engine.applyInject(type, payload)
        setFrame(engine.frame())
      },
      step: () => {
        engine.tick(TICK_S)
        setFrame(engine.frame())
      },
    }),
    [engine],
  )

  useEffect(() => {
    publish()
  }, [publish])

  const headingOf = useCallback((unitId: string) => engine.headingOf(unitId), [engine])

  // Se recalcula una vez por tick, que es exactamente cuando cambia.
  const view = useMemo(() => toWorldView(frame.state, headingOf), [frame, headingOf])

  return {
    frame,
    view,
    scenario: SCENARIO,
    speed,
    running,
    answersPhone,
    controls,
    headingOf,
  }
}
