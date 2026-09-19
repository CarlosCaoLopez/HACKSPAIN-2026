// La vista Mapa (SPEC-008 REQ-281): el mapa real a todo el ancho y el alto disponibles.
//
// Solo hay un mapa: el de OpenStreetMap. Necesita un ancla real fijada (SPEC-007), porque
// sin saber dónde del mundo real cae `(0, 0)` no hay dónde poner el valle simulado. Sin
// ancla NO se cae a otro mapa: se dice qué falta. Es degradación explícita, no un mapa
// inventado (REQ-066: un mapa que no es real presentado como real es peor que ninguno).
import type { Event, Plan, WorldState } from '../types'
import type { WorldView } from '../hooks/useWorldView'
import { ReplanBanner } from '../components/ReplanBanner'
import { ViewHeader } from '../components/ViewIcon'
import { RealMap } from '../geo/RealMap'
import { realAnchor, noMapReason, useFeeds } from '../hooks/useFeeds'

export function MapView({
  state,
  plan,
  events,
  worldView,
  scenarioId,
  awaitingSnapshot,
  onOpenCall,
}: {
  state: WorldState | null
  plan: Plan | null
  events: Event[]
  worldView: WorldView
  scenarioId: string | null
  awaitingSnapshot: boolean
  onOpenCall: (callId: string) => void
}) {
  const feedsState = useFeeds()
  const anchor = realAnchor(feedsState)

  return (
    <div className="flex h-full min-h-0 flex-col">
      <ViewHeader view="mapa">
        <span className="rounded-md border border-vela-edge px-2 py-0.5 text-xs text-vela-dim">
          {anchor ? 'mapa real · OpenStreetMap' : `sin mapa · ${noMapReason(feedsState)}`}
        </span>
      </ViewHeader>
      <div className="sticky top-0 z-[1000] bg-vela-panel">
        <ReplanBanner events={events} />
      </div>
      <div className="min-h-0 flex-1 px-3 pb-3">
        {anchor && feedsState.kind === 'ready' ? (
          <RealMap
            state={state}
            plan={plan}
            events={events}
            worldView={worldView}
            scenarioId={scenarioId}
            anchor={anchor}
            feeds={feedsState.feeds}
            awaitingSnapshot={awaitingSnapshot}
            onOpenCall={onOpenCall}
          />
        ) : (
          <NoMap reason={noMapReason(feedsState)} />
        )}
      </div>
    </div>
  )
}

/** El vacío dice qué falta y de dónde tiene que llegar, nunca «sin datos» (REQ-193). */
function NoMap({ reason }: { reason: string }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 rounded-[10px] border border-vela-edge-bright bg-vela-bg px-6 text-center">
      <p className="text-lg font-semibold text-vela-ink">Sin mapa: {reason}.</p>
      <p className="max-w-xl text-vela-dim">
        El mapa real necesita un ancla fijada para el escenario: el punto del mundo real donde cae
        el valle simulado. Se fija en <code>feeds/anchors/&lt;escenario&gt;.yaml</code> con{' '}
        <code>fixed: true</code>, y con <code>VELA_FEEDS</code> distinto de <code>off</code>.
      </p>
    </div>
  )
}
