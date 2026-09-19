// Las capas de Leaflet del mapa real, actualizadas EN SITIO por id. SPEC-008 REQ-286…310.
//
// Patrón: **registro por id** (un `Map<id, capa>` por tipo de cosa). Cada sincronización
// compara lo que dice el estado con lo que ya hay: crea lo nuevo, muta lo que cambió y
// quita lo que sobra. A `VELA_REPLAY_SPEED=60` (unos 80 eventos/s) borrar y recrear las
// capas en cada evento congelaría la interfaz (REQ-295); mutar no.
//
// No es un componente de React a propósito: Leaflet es imperativo, y una capa de React
// encima no quita trabajo aquí (REQ-294). `RealMap.tsx` solo lo monta y le pasa el estado.
import L from 'leaflet'

import type { ScenarioLayer } from '../hooks/useScenario'
import type { CellCause, WorldView } from '../hooks/useWorldView'
import { poiThreat } from './problems'
import type { Geo } from './grid'
import type { CellState, Plan, Task, UnitKind, UnitStatus, Waypoint } from '../types'
import { cellBounds, squareAround, toLatLon, type FeedAnchor, type LatLon } from './anchor'
import type { Callers } from './callers'
import { delayOf, fireFront, zones } from './declutter'
import { SimClock, UnitTrack } from './motion'
import type { Focus } from './firms'
import {
  BADGE_PX,
  CALLER_ICON,
  CELL_ICON,
  CITIZEN_ICON,
  CIV_TONE,
  FIRMS_ICON,
  POI_ICON,
  ROAD_CUT_ICON,
  TASK_ICON,
  UNIT_ICON,
  badgeElement,
  type BadgeSize,
  type IconDef,
} from './icons'

/** Todo lo que la escena necesita para pintarse. Lo arma `RealMap` en cada render. */
export interface SceneInput {
  view: WorldView
  plan: Plan | null
  tasks: Record<string, Task>
  unitKinds: ReadonlyMap<string, UnitKind>
  foci: Focus[]
  callers: Callers
}

export interface SceneOptions {
  layer: ScenarioLayer
  anchor: FeedAnchor
  tileUrl: string
  reducedMotion: boolean
  onTiles: (loaded: number, errors: number) => void
  onOpenCall: (callId: string) => void
}

/** Ventana de la estela de una unidad (REQ-306). */
const TRAIL_SIM_S = 60
const TRAIL_MAX_POINTS = 20
/** Cada cuántos píxeles de recorrido se añade un punto a la estela. */
const TRAIL_STEP_PX = 8
/** Tope de anillos de evento a la vez (REQ-309): los que sobran se descartan, no se encolan. */
const MAX_RINGS = 3
/** Una llamada colgada se queda así de tiempo pintada en gris (REQ-310). */
const ETA_LABEL_MS = 10_000
const FADE_MS = 600
/** Zoom a partir del cual se enseñan los rótulos secundarios y las etiquetas de FIRMS. */
const NEAR_ZOOM = 14
const FIRMS_LABEL_ZOOM = 13
const FAR_ZOOM = 12

function token(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(`--color-vela-${name}`).trim()
}

function marker(ll: LatLon, icon: L.DivIcon, extra: L.MarkerOptions = {}): L.Marker {
  return L.marker(ll, { icon, keyboard: false, ...extra })
}

interface Tag {
  text: string
  /** Rótulo secundario: solo con zoom cercano o al pasar el ratón (REQ-301.4). */
  minor?: boolean
  /** Nombre de pueblo: se esconde con zoom lejano (REQ-301.6). */
  poi?: boolean
}

/** Un marcador de insignia. `offset` lo desplaza en píxeles de pantalla respecto de su
 *  punto: es la forma de poner un satélite junto a un pueblo (la llamada, la tarea) sin
 *  taparlo. Va en el ANCLA del icono y no en una posición calculada, así se mantiene al
 *  hacer zoom sin recalcular nada. */
function badgeIcon(
  def: IconDef,
  size: BadgeSize,
  className = '',
  tag?: Tag,
  offset: { x: number; y: number } = { x: 0, y: 0 },
): L.DivIcon {
  const px = BADGE_PX[size]
  const root = document.createElement('div')
  root.className = `vela-unit-inner ${className}`.trim()
  root.appendChild(badgeElement(def, size))
  if (tag) {
    const label = document.createElement('span')
    label.className = `vela-tag${tag.minor ? ' vela-tag-minor' : ''}${tag.poi ? ' vela-tag-poi' : ''}`
    label.textContent = tag.text
    root.appendChild(label)
  }
  return L.divIcon({
    className: 'vela-marker',
    html: root,
    iconSize: [px, px],
    iconAnchor: [px / 2 - offset.x, px / 2 - offset.y],
  })
}

function pathClass(layer: L.Path): SVGElement | null {
  const el = layer.getElement()
  return el instanceof SVGElement ? el : null
}

interface UnitEntry {
  marker: L.Marker
  inner: HTMLElement
  kind: UnitKind
  status: UnitStatus
  /** Las posiciones emitidas: de aquí se interpola (`motion.ts`). */
  track: UnitTrack
  /** Dónde se está ENSEÑANDO ahora la unidad, que no es donde está su última posición: va un
   *  intervalo y medio por detrás para poder recorrer el tramo sin saltos. */
  shown: LatLon
  shownXZ: { x: number; z: number }
  flip: boolean
}

interface RouteEntry {
  sig: string
  coords: Waypoint[]
  done: L.Polyline
  pending: L.Polyline
  task: L.Marker | null
}

