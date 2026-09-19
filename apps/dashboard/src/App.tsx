// El dashboard. Se enseña ANTES que el mundo: no es la demo, es el banco de pruebas.
//
// SPEC-008: sidebar a la izquierda con dos vistas (Dashboards y Mapa). Los hooks del
// chorro viven AQUÍ, una sola vez: cambiar de vista no reconecta el WebSocket ni pierde
// eventos (REQ-275). Las vistas reciben props, no abren sockets.
import { useState } from 'react'

import { useEventStream } from './hooks/useEventStream'
import { useHealth } from './hooks/useHealth'
import { useScenarioId } from './hooks/useScenario'
import { useView } from './hooks/useView'
import { Sidebar } from './components/Sidebar'
import { RunsPanel } from './panels/RunsPanel'
import { DashboardsView } from './views/DashboardsView'
import { MapView } from './views/MapView'

export default function App() {
  const { state, plan, events, connected, hydrated } = useEventStream()
  // La geometría del mapa no viaja por eventos: se pide por HTTP, y el run en curso
  // decide de qué escenario (H3).
  const scenarioId = useScenarioId(events)
  // Lo que está simulado se anuncia solo (H5): si me callo que las llamadas son de
  // mentira, la pantalla miente por omisión.
  const health = useHealth(events)
  const [view, setView] = useView()
  const [comparing, setComparing] = useState(false)
  // El run ha terminado: es cuando la comparación tiene algo nuevo que decir, y cuando
  // yo la abro en el pitch.
  const finished = events.some((ev) => ev.type === 'run.ended')
  // Socket abierto y snapshot en vuelo: el hueco del esqueleto (REQ-202). Se calcula
  // aquí una vez y baja como una sola prop, porque con el socket CAÍDO no hay esqueleto
  // que valga — ahí mandan los estados vacíos.
  const awaitingSnapshot = connected && !hydrated

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
            awaitingSnapshot={awaitingSnapshot}
          />
        ) : (
          <MapView
            state={state}
            plan={plan}
            events={events}
            scenarioId={scenarioId}
            awaitingSnapshot={awaitingSnapshot}
          />
        )}
      </main>

      {comparing && <RunsPanel events={events} onClose={() => setComparing(false)} />}
    </div>
  )
}
