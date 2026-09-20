// El dashboard. Se enseña ANTES que el mundo: no es la demo, es el banco de pruebas.
//
// SPEC-008: sidebar a la izquierda con dos vistas (Dashboards y Mapa). Los hooks del
// chorro viven AQUÍ, una sola vez: cambiar de vista no reconecta el WebSocket ni pierde
// eventos (REQ-275). Las vistas reciben props, no abren sockets.
import { useState } from 'react'

import { useDemoStatus } from './hooks/useDemoStatus'
import { useEventStream } from './hooks/useEventStream'
import { useHealth } from './hooks/useHealth'
import { useScenarioId } from './hooks/useScenario'
import { useView } from './hooks/useView'
import { useWorldView } from './hooks/useWorldView'
import { Sidebar } from './components/Sidebar'
import { RunsPanel } from './panels/RunsPanel'
import { DashboardsView } from './views/DashboardsView'
import { MapView } from './views/MapView'
import { MinecraftView } from './views/MinecraftView'
import { Waiting } from './components/Waiting'

export default function App() {
  const { state, plan, events, connected, hydrated } = useEventStream()
  // La geometría del mapa no viaja por eventos: se pide por HTTP, y el run en curso
  // decide de qué escenario (H3).
  const scenarioId = useScenarioId(events)
  // Lo que está simulado se anuncia solo (H5): si me callo que las llamadas son de
  // mentira, la pantalla miente por omisión.
  const health = useHealth(events)
  // Una sola vista del mundo para el mapa y el panel Hechos (REQ-311): si cada uno plegara
  // la suya, podrían enseñar dos posiciones distintas del mismo camión.
  const worldView = useWorldView(state, events)
  const [view, setView] = useView()
  const [comparing, setComparing] = useState(false)
  // La llamada a la que lleva un clic en la persona que llama del mapa (REQ-310): se pasa
  // a Dashboards, que la trae a la vista en el panel de Llamadas.
  const [focusCallId, setFocusCallId] = useState<string | null>(null)
  const openCall = (callId: string) => {
    setFocusCallId(callId)
    setView('dashboards')
  }
  // El run ha terminado: es cuando la comparación tiene algo nuevo que decir, y cuando
  // yo la abro en el pitch.
  const finished = events.some((ev) => ev.type === 'run.ended')
  // Socket abierto y snapshot en vuelo: el hueco del esqueleto (REQ-202). Se calcula
  // aquí una vez y baja como una sola prop, porque con el socket CAÍDO no hay esqueleto
  // que valga — ahí mandan los estados vacíos.
  const awaitingSnapshot = connected && !hydrated
  // La /demo autoservicio: sin run, la sala de espera; con run, lo de siempre. En replay
  // (`make dev-dash`) el gateway no tiene run y sí tiene eventos: los eventos mandan.
  const demo = useDemoStatus()
  const [peeking, setPeeking] = useState(false)
  const waiting =
    demo !== null && !demo.busy && !peeking && events.length === 0 && state === null

  return (
    // `relative`: el modo comparación se monta encima, dentro del mismo marco. No es una
    // ruta ni una tercera vista: cerrado no ocupa ni un píxel.
    <div className="relative flex h-screen bg-vela-bg">
      <Sidebar
        view={view}
        onView={setView}
        scenarioId={scenarioId}
        finished={finished}
        health={health}
        state={state}
        connected={connected}
        eventCount={events.length}
        onRuns={() => setComparing(true)}
      />

      {/* La superficie principal, como en Factorial: blanca, con la esquina superior
          izquierda redondeada y un borde. */}
      <main className="min-w-0 flex-1 overflow-hidden rounded-tl-[14px] border-l border-t border-vela-edge bg-vela-panel">
        {view === 'dashboards' ? (
          <DashboardsView
            state={state}
            plan={plan}
            events={events}
            worldView={worldView}
            awaitingSnapshot={awaitingSnapshot}
            focusCallId={focusCallId}
          />
        ) : view === 'minecraft' ? (
          <MinecraftView
            events={events}
            awaitingSnapshot={awaitingSnapshot}
            demo={demo}
            focusCallId={focusCallId}
          />
        ) : (
          <MapView
            state={state}
            plan={plan}
            events={events}
            worldView={worldView}
            scenarioId={scenarioId}
            awaitingSnapshot={awaitingSnapshot}
            onOpenCall={openCall}
          />
        )}
      </main>

      {comparing && <RunsPanel events={events} onClose={() => setComparing(false)} />}
      {waiting && demo && <Waiting status={demo} onPeek={() => setPeeking(true)} />}
    </div>
  )
}
