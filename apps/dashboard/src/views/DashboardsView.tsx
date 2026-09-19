// La vista Dashboards (SPEC-008 REQ-276…280, REQ-311): rejilla 3×2, cada panel del alto de
// la pantalla. Hechos (lo que hay) abre, al lado de Qué ha cambiado (lo que ha pasado).
//
// Hace scroll vertical (enmienda de REQ-018, REQ-279): lo que se pidió es que cada panel
// tenga una pantalla entera. El scroll es de ESTE contenedor, no del body, así que la
// sidebar no se mueve; y el banner REPLAN va pegado arriba (REQ-274), de modo que un replan
// se ve estés en el panel que estés.
import type { Event, Plan, WorldState } from '../types'
import type { WorldView } from '../hooks/useWorldView'
import { ReplanBanner } from '../components/ReplanBanner'
import { ViewHeader } from '../components/ViewIcon'
import { ActionLog } from '../panels/ActionLog'
import { CallsPanel } from '../panels/CallsPanel'
import { DivergenceChart } from '../panels/DivergenceChart'
import { FactsPanel } from '../panels/FactsPanel'
import { PriorityQueue } from '../panels/PriorityQueue'
import { WhatChangedPanel } from '../panels/WhatChangedPanel'

/** Alto de la cabecera de vista y de la banda del banner (`h-14` en los dos). Son
 *  constantes y no "a ojo" para que las filas midan exactamente el alto útil. */
const HEADER_PX = 56
const BANNER_PX = 56
/** Lo que queda de pantalla bajo la cabecera y el banner, menos el aire inferior. */
const ROW_HEIGHT = `calc(100dvh - ${HEADER_PX + BANNER_PX}px - 12px)`

export function DashboardsView({
  state,
  plan,
  events,
  worldView,
  awaitingSnapshot,
  focusCallId,
}: {
  state: WorldState | null
  plan: Plan | null
  events: Event[]
  worldView: WorldView
  awaitingSnapshot: boolean
  focusCallId: string | null
}) {
  return (
    <div className="flex h-full min-h-0 flex-col">
      <ViewHeader view="dashboards" />
      <div className="min-h-0 flex-1 overflow-y-auto">
        {/* `sticky`: el banner acompaña al scroll. Fondo propio para que las tarjetas que
            pasan por debajo no se transparenten. */}
        <div className="sticky top-0 z-10 bg-vela-panel">
          <ReplanBanner events={events} />
        </div>
        <div className="grid grid-cols-2 gap-3 px-3 pb-3">
          <div style={{ height: ROW_HEIGHT }} className="min-h-0">
            <FactsPanel
              state={state}
              events={events}
              worldView={worldView}
              awaitingSnapshot={awaitingSnapshot}
            />
          </div>
          <div style={{ height: ROW_HEIGHT }} className="min-h-0">
            <WhatChangedPanel events={events} awaitingSnapshot={awaitingSnapshot} />
          </div>
          <div style={{ height: ROW_HEIGHT }} className="min-h-0">
            <PriorityQueue plan={plan} state={state} awaitingSnapshot={awaitingSnapshot} />
          </div>
          <div style={{ height: ROW_HEIGHT }} className="min-h-0">
            <ActionLog events={events} awaitingSnapshot={awaitingSnapshot} />
          </div>
          <div style={{ height: ROW_HEIGHT }} className="min-h-0">
            <CallsPanel
              events={events}
              awaitingSnapshot={awaitingSnapshot}
              focusCallId={focusCallId}
            />
          </div>
          <div style={{ height: ROW_HEIGHT }} className="min-h-0">
            <DivergenceChart events={events} />
          </div>
        </div>
      </div>
    </div>
  )
}