export class Scene {
  private readonly map: L.Map
  private readonly opts: SceneOptions
  private readonly geo: Geo
  private readonly palette: Record<string, string>
  private readonly waypoints: Map<string, Waypoint>
  private readonly timers = new Set<number>()

  private readonly cellLayer = L.layerGroup()
  private readonly roadLayer = L.layerGroup()
  private readonly routeLayer = L.layerGroup()
  private readonly focusLayer = L.layerGroup()
  private readonly markerLayer = L.layerGroup()

  private readonly cells = new Map<string, L.Rectangle>()
  private readonly roads = new Map<string, { line: L.Polyline; fence: L.Marker | null; cause: string | null }>()
  private readonly pois = new Map<string, { marker: L.Marker; sig: string }>()
  private readonly units = new Map<string, UnitEntry>()
  private readonly trails = new Map<string, { pts: Array<{ ll: LatLon; t: number }>; line: L.Polyline }>()
  private readonly routes = new Map<string, RouteEntry>()
  private readonly flames = new Map<string, L.Marker>()
  private readonly zoneMarks = new Map<string, L.Marker>()
  private readonly foci = new Map<string, { rect: L.Rectangle; mark: L.Marker }>()
  private readonly callers = new Map<string, { marker: L.Marker; sig: string }>()
  /** Pins de vecinos por Telegram, por `call_id` (`tg_<chat>`): un pin nuevo del mismo
   *  chat MUEVE el marcador, no crea otro (REQ del beat 4:25: `live` actualiza el pin). */
  private readonly citizens = new Map<string, { marker: L.Marker; sig: string }>()

  // Lo que se vio en la sincronización anterior, para saber qué ha ocurrido de nuevo y
  // lanzarle un anillo (REQ-309). La primera vez no se lanza ninguno: cargar el mapa a
  // mitad de un run no son cien eventos nuevos.
  private seen: {
    cells: Map<string, CellState>
    cuts: Set<string>
    unavailable: Set<string>
    foci: Set<string>
    callers: Set<string>
    citizens: Set<string>
  } | null = null
  private rings = 0
  // Movimiento continuo (REQ-304): el reloj de la simulación y el bucle de animación que
  // coloca cada unidad donde toca en ese reloj. El bucle NO pasa por React: muta los
  // marcadores directamente y no hace `setState` por fotograma (REQ-227).
  private readonly clock = new SimClock()
  private frameId = 0
  private zooming = false
  private fittedFoci = new Set<string>()
  private lastInput: SceneInput | null = null
  private tileLoaded = 0
  private tileErrors = 0

  constructor(container: HTMLElement, opts: SceneOptions) {
    this.opts = opts
    this.geo = { origin: opts.layer.origin, cellSize: opts.layer.hazard.cell_size }
    this.waypoints = new Map(opts.layer.waypoints.map((wp) => [wp.id, wp]))
    this.palette = Object.fromEntries(
      ['cell-burning', 'cell-at-risk', 'cell-burnt', 'cell-wet', 'cell-flooded', 'cell-dark', 'fire', 'warn', 'accent', 'dim', 'ink', 'water'].map(
        (name) => [name, token(name)],
      ),
    )

    // Minimalista (REQ-283): sin controles salvo el zoom, y sin animación de zoom ni de
    // fundido con "reducir movimiento" (REQ-297).
    this.map = L.map(container, {
      zoomControl: false,
      attributionControl: true,
      zoomAnimation: !opts.reducedMotion,
      fadeAnimation: !opts.reducedMotion,
      markerZoomAnimation: !opts.reducedMotion,
      preferCanvas: false,
    })
    L.control.zoom({ position: 'bottomright' }).addTo(this.map)
    this.map.attributionControl.setPrefix(false)

    const tiles = L.tileLayer(opts.tileUrl, {
      maxZoom: 19,
      attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    })
    tiles.on('tileload', () => {
      this.tileLoaded += 1
      opts.onTiles(this.tileLoaded, this.tileErrors)
    })
    tiles.on('tileerror', () => {
      this.tileErrors += 1
      opts.onTiles(this.tileLoaded, this.tileErrors)
    })
    tiles.addTo(this.map)

    // El orden de alta es el orden de pintado de los vectores: celdas, carreteras, rutas y
    // focos, y encima los marcadores.
    for (const group of [this.cellLayer, this.roadLayer, this.routeLayer, this.focusLayer, this.markerLayer]) {
      group.addTo(this.map)
    }

    this.drawValley()
    this.drawRoads()
    this.fitToValley()
    this.applyZoomClass()

    // Leaflet recoloca los marcadores por su cuenta durante la animación de zoom; si el bucle
    // también los movía, los dos se pisarían. Durante el zoom el bucle espera.
    this.map.on('zoomstart', () => {
      this.zooming = true
    })
    this.map.on('zoomend', () => {
      this.zooming = false
      this.applyZoomClass()
      this.layoutUnits()
    })

    // Con "reducir movimiento" no hay bucle: la unidad salta a su posición (REQ-307).
    if (!opts.reducedMotion) this.frameId = requestAnimationFrame(this.frame)
  }

  // --- vida del mapa ------------------------------------------------------------------

  destroy(): void {
    cancelAnimationFrame(this.frameId)
    for (const t of this.timers) window.clearTimeout(t)
    this.timers.clear()
    // Libera capas, eventos y el DOM de Leaflet: sin esto cada cambio de vista fugaría un
    // mapa entero (REQ-295).
    this.map.remove()
  }

