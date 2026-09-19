// El mapa real: OpenStreetMap con el valle simulado, los focos reales y los agentes
// encima. SPEC-008 REQ-281…297. Solo se monta con un ancla real fijada (REQ-282).
//
// Este fichero es el pegamento con React: monta la `Scene` de Leaflet, se la mantiene al
// día con lo que llega y superpone lo que NO es del mapa (distintivo del ancla, aviso de
// teselas, leyenda). Toda la lógica de capas vive en `scene.ts`.
import { useEffect, useMemo, useRef, useState } from 'react'
import 'leaflet/dist/leaflet.css'

import type { Feeds } from '../hooks/useFeeds'
import { useScenario } from '../hooks/useScenario'
import { useWorldView, type WorldView } from '../hooks/useWorldView'
import { problems } from './problems'
import type { Event, Plan, UnitKind, VelaEvent, WorldState } from '../types'
import type { FeedAnchor } from './anchor'
import { activeCallers } from './callers'
import { firmsFoci } from './firms'
import {
  CALLER_ICON,
  CELL_ICON,
  FIRMS_ICON,
  POI_ICON,
  ROAD_CUT_ICON,
  TASK_ICON,
  UNIT_ICON,
} from './icons'
import { Legend, type LegendItem } from './Legend'
import { Scene } from './scene'

// Teselas estándar de OpenStreetMap: sin clave y sin facturación (REQ-283). Se probó CARTO
// Positron primero, pero sus teselas `light_all` ya exigen `apikey` y pintan una marca de
// agua «API KEY REQUIRED» sobre todo el mapa. El aspecto minimalista se consigue aclarando
// las teselas con CSS (`.leaflet-tile-pane` en `index.css`), no con otro proveedor.
// La política de uso de OSM permite un uso ligero con atribución, que es el de una demo.
const TILE_URL = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png'

/** Cuántos errores de tesela sin ninguna cargada hacen falta para decir «sin red» (REQ-292). */
const TILE_ERRORS_BEFORE_WARNING = 3

/** De dónde sale el último viento: el del sim, o el de Open-Meteo, que es un MODELO
 *  (REQ-266). Mirar solo el último hecho `wind:*` es lo que dice la brújula. */
function windSourceOf(events: Event[]): string {
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const envelope = events[i]
    if (!envelope || envelope.type !== 'world.fact.asserted') continue
    const ev = envelope as unknown as VelaEvent
    if (ev.type !== 'world.fact.asserted') continue
    if (!ev.payload.key.startsWith('wind:')) continue
    return ev.payload.source.startsWith('api:open-meteo') ? 'Open-Meteo (modelo)' : 'sim'
  }
  return 'sim'
}

function AnchorBadge({ anchor }: { anchor: FeedAnchor }) {
  // Sin este distintivo el valle superpuesto se lee como geografía real (REQ-066, REQ-290).
  const date = anchor.reference_start
    ? `datos del ${new Date(anchor.reference_start).toLocaleDateString('es-ES', { day: 'numeric', month: 'long', year: 'numeric' })}`
    : 'en directo'
  return (
    <div className="absolute left-3 top-3 z-[500] max-w-sm rounded-[9px] border border-vela-edge bg-vela-panel/90 px-2.5 py-1.5 text-[13px] shadow-sm">
      <p className="font-semibold text-vela-ink">
        Zona real: {anchor.place} · 1 bloque = {anchor.meters_per_block} m · {date}
      </p>
      <p className="text-xs text-vela-dim">el valle es simulado; sus carreteras ≈ tramos reales declarados</p>
    </div>
  )
}

