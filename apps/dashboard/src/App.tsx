// El dashboard. Se enseña ANTES que el mundo: no es la demo, es el banco de pruebas.
import { useEventStream } from './hooks/useEventStream'
import { useScenarioId } from './hooks/useScenario'
import { ReplanBanner } from './components/ReplanBanner'
import { ActionLog } from './panels/ActionLog'
import { CallsPanel } from './panels/CallsPanel'
import { DivergenceChart } from './panels/DivergenceChart'
import { MapPanel } from './panels/MapPanel'
import { PriorityQueue } from './panels/PriorityQueue'
import { WhatChangedPanel } from './panels/WhatChangedPanel'

export default function App() {
  const { state, plan, events, connected } = useEventStream()
  // La geometría del mapa no viaja por eventos: se pide por HTTP, y el run en curso
  // decide de qué escenario (H3).
  const scenarioId = useScenarioId(events)

  return (
    // REQ-018: entra completo en 1920×1080 sin scroll vertical. La rejilla es
    // h-screen y cada celda es min-h-0; el scroll vive dentro de cada panel.
    //
    // La fila del medio es la banda del banner REPLAN (H4). Está siempre, con replan y
    // sin él: si apareciera y desapareciera, la rejilla entera daría un salto justo
    // cuando hay que mirarla.
    <div className="grid h-screen grid-rows-[auto_auto_1fr] bg-vela-bg">
      <header className="flex items-baseline gap-4 px-4 py-2">
        <span className="text-lg font-bold tracking-widest text-vela-ink">VELA</span>
        <span className="text-xs text-vela-dim">
          ver · establecer prioridad · llamar · adaptar
        </span>
        <span className="ml-auto text-xs tabular-nums text-vela-dim">
          t_sim {state?.t_sim.toFixed(1) ?? '—'} · seq {state?.seq ?? '—'} ·{' '}
          {events.length} eventos
        </span>
        <span className={connected ? 'text-xs text-vela-accent' : 'text-xs text-vela-dim'}>
          {connected ? 'conectado' : 'sin conexión'}
        </span>
      </header>

      <ReplanBanner events={events} />

      <main className="grid min-h-0 grid-cols-12 grid-rows-3 gap-3 p-3 pt-0">
        <div className="col-span-7 row-span-2 min-h-0">
          <MapPanel state={state} plan={plan} events={events} scenarioId={scenarioId} />
        </div>
        <div className="col-span-5 min-h-0">
          <WhatChangedPanel events={events} />
        </div>
        <div className="col-span-5 min-h-0">
          <PriorityQueue plan={plan} state={state} />
        </div>
        <div className="col-span-4 min-h-0">
          <ActionLog events={events} />
        </div>
        <div className="col-span-4 min-h-0">
          <CallsPanel events={events} />
        </div>
        <div className="col-span-4 min-h-0">
          <DivergenceChart events={events} />
        </div>
      </main>
    </div>
  )
}