  /** Vuelve a calcular el tamaño cuando el contenedor cambia (sidebar plegada, ventana). */
  invalidate(): void {
    this.map.invalidateSize()
  }

  zoomIn(): void {
    this.map.zoomIn()
  }

  private later(fn: () => void, ms: number): void {
    const id = window.setTimeout(() => {
      this.timers.delete(id)
      fn()
    }, ms)
    this.timers.add(id)
  }

  private applyZoomClass(): void {
    const z = this.map.getZoom()
    const el = this.map.getContainer()
    el.classList.toggle('vela-zoom-far', z < FAR_ZOOM)
    el.classList.toggle('vela-zoom-mid', z >= FAR_ZOOM && z < NEAR_ZOOM)
    el.classList.toggle('vela-zoom-near', z >= NEAR_ZOOM)
    el.classList.toggle('vela-zoom-firms-label', z >= FIRMS_LABEL_ZOOM)
  }

  private ll(x: number, z: number): LatLon {
    return toLatLon(this.opts.anchor, x, z)
  }

  // --- lo estático: el valle y sus carreteras -----------------------------------------

  /** El contorno del valle simulado (REQ-286): discontinuo y sin relleno, con su nombre. Es
   *  lo que impide leer el mapa del sim como geografía real (REQ-066, REQ-290). */
  private drawValley(): void {
    const { layer } = this.opts
    const points = [
      ...layer.waypoints.map((w) => ({ x: w.x, z: w.z })),
      ...layer.pois.map((p) => ({ x: p.x, z: p.z })),
    ]
    if (points.length === 0) return
    const pad = this.geo.cellSize * 2
    const xs = points.map((p) => p.x)
    const zs = points.map((p) => p.z)
    const a = this.ll(Math.min(...xs) - pad, Math.min(...zs) - pad)
    const b = this.ll(Math.max(...xs) + pad, Math.max(...zs) + pad)
    const rect = L.rectangle([a, b], {
      color: this.palette.dim,
      weight: 1.5,
      dashArray: '6 6',
      fill: false,
      interactive: false,
    })
    rect.addTo(this.cellLayer)
    // En la esquina noroeste y por FUERA del borde: en el centro cae encima de las
    // carreteras y las unidades, y dentro del borde tapa lo que esté cerca de él.
    L.tooltip({
      permanent: true,
      direction: 'top',
      offset: [0, -2],
      className: 'vela-valley-tip',
      interactive: false,
    })
      .setLatLng(a)
      .setContent(`valle simulado · ${layer.name}`)
      .addTo(this.cellLayer)
  }

  private drawRoads(): void {
    for (const road of this.opts.layer.roads) {
      const a = this.waypoints.get(road.a)
      const b = this.waypoints.get(road.b)
      if (!a || !b) continue
      const line = L.polyline([this.ll(a.x, a.z), this.ll(b.x, b.z)], {
        color: this.palette.dim,
        weight: 3,
        opacity: 0.55,
        interactive: false,
      })
      line.addTo(this.roadLayer)
      this.roads.set(road.id, { line, fence: null, cause: null })
    }
  }

  private fitToValley(): void {
    const { layer } = this.opts
    const pts: LatLon[] = [
      ...layer.waypoints.map((w) => this.ll(w.x, w.z)),
      ...layer.pois.map((p) => this.ll(p.x, p.z)),
    ]
    if (pts.length === 0) {
      this.map.setView([this.opts.anchor.lat0, this.opts.anchor.lon0], 12)
      return
    }
    this.map.fitBounds(L.latLngBounds(pts).pad(0.25), { animate: false })
  }

  // --- sincronización -----------------------------------------------------------------

  sync(input: SceneInput): void {
    this.lastInput = input
    const first = this.seen === null
    this.syncCells(input)
    this.syncRoads(input)
    this.syncPois(input)
    this.syncUnits(input)
    this.syncRoutes(input)
    this.syncFront(input)
    this.syncFoci(input)
    this.syncCallers(input)
    this.syncCitizens(input)
    this.layoutUnits()
    this.emitRings(input, first)
  }

  private cellStyle(state: CellState, cause: CellCause | undefined): L.PathOptions {
    const p = this.palette
    switch (state) {
      case 'burning':
        return { fillColor: p['cell-burning'], fillOpacity: 1, color: p.fire, weight: 1.5 }
      case 'at_risk':
        // Borde discontinuo y no relleno plano: se distingue del fuego por
        // la forma además del color (REQ-220).
        return { fillColor: p['cell-at-risk'], fillOpacity: 1, color: p.warn, weight: 1.5, dashArray: '4 3' }
      case 'burnt':
        // «Mojada»: la apagó un camión. Es el efecto de nuestra orden y se lee distinto
        // de la ceniza. La causa solo viene por evento: tras un snapshot vuelve a negro
        // (`useWorldView.seed` lo explica) y aquí no se inventa.
        if (cause === 'extinguished') {
          return { fillColor: p['cell-wet'], fillOpacity: 1, color: p.water, weight: 1 }
        }
        return { fillColor: p['cell-burnt'], fillOpacity: 1, color: p['cell-burnt'], weight: 0.5 }
      case 'flooded':
        return { fillColor: p['cell-flooded'], fillOpacity: 1, color: p['cell-flooded'], weight: 0.5 }
      case 'dark':
        return { fillColor: p['cell-dark'], fillOpacity: 1, color: p['cell-dark'], weight: 0.5 }
      case 'intact':
        return { fillOpacity: 0, weight: 0 }
    }
  }

