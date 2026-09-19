// La vista Dashboards (SPEC-008 REQ-276…280): dos columnas, cada panel del alto de la
// pantalla, y Divergencia debajo a todo el ancho.
//
// Hace scroll vertical (enmienda de REQ-018, REQ-279): lo que se pidió es que cada panel
// tenga una pantalla entera. El scroll es de ESTE contenedor, no del body, así que la
// sidebar no se mueve; y el banner REPLAN va pegado arriba (REQ-274), de modo que un replan
// se ve estés en el panel que estés.
import type { Event, Plan, WorldState } from '../types'
import { ReplanBanner } from '../components/ReplanBanner'
import { ViewHeader } from '../components/ViewIcon'
import { ActionLog } from '../panels/ActionLog'
import { CallsPanel } from '../panels/CallsPanel'
import { DivergenceChart } from '../panels/DivergenceChart'
import { PriorityQueue } from '../panels/PriorityQueue'
import { WhatChangedPanel } from '../panels/WhatChangedPanel'

/** Alto de la cabecera de vista y de la banda del banner (`h-14` en los dos). Son
 *  constantes y no "a ojo" para que las filas midan exactamente el alto útil. */
const HEADER_PX = 56
const BANNER_PX = 56
/** Lo que queda de pantalla bajo la cabecera y el banner, menos el aire inferior. */
const ROW_HEIGHT = `calc(100dvh - ${HEADER_PX + BANNER_PX}px - 12px)`
const HALF_ROW_HEIGHT = `calc((100dvh - ${HEADER_PX + BANNER_PX}px - 12px) / 2)`

export function DashboardsView({
  state,
  plan,
  events,
  awaitingSnapshot,
  focusCallId,
}: {
  state: WorldState | null
  plan: Plan | null
  events: Event[]
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
          <div style={{ height: HALF_ROW_HEIGHT }} className="col-span-2 min-h-0">
            <DivergenceChart events={events} />
          </div>
        </div>
      </div>
    </div>
  )
}
