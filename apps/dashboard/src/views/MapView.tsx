// La vista Mapa (SPEC-008 REQ-281, REQ-282): el mapa a todo el ancho y el alto disponibles.
//
// El modo lo deciden los DATOS, nunca un interruptor (REQ-282): con un ancla real fijada
// sería el mapa de OpenStreetMap; sin ella, es el esquemático de SPEC-006. Es degradación
// explícita: el chip dice en qué modo está y por qué, en vez de caer sin avisar.
import type { Event, Plan, WorldState } from '../types'
import { ReplanBanner } from '../components/ReplanBanner'
import { ViewHeader } from '../components/ViewIcon'
import { MapPanel } from '../panels/MapPanel'

export function MapView({
  state,
  plan,
  events,
  scenarioId,
  awaitingSnapshot,
}: {
  state: WorldState | null
  plan: Plan | null
  events: Event[]
  scenarioId: string | null
  awaitingSnapshot: boolean
}) {
  // El motivo por el que no hay mapa real. Todavía no hay `/api/feeds` en el cliente
  // (llega en F5), así que hoy siempre es esquemático.
  const reason = 'sin ancla real'

  return (
    <div className="flex h-full min-h-0 flex-col">
      <ViewHeader view="mapa">
        <span className="rounded-md border border-vela-edge px-2 py-0.5 text-xs text-vela-dim">
          esquemático · {reason}
        </span>
      </ViewHeader>
      <div className="sticky top-0 z-10 bg-vela-panel">
        <ReplanBanner events={events} />
      </div>
      <div className="min-h-0 flex-1 px-3 pb-3">
        <MapPanel
          state={state}
          plan={plan}
          events={events}
          scenarioId={scenarioId}
          awaitingSnapshot={awaitingSnapshot}
        />
      </div>
    </div>
  )
}