  private syncCells({ view }: SceneInput): void {
    for (const [id, state] of view.cells) {
      const existing = this.cells.get(id)
      if (state === 'intact') {
        if (existing) {
          this.cellLayer.removeLayer(existing)
          this.cells.delete(id)
        }
        continue
      }
      const cause = view.cellCauses.get(id)
      if (existing) {
        existing.setStyle(this.cellStyle(state, cause))
        pathClass(existing)?.classList.remove('vela-geo-cell-grow')
        continue
      }
      const bounds = cellBounds(this.opts.anchor, id, this.geo)
      if (!bounds) continue
      const rect = L.rectangle(bounds, { ...this.cellStyle(state, cause), interactive: false, className: 'vela-geo-cell' })
      rect.addTo(this.cellLayer)
      if (state === 'burning') this.growFrom(rect, id, view.cells)
      this.cells.set(id, rect)
    }
    for (const [id, rect] of this.cells) {
      if (!view.cells.has(id)) {
        this.cellLayer.removeLayer(rect)
        this.cells.delete(id)
      }
    }
  }

  /** La celda que se enciende crece desde el lado de la vecina que ya ardía (REQ-308). */
  private growFrom(rect: L.Rectangle, id: string, cells: ReadonlyMap<string, CellState>): void {
    const el = pathClass(rect)
    const m = /^cell_(-?\d+)_(-?\d+)$/.exec(id)
    if (!el || !m) return
    const cx = Number(m[1])
    const cz = Number(m[2])
    const from: Array<[number, number, string]> = [
      [-1, 0, '0% 50%'], // arde la del oeste: crece desde su lado izquierdo
      [1, 0, '100% 50%'],
      [0, -1, '50% 0%'], // z menor = norte
      [0, 1, '50% 100%'],
    ]
    const hit = from.find(([dx, dz]) => cells.get(`cell_${cx + dx}_${cz + dz}`) === 'burning')
    el.style.transformOrigin = hit ? hit[2] : '50% 50%'
    el.classList.add('vela-geo-cell-grow')
  }

  private syncRoads({ view }: SceneInput): void {
    for (const [id, entry] of this.roads) {
      const cut = view.cutRoads.has(id)
      const cause = view.cutRoads.get(id) ?? null
      if (cut && !entry.fence) {
        entry.line.setStyle({ color: this.palette.warn, weight: 4, dashArray: '8 6', opacity: 0.95 })
        const pts = entry.line.getLatLngs() as L.LatLng[]
        const a = pts[0]
        const b = pts[1]
        if (a && b) {
          const mid: LatLon = [(a.lat + b.lat) / 2, (a.lng + b.lng) / 2]
          const fence = marker(mid, badgeIcon(ROAD_CUT_ICON, 'small'))
          if (cause) {
            fence.bindTooltip(cause, { permanent: true, direction: 'bottom', className: 'vela-cut-tip', interactive: false })
          }
          fence.addTo(this.markerLayer)
          entry.fence = fence
          entry.cause = cause
        }
      } else if (!cut && entry.fence) {
        entry.line.setStyle({ color: this.palette.dim, weight: 3, dashArray: undefined, opacity: 0.55 })
        this.markerLayer.removeLayer(entry.fence)
        entry.fence = null
        entry.cause = null
      }
    }
  }

  private syncPois({ view }: SceneInput): void {
    for (const poi of this.opts.layer.pois) {
      const threat = poiThreat(poi, view, this.geo)
      // Los civiles son una burbuja pegada al pueblo, no iconos sueltos (REQ-301.3); y solo
      // si hay expuestos o atrapados.
      const civ = threat.civ
      const sig = `${poi.kind}|${threat.level}|${civ ? `${civ.state}:${civ.count}` : ''}`
      const existing = this.pois.get(poi.id)
      if (existing?.sig === sig) continue

      const icon = badgeIcon(POI_ICON[poi.kind], 'poi', threat.level > 0 ? 'vela-poi-alert' : '', {
        text: poi.name,
        poi: true,
      })
      const root = icon.options.html
      if (civ && root instanceof HTMLElement) {
        const bubble = document.createElement('span')
        bubble.className = `vela-civ-bubble vela-tone-${CIV_TONE[civ.state]}`
        bubble.textContent = String(civ.count)
        bubble.title = `${civ.count} ${civ.state}`
        root.appendChild(bubble)
      }
      const at = this.ll(poi.x, poi.z)
      if (existing) {
        existing.marker.setIcon(icon)
        existing.sig = sig
      } else {
        const m = marker(at, icon, { zIndexOffset: 100 })
        m.addTo(this.markerLayer)
        this.pois.set(poi.id, { marker: m, sig })
      }
    }
  }