export function RealMap({
  state,
  plan,
  events,
  scenarioId,
  anchor,
  feeds,
  awaitingSnapshot,
  onOpenCall,
}: {
  state: WorldState | null
  plan: Plan | null
  events: Event[]
  scenarioId: string | null
  anchor: FeedAnchor
  feeds: Feeds
  awaitingSnapshot: boolean
  onOpenCall: (callId: string) => void
}) {
  const layer = useScenario(scenarioId)
  const worldView = useWorldView(state, events)
  const containerRef = useRef<HTMLDivElement>(null)
  const [scene, setScene] = useState<Scene | null>(null)
  const [tiles, setTiles] = useState({ loaded: 0, errors: 0 })

  // Esperando el snapshot no se pintan ni unidades ni civiles (REQ-202): son estado del run
  // y todavía no se sabe dónde están. Enseñar los de antes de la reconexión sería pintar un
  // mundo que quizá ya no existe.
  const view: WorldView = useMemo(
    () =>
      awaitingSnapshot
        ? { ...worldView, units: new Map(), civilians: new Map() }
        : worldView,
    [worldView, awaitingSnapshot],
  )

  // `onOpenCall` cambia de identidad con cada render de `App`; la escena guarda la primera.
  // Por eso se pasa por una ref: el clic siempre llama a la versión de ahora.
  const openCall = useRef(onOpenCall)
  openCall.current = onOpenCall

  // `feeds` se vuelve a pedir cada 10 s y el ancla llega como un objeto nuevo cada vez: si
  // el efecto dependiera de su identidad, el mapa entero se recrearía cada 10 s. Depende
  // de lo que de verdad lo define.
  const { id: anchorId, lat0, lon0, meters_per_block: metersPerBlock } = anchor
  // Las dependencias son a propósito los cuatro valores y no el objeto.
  const stableAnchor = useMemo(() => anchor, [anchorId, lat0, lon0, metersPerBlock])

  useEffect(() => {
    const container = containerRef.current
    if (!container || !layer) return
    const created = new Scene(container, {
      layer,
      anchor: stableAnchor,
      tileUrl: TILE_URL,
      reducedMotion: window.matchMedia('(prefers-reduced-motion: reduce)').matches,
      onTiles: (loaded, errors) => setTiles({ loaded, errors }),
      onOpenCall: (callId) => openCall.current(callId),
    })
    setScene(created)
    // La sidebar se pliega y la ventana cambia: Leaflet no se entera solo del tamaño.
    const observer = new ResizeObserver(() => created.invalidate())
    observer.observe(container)
    return () => {
      observer.disconnect()
      created.destroy()
      setScene(null)
    }
  }, [layer, stableAnchor])

  const foci = useMemo(() => firmsFoci(events, feeds.detections), [events, feeds.detections])
  const callers = useMemo(
    () => activeCallers(events, layer?.pois ?? [], view.tSim),
    [events, layer, view.tSim],
  )
  const unitKinds = useMemo(() => {
    const kinds = new Map<string, UnitKind>()
    for (const u of layer?.units ?? []) kinds.set(u.id, u.kind)
    for (const u of Object.values(state?.units ?? {})) kinds.set(u.id, u.kind)
    return kinds
  }, [layer, state])

  useEffect(() => {
    scene?.sync({
      view,
      plan: awaitingSnapshot ? null : plan,
      tasks: state?.tasks ?? {},
      unitKinds,
      foci,
      callers,
    })
  }, [scene, view, plan, state, unitKinds, foci, callers, awaitingSnapshot])

  const legendItems = useMemo<LegendItem[]>(() => {
    if (!layer) return []
    const out: LegendItem[] = []
    const seen = new Set<string>()
    const add = (key: string, def: LegendItem['def']) => {
      if (seen.has(key)) return
      seen.add(key)
      out.push({ key, def })
    }
    for (const u of view.units.values()) {
      const kind = unitKinds.get(u.id)
      if (kind) add(`unit:${kind}`, UNIT_ICON[kind])
    }
    for (const p of layer.pois) add(`poi:${p.kind}`, POI_ICON[p.kind])
    for (const s of view.cells.values()) if (s !== 'intact') add(`cell:${s}`, CELL_ICON[s])
    if (view.cutRoads.size > 0) add('cut', ROAD_CUT_ICON)
    if (foci.length > 0) add('firms', FIRMS_ICON)
    if (callers.placed.length > 0) add('caller', CALLER_ICON)
    for (const a of plan?.assignments ?? []) {
      const kind = state?.tasks[a.task_id]?.kind
      if (kind) add(`task:${kind}`, TASK_ICON[kind])
    }
    return out
  }, [layer, view, unitKinds, foci, callers, plan, state])

  const trouble = useMemo(
    () => (layer && !awaitingSnapshot ? problems(view, layer, plan) : []),
    [layer, view, plan, awaitingSnapshot],
  )
  const windSource = useMemo(() => windSourceOf(events), [events])

  const firmsOk = feeds.sources.firms?.status === 'ok'
  const noTiles = tiles.loaded === 0 && tiles.errors >= TILE_ERRORS_BEFORE_WARNING

  return (
    // `isolate`: los z-index de Leaflet (hasta 1000) se quedan dentro del mapa y no pelean
    // con el banner REPLAN, que tiene que verse siempre por encima (REQ-274).
    <div className="relative isolate h-full w-full overflow-hidden rounded-[10px] border border-vela-edge-bright bg-vela-bg">
      {/* Con `layer` sin llegar no hay mapa que montar: se dice. */}
      {!layer && (
        <p className="absolute inset-0 flex items-center justify-center text-vela-dim">
          Esperando geometría del escenario.
        </p>
      )}
      <div ref={containerRef} className="h-full w-full" />
      <AnchorBadge anchor={anchor} />
      {noTiles && (
        <p className="absolute right-3 top-3 z-[500] rounded-md border border-vela-warn/40 bg-vela-warn-bg px-2 py-1 text-xs font-medium text-vela-warn">
          sin teselas (sin red)
        </p>
      )}
      <Legend
        items={legendItems}
        wind={view.wind}
        windSource={windSource}
        problems={trouble}
        unplacedCalls={callers.unplaced}
        firmsNote={firmsOk && foci.length === 0 ? 'FIRMS: sin focos en la zona' : null}
      />
    </div>
  )
}
