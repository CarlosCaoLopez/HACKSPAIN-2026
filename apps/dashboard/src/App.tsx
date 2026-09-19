// El dashboard. Se enseña ANTES que el mundo: no es la demo, es el banco de pruebas.
import { useState } from 'react'

import { useEventStream } from './hooks/useEventStream'
import { useHealth } from './hooks/useHealth'
import { useScenarioId } from './hooks/useScenario'
import { ReplanBanner } from './components/ReplanBanner'
import { ActionLog } from './panels/ActionLog'
import { CallsPanel } from './panels/CallsPanel'
import { DivergenceChart } from './panels/DivergenceChart'
import { MapPanel } from './panels/MapPanel'
import { PriorityQueue } from './panels/PriorityQueue'
import { RunsPanel } from './panels/RunsPanel'
import { WhatChangedPanel } from './panels/WhatChangedPanel'

/** Un plan B en marcha, dicho en la cabecera. Ámbar y no rojo: el rojo es del banner
 *  REPLAN y de nada más, y esto no es una alarma — es una aclaración. */
function Badge({ children }: { children: string }) {
  return (
    <span className="rounded border border-vela-warn/40 px-2 py-0.5 text-xs text-vela-warn">
      {children}
    </span>
  )
}

export default function App() {
  const { state, plan, events, connected, hydrated } = useEventStream()
  // La geometría del mapa no viaja por eventos: se pide por HTTP, y el run en curso
  // decide de qué escenario (H3).
  const scenarioId = useScenarioId(events)
  // Lo que está simulado se anuncia solo (H5): si me callo que las llamadas son de
  // mentira, la pantalla miente por omisión.
  const health = useHealth(events)
  const [comparing, setComparing] = useState(false)
  // El run ha terminado: es cuando la comparación tiene algo nuevo que decir, y cuando
  // yo la abro en el pitch.
  const finished = events.some((ev) => ev.type === 'run.ended')
  // Socket abierto y snapshot en vuelo: el hueco del esqueleto (REQ-202). Se calcula
  // aquí una vez y baja como una sola prop, porque con el socket CAÍDO no hay esqueleto
  // que valga — ahí mandan los estados vacíos, y uno que se quedara pulsando para
  // siempre volvería a leerse como *roto*.
  const awaitingSnapshot = connected && !hydrated

  return (
    // REQ-018: entra completo en 1920×1080 sin scroll vertical. La rejilla es
    // h-screen y cada celda es min-h-0; el scroll vive dentro de cada panel.
    //
    // La fila del medio es la banda del banner REPLAN (H4). Está siempre, con replan y
    // sin él: si apareciera y desapareciera, la rejilla entera daría un salto justo
    // cuando hay que mirarla.
    // `relative`: el modo comparación se monta encima, dentro del mismo marco. No es
    // una ruta —no hay router y no hace falta uno para enseñar dos columnas—, y cerrado
    // no ocupa ni un píxel de la rejilla.
    <div className="relative grid h-screen grid-rows-[auto_auto_1fr] bg-vela-bg">
      <header className="flex items-baseline gap-4 px-4 py-2">
        <span className="text-lg font-bold tracking-widest text-vela-ink">VELA</span>
        {/* Estado, no promesa (REQ-198). Aquí estaba el eslogan, que es marketing en una
            pantalla sin sitio y que además digo yo en voz alta en el minuto 0. Lo que
            hace falta saber de un vistazo es qué escenario corre y si sigue vivo. */}
        <span className="text-sm text-vela-dim">
          {scenarioId ?? 'sin escenario'} · {finished ? 'run terminado' : 'run en curso'}
        </span>
        {health?.calls === 'simuladas' && <Badge>llamadas simuladas</Badge>}
        {health?.minecraft === 'apagado' && <Badge>sin Minecraft</Badge>}
        <span className="ml-auto text-xs tabular-nums text-vela-dim">
          t_sim {state?.t_sim.toFixed(1) ?? '—'} · seq {state?.seq ?? '—'}
        </span>
        {/* El estado del socket va PEGADO al contador que lo valida (REQ-201): si el WS
            cae, el número que deja de subir y el aviso que lo explica tienen que estar
            en el mismo sitio, no uno en cada punta de la cabecera. */}
        <span
          className={`text-xs tabular-nums ${connected ? 'text-vela-dim' : 'text-vela-warn'}`}
        >
          ● {events.length} eventos{connected ? '' : ' · sin conexión'}
        </span>
        <button
          type="button"
          onClick={() => setComparing(true)}
          // 2.5rem: se pulsa con el ratón en directo y se lee a diez metros (REQ-148).
          className={`min-h-[2.5rem] rounded border px-3 text-sm ${
            finished
              ? 'border-vela-accent text-vela-accent'
              : 'border-vela-edge text-vela-dim'
          }`}
        >
          runs
        </button>
      </header>

      {comparing && <RunsPanel events={events} onClose={() => setComparing(false)} />}

      <ReplanBanner events={events} />

      <main className="grid min-h-0 grid-cols-12 grid-rows-3 gap-3 p-3 pt-0">
        <div className="col-span-7 row-span-2 min-h-0">
          <MapPanel
            state={state}
            plan={plan}
            events={events}
            scenarioId={scenarioId}
            awaitingSnapshot={awaitingSnapshot}
          />
        </div>
        <div className="col-span-5 min-h-0">
          <WhatChangedPanel events={events} awaitingSnapshot={awaitingSnapshot} />
        </div>
        <div className="col-span-5 min-h-0">
          <PriorityQueue plan={plan} state={state} awaitingSnapshot={awaitingSnapshot} />
        </div>
        <div className="col-span-4 min-h-0">
          <ActionLog events={events} awaitingSnapshot={awaitingSnapshot} />
        </div>
        <div className="col-span-4 min-h-0">
          <CallsPanel events={events} awaitingSnapshot={awaitingSnapshot} />
        </div>
        <div className="col-span-4 min-h-0">
          <DivergenceChart events={events} />
        </div>
      </main>
    </div>
  )
}