  /** Recoge las posiciones nuevas. NO mueve nada: quien mueve es `frame`, que interpola
   *  entre las muestras con el reloj de la simulación. Así el recorrido no depende de cuándo
   *  ni cómo lleguen los eventos. */
  private syncUnits({ view, unitKinds }: SceneInput): void {
    // El tiempo ha ido hacia atrás (run nuevo, replay que da la vuelta): las posiciones
    // antiguas ya no valen y la estela cruzaría el mapa de un extremo a otro.
    if (this.clock.observe(view.tSim, performance.now())) {
      for (const [id, entry] of this.units) {
        entry.track.clear()
        this.dropTrail(id)
      }
    }

    for (const u of view.units.values()) {
      const kind = unitKinds.get(u.id) ?? 'crew'
      let entry = this.units.get(u.id)

      if (!entry) {
        const at = this.ll(u.x, u.z)
        const icon = badgeIcon(UNIT_ICON[kind], 'unit', '', { text: u.id.replace(/^unit_/, ''), minor: true })
        const root = icon.options.html
        if (!(root instanceof HTMLElement)) continue
        const m = marker(at, icon, { zIndexOffset: 1000 })
        m.addTo(this.markerLayer)
        entry = {
          marker: m,
          inner: root,
          kind,
          status: u.status,
          track: new UnitTrack(),
          shown: at,
          shownXZ: { x: u.x, z: u.z },
          flip: false,
        }
        this.units.set(u.id, entry)
        this.paintUnit(entry)
      }

      entry.track.push({ t: u.t, x: u.x, z: u.z })
      if (entry.status !== u.status) {
        entry.status = u.status
        this.paintUnit(entry)
      }
      // Sin bucle (reducir movimiento) no hay interpolación: la unidad salta a la última.
      if (this.opts.reducedMotion) this.placeUnit(u.id, entry, { x: u.x, z: u.z }, view.tSim)
    }

    for (const [id, entry] of this.units) {
      if (view.units.has(id)) continue
      this.markerLayer.removeLayer(entry.marker)
      this.units.delete(id)
      this.dropTrail(id)
    }
  }

  /** Un fotograma: cada unidad se coloca donde tocaba hace `lag()` de simulación. */
  private readonly frame = (wallMs: number): void => {
    this.frameId = requestAnimationFrame(this.frame)
    if (this.zooming || this.units.size === 0) return
    const now = this.clock.now(wallMs)
    let moved = false
    for (const [id, entry] of this.units) {
      const at = entry.track.shownAt(now)
      if (at && this.placeUnit(id, entry, at, now)) moved = true
    }
    if (moved) this.layoutUnits()
  }

  /** Pone la unidad en `xz`. Devuelve `true` si se ha movido. Es el único sitio que mueve una
   *  unidad, y de él cuelgan lo que tiene que ir con ella: la estela y el tramo recorrido de
   *  su ruta. */
  private placeUnit(id: string, entry: UnitEntry, xz: { x: number; z: number }, tSim: number): boolean {
    const at = this.ll(xz.x, xz.z)
    if (at[0] === entry.shown[0] && at[1] === entry.shown[1]) return false

    // El icono no rota (un camión boca abajo no se reconoce): se voltea según el sentido
    // este-oeste del movimiento.
    const dx = at[1] - entry.shown[1]
    if (Math.abs(dx) > 1e-9 && entry.flip !== dx < 0) {
      entry.flip = dx < 0
      this.paintUnit(entry)
    }
    entry.marker.setLatLng(at)
    entry.shown = at
    entry.shownXZ = xz
    this.trail(id, at, entry.status, tSim)
    const route = this.routes.get(id)
    if (route) this.splitRoute(route, xz.x, xz.z)
    return true
  }

  private paintUnit(entry: UnitEntry): void {
    const { kind } = entry
    const motion = kind === 'drone' ? 'vela-hover' : kind === 'crew' ? 'vela-sway' : 'vela-siren'
    entry.inner.className = `vela-unit-inner vela-unit-${entry.status} ${motion}${entry.flip ? ' vela-flip' : ''}`
  }

  /** Estela corta de las unidades en marcha (REQ-306): se lee de dónde vienen sin guardar
   *  una historia infinita. Se añade un punto cada `TRAIL_STEP_PX` píxeles de recorrido, no
   *  en cada fotograma: 60 puntos por segundo serían una línea que ni se ve ni se puede
   *  dibujar barata. */
  private trail(id: string, at: LatLon, status: UnitStatus, tSim: number): void {
    if (status !== 'moving') {
      this.dropTrail(id)
      return
    }
    let trail = this.trails.get(id)
    if (!trail) {
      trail = {
        pts: [],
        line: L.polyline([], { color: this.palette.accent, weight: 3, opacity: 0.35, interactive: false }),
      }
      trail.line.addTo(this.routeLayer)
      this.trails.set(id, trail)
    }
    const last = trail.pts.at(-1)
    const far =
      !last ||
      this.map.latLngToLayerPoint(at).distanceTo(this.map.latLngToLayerPoint(last.ll)) >= TRAIL_STEP_PX
    if (far) trail.pts.push({ ll: at, t: tSim })
    trail.pts = trail.pts.filter((p) => tSim - p.t <= TRAIL_SIM_S).slice(-TRAIL_MAX_POINTS)
    // El último tramo llega hasta la unidad, así la estela no se despega de ella.
    trail.line.setLatLngs([...trail.pts.map((p) => p.ll), at])
  }

  private dropTrail(id: string): void {
    const trail = this.trails.get(id)
    if (!trail) return
    this.routeLayer.removeLayer(trail.line)
    this.trails.delete(id)
  }

