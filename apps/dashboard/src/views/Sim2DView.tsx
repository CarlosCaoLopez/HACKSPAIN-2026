// La vista Simulación 2D.
//
// Es la única vista que NO habla con el backend: no recibe `state`, ni `plan`, ni
// `events`, ni el WebSocket de `App`. Simula el mundo en el navegador con el mismo
// modelo de fuego que `packages/sim` y el mismo solver que `packages/core`, y por eso
// funciona con el gateway apagado, sin `feeds/` y sin Minecraft.
//
// Lo que enseña, de izquierda a derecha: el mapa donde se enciende el fuego, y al lado
// la cadena entera de la decisión — la Policy que dice qué importa, el reparto que
// hace el solver con su ecuación de coste, y a quién se llama por ese reparto.
import { useState } from 'react'

import { ViewHeader } from '../components/ViewIcon'
import { mmss } from '../story/format'
import { SimControls } from '../sim2d/SimControls'
import { SimMap } from '../sim2d/SimMap'
import { PolicyPanel } from '../sim2d/panels/PolicyPanel'
import { SimCallsPanel } from '../sim2d/panels/SimCallsPanel'
import { SolverPanel } from '../sim2d/panels/SolverPanel'
import { useSimEngine } from '../sim2d/useSimEngine'
import { RoadGraph } from '../sim2d/engine/graph'

export function Sim2DView() {
  const sim = useSimEngine()
  const [tool, setTool] = useState<'none' | 'ignite' | 'douse'>('ignite')
  const [focus, setFocus] = useState<string | null>(null)
  const [graph] = useState(() => RoadGraph.fromScenario(sim.scenario))

  const pick = (cellId: string) => {
    if (tool === 'ignite') sim.controls.ignite(cellId)
    else if (tool === 'douse') sim.controls.douse(cellId)
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <ViewHeader view="simulacion">
        <span className="ml-auto flex items-center gap-4 text-sm text-vela-dim">
          <span className="tabular-nums text-vela-ink">t {mmss(sim.frame.tSim)}</span>
          <span>escenario simulado · sin conexión al gateway</span>
        </span>
      </ViewHeader>

      <div className="border-y border-vela-edge bg-vela-bg">
        <SimControls
          frame={sim.frame}
          scenario={sim.scenario}
          speed={sim.speed}
          running={sim.running}
          answersPhone={sim.answersPhone}
          tool={tool}
          controls={sim.controls}
          onTool={setTool}
        />
      </div>

      <Banner frame={sim.frame} />

      <div className="grid min-h-0 flex-1 gap-3 p-3 grid-cols-[minmax(0,1fr)_minmax(0,27rem)]">
        <SimMap
          frame={sim.frame}
          view={sim.view}
          scenario={sim.scenario}
          speed={sim.speed}
          tool={tool}
          focus={focus}
          onPickCell={pick}
          onFocus={setFocus}
        />
        <div className="flex min-h-0 flex-col gap-3 overflow-y-auto">
          <div className="min-h-[22rem] shrink-0">
            <PolicyPanel frame={sim.frame} />
          </div>
          <div className="min-h-[22rem] shrink-0">
            <SolverPanel
              frame={sim.frame}
              graph={graph}
              aliases={sim.scenario.road_aliases}
              focus={focus}
              onFocus={setFocus}
            />
          </div>
          <div className="min-h-[22rem] shrink-0">
            <SimCallsPanel frame={sim.frame} />
          </div>
        </div>
      </div>
    </div>
  )
}

/** Un replan (Policy nueva) y un re-reparto (misma Policy) no son lo mismo, y no se
 *  pintan igual: el rojo es del acontecimiento, el gris es de la rutina. */
function Banner({ frame }: { frame: ReturnType<typeof useSimEngine>['frame'] }) {
  const b = frame.banner
  if (!b) return null
  const fresh = frame.tSim - b.tSim < 8
  if (!fresh) return null
  const replan = b.kind === 'replan'
  return (
    <div
      className={`flex h-10 shrink-0 items-center gap-3 px-4 text-sm ${
        replan ? 'bg-vela-replan text-white' : 'bg-vela-edge text-vela-ink'
      }`}
    >
      <span className="font-semibold tracking-[0.18em]">{replan ? 'REPLAN' : 'NUEVO REPARTO'}</span>
      <span>{b.reason}</span>
      <span className="ml-auto tabular-nums opacity-80">{mmss(b.tSim)}</span>
    </div>
  )
}
