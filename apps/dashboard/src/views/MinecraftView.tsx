// La vista Minecraft: lo que mira el visitante de la /demo mientras le suenan los
// teléfonos. El vídeo a lo grande y, al lado, las llamadas en vivo: es la pareja que en el
// pitch iba en dos pantallas, en una.
import type { Event } from '../types'
import { CamPanel } from '../panels/CamPanel'
import { CallsPanel } from '../panels/CallsPanel'
import { ReplanBanner } from '../components/ReplanBanner'
import { ViewHeader } from '../components/ViewIcon'
import { mmss } from '../story/format'
import { useCountdown, type DemoStatus } from '../hooks/useDemoStatus'

const HEADER_PX = 56
const BANNER_PX = 56
const BODY_HEIGHT = `calc(100dvh - ${HEADER_PX + BANNER_PX}px - 12px)`

export function MinecraftView({
  events,
  awaitingSnapshot,
  demo,
  focusCallId,
}: {
  events: Event[]
  awaitingSnapshot: boolean
  demo: DemoStatus | null
  focusCallId: string | null
}) {
  const left = useCountdown(demo?.ends_in_s ?? null)
  const camUrl = demo?.cam_hls_url ?? ''

  return (
    <div className="flex h-full min-h-0 flex-col">
      <ViewHeader view="minecraft">
        {demo?.busy && left !== null && (
          <span className="ml-auto text-sm tabular-nums text-vela-dim" title="La demo se para sola al acabar">
            acaba en {mmss(left)}
          </span>
        )}
      </ViewHeader>
      <div className="sticky top-0 z-10 bg-vela-panel">
        <ReplanBanner events={events} />
      </div>
      <div className="grid min-h-0 gap-3 px-3 pb-3 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]" style={{ height: BODY_HEIGHT }}>
        {camUrl ? (
          <CamPanel url={camUrl} className="min-h-0" />
        ) : (
          <div className="flex min-h-0 items-center justify-center rounded-xl border border-vela-edge bg-vela-bg p-6 text-center text-vela-dim">
            <div>
              <p className="text-lg text-vela-ink">Sin cámara de Minecraft en este despliegue</p>
              <p className="mt-1 text-sm">
                Falta <code>VELA_CAM_PLAYER</code> en el gateway, o Minecraft va en la segunda pantalla como en el pitch.
                El mapa real está en la vista Mapa.
              </p>
            </div>
          </div>
        )}
        <div className="min-h-0">
          <CallsPanel events={events} awaitingSnapshot={awaitingSnapshot} focusCallId={focusCallId} />
        </div>
      </div>
    </div>
  )
}