  /** Las unidades que caen a menos de 24 px se abren en abanico (REQ-301.5): nunca una
   *  insignia encima de otra. Se hace con un desplazamiento CSS del interior, que no se
   *  pelea con el `transform` con el que Leaflet coloca y desliza el marcador. */
  private layoutUnits(): void {
    const items = [...this.units.values()].map((u) => ({ u, p: this.map.latLngToLayerPoint(u.shown) }))
    // Un pueblo cuenta como un ocupante fijo del sitio: una unidad que está EN el pueblo
    // (el camión en su base, la ambulancia en el hospital) se aparta a su alrededor en vez
    // de taparle la insignia y el nombre.
    const poiPoints = this.opts.layer.pois.map((p) => this.map.latLngToLayerPoint(this.ll(p.x, p.z)))
    const used = new Set<number>()
    items.forEach((a, i) => {
      if (used.has(i)) return
      const group = items.filter((b, j) => !used.has(j) && a.p.distanceTo(b.p) < 24)
      group.forEach((g) => used.add(items.indexOf(g)))
      const atPoi = poiPoints.some((p) => p.distanceTo(a.p) < 24)
      group.forEach((g, k) => {
        const angle = (2 * Math.PI * k) / group.length - (atPoi ? Math.PI / 4 : Math.PI / 2)
        const r = atPoi ? 28 : group.length > 1 ? 18 : 0
        g.u.inner.style.setProperty('--fan-x', `${Math.round(Math.cos(angle) * r)}px`)
        g.u.inner.style.setProperty('--fan-y', `${Math.round(Math.sin(angle) * r)}px`)
      })
    })
  }

  private syncRoutes({ plan, view, tasks }: SceneInput): void {
    const active = new Set<string>()
    for (const a of plan?.assignments ?? []) {
      const unit = view.units.get(a.unit_id)
      if (!unit) continue
      const coords = a.route
        .map((id) => this.waypoints.get(id))
        .filter((w): w is Waypoint => w !== undefined)
      if (coords.length === 0) continue
      active.add(a.unit_id)

      const sig = `${a.task_id}|${a.route.join('>')}`
      let entry = this.routes.get(a.unit_id)
      if (entry && entry.sig !== sig) {
        this.fadeOut(entry)
        entry = undefined
      }
      if (!entry) {
        const done = L.polyline([], { color: this.palette.accent, weight: 4, opacity: 0.3, interactive: false, className: 'vela-geo-route-in' })
        const pending = L.polyline([], { color: this.palette.accent, weight: 4, opacity: 0.75, interactive: false, className: 'vela-geo-route-in' })
        done.addTo(this.routeLayer)
        pending.addTo(this.routeLayer)
        entry = { sig, coords, done, pending, task: this.taskBadge(a.unit_id, a.task_id, coords, tasks, a.eta_s) }
        this.routes.set(a.unit_id, entry)
      }
      // El tramo recorrido sigue a la unidad TAL COMO SE ENSEÑA, no a su última posición
      // emitida, que va por delante: si no, la ruta se aclararía antes de que llegue el icono.
      const at = this.units.get(a.unit_id)?.shownXZ ?? { x: unit.x, z: unit.z }
      this.splitRoute(entry, at.x, at.z)
    }
    for (const [unitId, entry] of this.routes) {
      if (active.has(unitId)) continue
      this.fadeOut(entry)
      this.routes.delete(unitId)
    }
  }

  /** La ruta se parte en el tramo ya recorrido (claro) y el que queda (acento), cortando
   *  por el segmento más cercano a la unidad (REQ-305). */
  private splitRoute(entry: RouteEntry, x: number, z: number): void {
    const route = entry.coords
    let seg = 0
    let best = Infinity
    for (let i = 0; i < route.length - 1; i += 1) {
      const a = route[i]
      const b = route[i + 1]
      if (!a || !b) continue
      const d = distToSegment(x, z, a.x, a.z, b.x, b.z)
      if (d < best) {
        best = d
        seg = i
      }
    }
    const here = this.ll(x, z)
    const pts = route.map((w) => this.ll(w.x, w.z))
    entry.done.setLatLngs([...pts.slice(0, seg + 1), here])
    entry.pending.setLatLngs([here, ...pts.slice(seg + 1)])
  }

  /** Qué va a hacer la unidad, en el destino (REQ-302). La ETA se queda fija 10 s: es el
   *  clímax de un replan (REQ-226). Después, solo al pasar el ratón. */
  private taskBadge(
    unitId: string,
    taskId: string,
    route: Waypoint[],
    tasks: Record<string, Task>,
    etaS: number,
  ): L.Marker | null {
    const kind = tasks[taskId]?.kind
    const end = route.at(-1)
    if (!kind || !end) return null
    const label = `${Math.round(etaS)} s`
    // Satélite abajo a la derecha del destino: el pueblo o el punto de la tarea quedan libres.
    const icon = badgeIcon(TASK_ICON[kind], 'small', 'vela-zoom-hide', undefined, { x: 20, y: 20 })
    const m = marker(this.ll(end.x, end.z), icon, {
      title: `${TASK_ICON[kind].label} · ${unitId.replace(/^unit_/, '')} · ${label}`,
      zIndexOffset: 500,
    })
    m.bindTooltip(label, { permanent: true, direction: 'right', className: 'vela-eta-tip', interactive: false })
    m.addTo(this.markerLayer)
    this.later(() => {
      m.unbindTooltip()
      m.bindTooltip(label, { direction: 'right', className: 'vela-eta-tip', interactive: false })
    }, ETA_LABEL_MS)
    return m
  }

  private fadeOut(entry: RouteEntry): void {
    for (const line of [entry.done, entry.pending]) {
      const el = pathClass(line)
      el?.classList.remove('vela-geo-route-in')
      el?.classList.add('vela-geo-route-out')
    }
    if (entry.task) this.markerLayer.removeLayer(entry.task)
    this.later(() => {
      this.routeLayer.removeLayer(entry.done)
      this.routeLayer.removeLayer(entry.pending)
    }, FADE_MS)
  }

  /** Llamas en el frente del fuego y un icono por zona (REQ-301). */
  private syncFront({ view }: SceneInput): void {
    const front = new Set(fireFront(view.cells))
    for (const id of front) {
      if (this.flames.has(id)) continue
      const at = this.cellCenter(id)
      if (!at) continue
      const icon = badgeIcon(CELL_ICON.burning, 'small', 'vela-geo-flame')
      const m = marker(at, icon, { interactive: false, zIndexOffset: 300 })
      const root = icon.options.html
      if (root instanceof HTMLElement) root.style.setProperty('--delay', `${delayOf(id)}s`)
      m.addTo(this.markerLayer)
      this.flames.set(id, m)
    }
    for (const [id, m] of this.flames) {
      if (front.has(id)) continue
      this.markerLayer.removeLayer(m)
      this.flames.delete(id)
    }

    const wanted = new Map(zones(view.cells).map((z) => [`${z.state}:${z.cellId}`, z]))
    for (const [key, z] of wanted) {
      if (this.zoneMarks.has(key)) continue
      const at = this.cellCenter(z.cellId)
      if (!at) continue
      const m = marker(at, badgeIcon(CELL_ICON[z.state], 'small', 'vela-zoom-hide'), {
        interactive: false,
        title: CELL_ICON[z.state].label,
        zIndexOffset: 200,
      })
      m.addTo(this.markerLayer)
      this.zoneMarks.set(key, m)
    }
    for (const [key, m] of this.zoneMarks) {
      if (wanted.has(key)) continue
      this.markerLayer.removeLayer(m)
      this.zoneMarks.delete(key)
    }
  }

  private cellCenter(id: string): LatLon | null {
    const b = cellBounds(this.opts.anchor, id, this.geo)
    return b ? [(b[0][0] + b[1][0]) / 2, (b[0][1] + b[1][1]) / 2] : null
  }

  /** Los focos reales de FIRMS: el cuadrado de su píxel de 375 m, con borde discontinuo y
   *  sin relleno sólido, para que no se confundan con las celdas del sim (REQ-264, 288). */
  private syncFoci({ foci }: SceneInput): void {
    const keys = new Set(foci.map((f) => f.key))
    for (const f of foci) {
      if (this.foci.has(f.key)) continue
      const rect = L.rectangle(squareAround(f.lat, f.lon, f.sizeM, this.opts.anchor.lat0), {
        color: this.palette.fire,
        weight: 2,
        dashArray: '6 4',
        fillColor: this.palette.fire,
        fillOpacity: 0.15,
        interactive: false,
      })
      rect.addTo(this.focusLayer)
      const mark = marker([f.lat, f.lon], badgeIcon(FIRMS_ICON, 'small'), {
        title: `FIRMS ${f.satellite} ${f.time}`,
        zIndexOffset: 400,
      })
      mark.bindTooltip(`FIRMS ${f.satellite} ${f.time}`, { permanent: true, direction: 'right', className: 'vela-firms-tip', interactive: false })
      mark.addTo(this.markerLayer)
      this.foci.set(f.key, { rect, mark })
    }
    for (const [key, entry] of this.foci) {
      if (keys.has(key)) continue
      this.focusLayer.removeLayer(entry.rect)
      this.markerLayer.removeLayer(entry.mark)
      this.foci.delete(key)
    }
    this.fitNewFoci(foci)
  }

  /** Solo se reencuadra cuando aparece un foco fuera de lo que se ve, y una vez por foco:
   *  el mapa no salta con cada evento (REQ-284). */
  private fitNewFoci(foci: Focus[]): void {
    const fresh = foci.filter((f) => !this.fittedFoci.has(f.key))
    if (fresh.length === 0) return
    for (const f of fresh) this.fittedFoci.add(f.key)
    const bounds = this.map.getBounds()
    if (fresh.every((f) => bounds.contains([f.lat, f.lon]))) return
    const all = L.latLngBounds([...foci.map((f): LatLon => [f.lat, f.lon]), bounds.getSouthWest(), bounds.getNorthEast()])
    this.map.fitBounds(all.pad(0.15), { animate: false })
  }

  /** La persona que llama, como satélite de su pueblo (REQ-310). */
  private syncCallers({ callers }: SceneInput): void {
    const poisById = new Map(this.opts.layer.pois.map((p) => [p.id, p]))
    const alive = new Set<string>()
    for (const c of callers.placed) {
      const poi = poisById.get(c.poiId)
      if (!poi) continue
      alive.add(c.callId)
      const sig = `${c.ended}|${c.direction}`
      const existing = this.callers.get(c.callId)
      if (existing?.sig === sig) {
        if (c.lastLine) existing.marker.setTooltipContent(c.lastLine)
        continue
      }
      const icon = badgeIcon(
        CALLER_ICON,
        'poi',
        `${c.ended ? 'vela-geo-ended' : 'vela-geo-voice'}`,
        { text: c.direction === 'inbound' ? '↘ entrante' : '↗ saliente', minor: true },
        // Como satélite arriba a la derecha del pueblo, para no taparlo.
        { x: 28, y: -28 },
      )
      if (existing) this.markerLayer.removeLayer(existing.marker)
      const m = marker(this.ll(poi.x, poi.z), icon, { zIndexOffset: 700 })
      m.bindTooltip(c.lastLine || (c.direction === 'inbound' ? 'llamada entrante' : 'llamada saliente'), {
        direction: 'top',
        className: 'vela-call-tip',
      })
      m.on('click', () => this.opts.onOpenCall(c.callId))
      m.addTo(this.markerLayer)
      this.callers.set(c.callId, { marker: m, sig })
    }
    for (const [id, entry] of this.callers) {
      if (alive.has(id)) continue
      this.markerLayer.removeLayer(entry.marker)
      this.callers.delete(id)
    }
  }

  /** El pin del vecino por Telegram, en el (x, z) que el core proyectó y con la misma
   *  ancla que todo lo demás (REQ-285): queda sobre Pueblo B en León aunque el pin real
   *  saliera de Madrid, porque la maqueta vive en León. Varios chats → varios pins; un
   *  pin nuevo del mismo chat mueve el suyo. Clic: la tarjeta del chat. */
  private syncCitizens({ view }: SceneInput): void {
    for (const c of view.citizens.values()) {
      const at = this.ll(c.x, c.z)
      const sig = `${c.x},${c.z}|${c.poiName ?? ''}|${c.live}`
      const existing = this.citizens.get(c.callId)
      if (existing?.sig === sig) continue
      if (existing) {
        // Mismo chat, sitio nuevo (`live`): se mueve, no se duplica.
        existing.marker.setLatLng(at)
        existing.sig = sig
        continue
      }
      const icon = badgeIcon(CITIZEN_ICON, 'poi', `vela-geo-citizen${c.live ? ' vela-geo-live' : ''}`, {
        text: c.poiName ? `vecino · ${c.poiName}` : 'vecino',
      })
      const m = marker(at, icon, { zIndexOffset: 800 })
      m.bindTooltip(c.poiName ? `vecino por Telegram · junto a ${c.poiName}` : 'vecino por Telegram · pin sin anclar', {
        direction: 'top',
        className: 'vela-call-tip',
      })
      m.on('click', () => this.opts.onOpenCall(c.callId))
      m.addTo(this.markerLayer)
      this.citizens.set(c.callId, { marker: m, sig })
    }
    for (const [id, entry] of this.citizens) {
      if (view.citizens.has(id)) continue
      this.markerLayer.removeLayer(entry.marker)
      this.citizens.delete(id)
    }
  }

  // --- anillos de evento ---------------------------------------------------------------

  /** Lo que acaba de pasar se marca un momento (REQ-309). A 80 eventos/s se descartan los
   *  anillos que sobran en vez de encolarlos: encolar retrasaría el anillo de algo que ya
   *  pasó hace un rato. */
  private emitRings({ view, foci, callers }: SceneInput, first: boolean): void {
    const now = {
      cells: new Map(view.cells),
      cuts: new Set(view.cutRoads.keys()),
      unavailable: new Set([...view.units.values()].filter((u) => u.status === 'unavailable').map((u) => u.id)),
      foci: new Set(foci.map((f) => f.key)),
      callers: new Set(callers.placed.filter((c) => !c.ended).map((c) => c.callId)),
      citizens: new Set(view.citizens.keys()),
    }
    const before = this.seen
    this.seen = now
    if (first || !before) return

    for (const [id, state] of now.cells) {
      if (state === 'burning' && before.cells.get(id) !== 'burning') this.ring(this.cellCenter(id))
    }
    for (const id of now.cuts) {
      if (before.cuts.has(id)) continue
      const line = this.roads.get(id)?.line.getBounds().getCenter()
      if (line) this.ring([line.lat, line.lng])
    }
    for (const id of now.unavailable) {
      if (!before.unavailable.has(id)) this.ring(this.units.get(id)?.shown ?? null)
    }
    for (const key of now.foci) {
      const f = foci.find((x) => x.key === key)
      if (!before.foci.has(key) && f) this.ring([f.lat, f.lon])
    }
    for (const id of now.callers) {
      if (before.callers.has(id)) continue
      const poiId = callers.placed.find((c) => c.callId === id)?.poiId
      const poi = this.opts.layer.pois.find((p) => p.id === poiId)
      if (poi) this.ring(this.ll(poi.x, poi.z))
    }
    // Un pin nuevo es el momento del beat 4:25: se marca donde cayó.
    for (const id of now.citizens) {
      if (before.citizens.has(id)) continue
      const c = view.citizens.get(id)
      if (c) this.ring(this.ll(c.x, c.z))
    }
  }

  private ring(at: LatLon | null): void {
    if (!at || this.rings >= MAX_RINGS || this.opts.reducedMotion) return
    this.rings += 1
    const m = L.marker(at, {
      icon: L.divIcon({ className: '', html: '<div class="vela-geo-ring"></div>', iconSize: [0, 0] }),
      interactive: false,
      keyboard: false,
      zIndexOffset: 900,
    })
    m.addTo(this.markerLayer)
    this.later(() => {
      this.markerLayer.removeLayer(m)
      this.rings -= 1
    }, 800)
  }

  /** Vuelve a pintar con el último estado, por ejemplo tras cambiar el tamaño. */
  refresh(): void {
    if (this.lastInput) this.sync(this.lastInput)
  }
}

function distToSegment(px: number, pz: number, ax: number, az: number, bx: number, bz: number): number {
  const dx = bx - ax
  const dz = bz - az
  const len2 = dx * dx + dz * dz
  const t = len2 === 0 ? 0 : Math.max(0, Math.min(1, ((px - ax) * dx + (pz - az) * dz) / len2))
  return Math.hypot(px - (ax + t * dx), pz - (az + t * dz))
}
