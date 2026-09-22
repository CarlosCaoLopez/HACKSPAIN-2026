// Las capas del mapa, cada una con una responsabilidad y ningún estado.
//
// Revividas de `apps/dashboard/src/map/layers.tsx`, que SPEC-008 borró al cambiar el
// mapa esquemático por el de OpenStreetMap. Aquí vuelven a servir porque esta página
// no tiene ancla real que respetar: es una simulación y lo dice.
//
// Reciben datos ya proyectables y devuelven SVG. Orden (SPEC-006 REQ-076 ampliado), de
// abajo arriba: terreno → celdas → viento → humo → carreteras → halos de problema → POIs
// → flechas → unidades → brújula. Las unidades encima de todo porque son lo que se mira.
//
// Los fenómenos (fuego, humo, viento, ondas, pulso) son CSS sobre SVG y no llevan bucle en
// React (REQ-227): el navegador anima, el componente solo dice qué hay. Y son RENDER de
// estados ya emitidos, nunca simulación: el humo sale de una celda que ya está `burning` y
// sopla hacia donde el viento ya dice que sopla.
import type { CSSProperties } from 'react'

import type { CellState, POI, Plan, RoadEdge, Waypoint, Wind } from '../../types'
import type { CivilianView, UnitView } from '../worldview'
import { CIV_STATE } from '../../story/labels'
import { seconds } from '../../story/format'
import { cellCenter, projectCell, type Box, type Geo } from './project'
import type { PoiThreat } from '../../geo/problems'

/** Los colores del mapa salen de `@theme` y no hay un solo literal en este fichero
 *  (REQ-199, REQ-230): un valor suelto aquí dentro no se puede comparar con el del banner
 *  sin abrir dos ficheros, y esa comparación —que `burning` y `vela-replan` no se confundan
 *  a diez metros— es la que decide si el banner sigue siendo un acontecimiento.
 *
 *  El mapa se pinta en CLARO: `--color-vela-ground` es pálido, la carretera es lo oscuro
 *  y `ALERT`/`GLOW` se usan solo en formas, nunca en texto (el texto va en `INK` y `WARN`,
 *  que ya son oscuros). Si alguna vez se vuelve al fondo oscuro, lo que hay que revisar
 *  son las opacidades de relleno de este fichero, no los tokens. */
const INK = 'var(--color-vela-ink)'
const ACCENT = 'var(--color-vela-accent)'
const WARN = 'var(--color-vela-warn)'
const ALERT = 'var(--color-vela-alert)'
const GLOW = 'var(--color-vela-glow)'
const HALO = 'var(--color-vela-halo)'
const MARKER = 'var(--color-vela-marker)'
const MARKER_EDGE = 'var(--color-vela-marker-edge)'
const ROAD = 'var(--color-vela-road)'
const ROAD_SHADOW = 'var(--color-vela-road-shadow)'

/** Texto legible sobre CUALQUIER zona de la imagen (REQ-218): trazo claro por detrás
 *  (`paint-order: stroke`) y relleno oscuro. Sin esto, una etiqueta oscura se pierde en la
 *  vegetación y una clara en los claros de los pueblos. */
function halo(stroke: number): CSSProperties {
  return { paintOrder: 'stroke', stroke: HALO, strokeWidth: stroke * 2.4, strokeLinejoin: 'round' }
}

/** Un desfase estable por id, para que las llamas no latan todas a la vez. Estable y no
 *  aleatorio: un `Math.random()` en render cambiaría el desfase en cada evento. */
function phase(id: string, span: number): number {
  let h = 0
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) | 0
  return (Math.abs(h) % 1000) / 1000 * span
}

/** El sentido hacia el que SOPLA el viento, como vector unitario en el mundo (x, z).
 *
 *  `bearing_deg` es de dónde VIENE (0 = norte, horario): el YAML lo dice —«270: viene del
 *  oeste, empuja hacia el este»—, así que se empuja hacia bearing + 180. Con el norte en −z. */
function blowTo(bearingDeg: number): { dx: number; dz: number; deg: number } {
  const a = ((bearingDeg + 180) * Math.PI) / 180
  return { dx: Math.sin(a), dz: -Math.cos(a), deg: (bearingDeg + 180) % 360 }
}

/** Definiciones compartidas: la punta de las flechas, el rayado del riesgo y el resplandor
 *  del fuego. Una vez, aquí arriba, y no una por celda. */
export function MapDefs({ stroke, cellSize }: { stroke: number; cellSize: number }) {
  return (
    <defs>
      <marker
        id="vela-arrow"
        viewBox="0 0 10 10"
        refX="8"
        refY="5"
        markerWidth={stroke * 4}
        markerHeight={stroke * 4}
        orient="auto-start-reverse"
      >
        <path d="M 0 0 L 10 5 L 0 10 z" fill={ACCENT} />
      </marker>
      {/* `at_risk` es un rayado y no un relleno plano (REQ-220): se distingue del fuego por
          textura además de por color, para quien no distinga naranja de ámbar. */}
      <pattern
        id="vela-hatch"
        width={cellSize / 3}
        height={cellSize / 3}
        patternUnits="userSpaceOnUse"
        patternTransform="rotate(45)"
      >
        <rect width={cellSize / 3} height={cellSize / 3} fill={ALERT} fillOpacity={0.3} />
        <line y2={cellSize / 3} stroke={ALERT} strokeWidth={cellSize / 9} strokeOpacity={0.85} />
      </pattern>
      <filter id="vela-glow" x="-50%" y="-50%" width="200%" height="200%">
        <feGaussianBlur stdDeviation={cellSize * 0.7} />
      </filter>
    </defs>
  )
}

/** La imagen de satélite, en coordenadas del mundo (REQ-215): escala con el `viewBox` sin
 *  lógica extra. `crisp-edges` no: el terreno es suave y el escalado bilineal lo mejora. */
export function TerrainLayer({ url, box }: { url: string; box: Box }) {
  return (
    <image
      href={url}
      x={box.minX}
      y={box.minZ}
      width={box.width}
      height={box.height}
      preserveAspectRatio="none"
    />
  )
}

/** Sigue siendo una tabla explícita sobre `CellState` y no una plantilla de nombres: así
 *  un estado nuevo de P1 rompe la compilación en vez de pintar una celda transparente. */
const CELL_FILL: Record<CellState, string | null> = {
  intact: null,
  at_risk: 'url(#vela-hatch)',
  burning: 'var(--color-vela-cell-burning)',
  burnt: 'var(--color-vela-cell-burnt)',
  flooded: 'var(--color-vela-cell-flooded)',
  dark: 'var(--color-vela-cell-dark)',
}

export function CellsLayer({ cells, geo }: { cells: Map<string, CellState>; geo: Geo }) {
  const burning: string[] = []
  const rest: Array<[string, CellState]> = []
  for (const [id, state] of cells) {
    if (state === 'intact') continue
    if (state === 'burning') burning.push(id)
    else rest.push([id, state])
  }
  return (
    <g>
      {rest.map(([id, state]) => {
        const box = projectCell(id, geo)
        const fill = CELL_FILL[state]
        if (!box || !fill) return null
        return (
          <rect
            key={id}
            x={box.minX}
            y={box.minZ}
            width={box.width}
            height={box.height}
            fill={fill}
            // Las ondas de agua son lentas y desfasadas por celda (REQ-221).
            className={state === 'flooded' ? 'vela-ripple' : undefined}
            style={
              state === 'flooded' ? { animationDelay: `${phase(id, 4)}s` } : undefined
            }
          />
        )
      })}
      {/* El resplandor va DEBAJO y es un solo grupo con un solo filtro: un desenfoque por
          celda con cien celdas ardiendo se comería el navegador (REQ-227). */}
      <g filter="url(#vela-glow)" className="vela-flame">
        {burning.map((id) => {
          const box = projectCell(id, geo)
          if (!box) return null
          return (
            <rect
              key={id}
              x={box.minX}
              y={box.minZ}
              width={box.width}
              height={box.height}
              fill={GLOW}
            />
          )
        })}
      </g>
      {burning.map((id) => {
        const box = projectCell(id, geo)
        if (!box) return null
        return (
          <rect
            key={id}
            x={box.minX}
            y={box.minZ}
            width={box.width}
            height={box.height}
            fill={CELL_FILL.burning ?? undefined}
            stroke={GLOW}
            strokeWidth={geo.cellSize / 14}
            className="vela-flame"
            style={{ animationDelay: `${phase(id, 1.4)}s` }}
          />
        )
      })}
    </g>
  )
}

/** Estelas de viento (REQ-222): un campo de trazos cortos que recorren el encuadre en el
 *  sentido en que sopla, con velocidad proporcional a `speed`. El campo entero es un grupo
 *  girado por CSS, así que al girar el viento (inject de t≈150) gira con transición de
 *  ~600 ms en vez de saltar. Debajo de las unidades: es ambiente, no información. */
export function WindField({
  wind,
  box,
  stroke,
}: {
  wind: Wind | null
  box: Box
  stroke: number
}) {
  if (!wind || wind.speed <= 0) return null
  const { deg } = blowTo(wind.bearing_deg)
  const cx = box.minX + box.width / 2
  const cz = box.minZ + box.height / 2
  // Un cuadrado de la diagonal: girado, sigue cubriendo el encuadre entero.
  const side = Math.hypot(box.width, box.height)
  const len = stroke * 14
  const travel = side * 0.16
  const duration = Math.max(1.6, 4 / Math.max(0.4, wind.speed))
  const streaks = Array.from({ length: 40 }, (_, i) => {
    // Cuadrícula desplazada, no aleatoria: cubre parejo y es la misma en cada render.
    const col = i % 8
    const row = Math.floor(i / 8)
    return {
      i,
      x: -side / 2 + ((col + 0.5 + (row % 2) * 0.5) / 8.5) * side,
      y: -side / 2 + ((row + 0.5) / 5) * side,
    }
  })
  return (
    <g transform={`translate(${cx} ${cz})`} pointerEvents="none">
      <g className="vela-wind" style={{ transform: `rotate(${deg - 90}deg)` }}>
        {streaks.map((s) => (
          <line
            key={s.i}
            className="vela-gust"
            x1={s.x}
            y1={s.y}
            x2={s.x + len}
            y2={s.y}
            stroke="var(--color-vela-gust)"
            strokeWidth={stroke * 0.55}
            strokeLinecap="round"
            style={
              {
                '--dx': `${travel}px`,
                '--dz': '0px',
                animationDuration: `${duration}s`,
                animationDelay: `${phase(`g${s.i}`, duration)}s`,
              } as CSSProperties
            }
          />
        ))}
      </g>
    </g>
  )
}

/** Humo (REQ-219): penachos que salen de las celdas que YA arden y derivan a favor del
 *  viento. Tope de 40 en pantalla: si arden más, se reparten (una de cada `n`). No es una
 *  columna de humo simulada, es el adorno de un dato que ya llegó. */
const MAX_PLUMES = 40

export function SmokeLayer({
  cells,
  geo,
  wind,
}: {
  cells: Map<string, CellState>
  geo: Geo
  wind: Wind | null
}) {
  const burning = [...cells].filter(([, s]) => s === 'burning').map(([id]) => id)
  if (burning.length === 0) return null
  const step = Math.ceil(burning.length / MAX_PLUMES)
  const { dx, dz } = wind ? blowTo(wind.bearing_deg) : { dx: 0, dz: -1 }
  // Sin viento, el humo sube: hacia el norte del mapa, con recorrido corto.
  const reach = geo.cellSize * (wind ? 2.5 + wind.speed * 2.5 : 2)
  return (
    <g pointerEvents="none">
      {burning
        .filter((_, i) => i % step === 0)
        .map((id) => {
          const c = cellCenter(id, geo)
          if (!c) return null
          return (
            <circle
              key={id}
              className="vela-smoke"
              cx={c.x}
              cy={c.z}
              r={geo.cellSize * 0.9}
              fill="var(--color-vela-smoke)"
              style={
                {
                  '--dx': `${dx * reach}px`,
                  '--dz': `${dz * reach}px`,
                  animationDelay: `${phase(id, 6)}s`,
                } as CSSProperties
              }
            />
          )
        })}
    </g>
  )
}

export function RoadsLayer({
  roads,
  waypoints,
  cutRoads,
  stroke,
}: {
  roads: RoadEdge[]
  waypoints: Map<string, Waypoint>
  cutRoads: Map<string, string | null>
  stroke: number
}) {
  return (
    <g>
      {roads.map((road) => {
        const a = waypoints.get(road.a)
        const b = waypoints.get(road.b)
        if (!a || !b) return null
        const cut = cutRoads.has(road.id)
        const cause = cutRoads.get(road.id)
        const mx = (a.x + b.x) / 2
        const mz = (a.z + b.z) / 2
        return (
          <g key={road.id}>
            {/* Sombra y luego el asfalto: una carretera sobre satélite es una franja clara
                con borde, no una línea fina. */}
            <line
              x1={a.x}
              y1={a.z}
              x2={b.x}
              y2={b.z}
              stroke={ROAD_SHADOW}
              strokeWidth={stroke * 3}
              strokeLinecap="round"
            />
            <line
              x1={a.x}
              y1={a.z}
              x2={b.x}
              y2={b.z}
              stroke={cut ? ALERT : ROAD}
              strokeWidth={cut ? stroke * 2 : stroke * 1.7}
              strokeLinecap="round"
              strokeDasharray={cut ? `${stroke * 3} ${stroke * 2.4}` : undefined}
            />
            {cut && (
              <>
                {/* El halo de la carretera cortada (REQ-223): pulsa sobre el punto medio,
                    que es donde se mira. */}
                <circle
                  className="vela-pulse"
                  cx={mx}
                  cy={mz}
                  r={stroke * 7}
                  fill="none"
                  stroke={ALERT}
                  strokeWidth={stroke * 1.2}
                />
                <text
                  x={mx}
                  y={mz - stroke * 3}
                  fill={INK}
                  fontSize={stroke * 5.8}
                  fontWeight={700}
                  textAnchor="middle"
                  style={halo(stroke)}
                >
                  ✕ cortada
                </text>
                {/* La causa va EN el mapa, no en un tooltip: un tooltip no existe en un
                    proyector. */}
                {cause && (
                  <text
                    x={mx}
                    y={mz + stroke * 6}
                    fill={WARN}
                    fontSize={stroke * 5}
                    fontWeight={500}
                    textAnchor="middle"
                    style={halo(stroke)}
                  >
                    {cause}
                  </text>
                )}
              </>
            )}
          </g>
        )
      })}
    </g>
  )
}

/** Zonas con problema (REQ-223): un halo pulsante sobre los POIs amenazados. El color es
 *  del nivel —naranja el grave, ámbar el aviso— y NUNCA `vela-replan`, que es del banner. */
export function ThreatLayer({
  pois,
  threats,
  stroke,
}: {
  pois: POI[]
  threats: Map<string, PoiThreat>
  stroke: number
}) {
  return (
    <g pointerEvents="none">
      {pois.map((poi) => {
        const t = threats.get(poi.id)
        if (!t || t.level === 0) return null
        const color = t.level === 2 ? GLOW : ALERT
        return (
          <g key={poi.id}>
            {/* 0,12 era para fondo oscuro; sobre terreno pálido el relleno
                desaparecía y el POI amenazado se quedaba sin halo. */}
            <circle cx={poi.x} cy={poi.z} r={stroke * 12} fill={color} fillOpacity={0.22} />
            <circle
              className="vela-pulse"
              cx={poi.x}
              cy={poi.z}
              r={stroke * 12}
              fill="none"
              stroke={color}
              strokeWidth={stroke * 1.6}
              style={{ animationDelay: `${phase(poi.id, 1.8)}s` }}
            />
          </g>
        )
      })}
    </g>
  )
}

export function PoisLayer({
  pois,
  civilians,
  stroke,
}: {
  pois: POI[]
  civilians: CivilianView[]
  stroke: number
}) {
  const byPoi = new Map(civilians.map((c) => [c.poiId, c]))
  return (
    <g>
      {pois.map((poi) => {
        const civ = byPoi.get(poi.id)
        return (
          <g key={poi.id}>
            <rect
              x={poi.x - stroke * 3}
              y={poi.z - stroke * 3}
              width={stroke * 6}
              height={stroke * 6}
              rx={stroke}
              fill={MARKER}
              stroke={MARKER_EDGE}
              strokeWidth={stroke * 1.1}
            />
            <text
              x={poi.x + stroke * 5.5}
              y={poi.z}
              fill={INK}
              fontSize={stroke * 6.2}
              fontWeight={600}
              style={halo(stroke)}
            >
              {poi.name}
            </text>
            {civ && (
              <text
                x={poi.x + stroke * 5.5}
                y={poi.z + stroke * 6.4}
                fill={civ.state === 'safe' ? ACCENT : WARN}
                fontSize={stroke * 5.4}
                fontWeight={600}
                style={halo(stroke)}
              >
                {civ.count} {CIV_STATE[civ.state]}
              </text>
            )}
          </g>
        )
      })}
    </g>
  )
}

export function AssignmentsLayer({
  plan,
  waypoints,
  stroke,
}: {
  plan: Plan | null
  waypoints: Map<string, Waypoint>
  stroke: number
}) {
  if (!plan) return null
  return (
    // `key` con el id del plan: al llegar un plan nuevo, React monta un grupo nuevo y
    // el CSS le hace el fundido de entrada. No se morfea la `d` de un path (no es
    // animable de forma fiable en todos los navegadores): entra la ruta nueva y sale la
    // vieja, que a diez metros lee igual de bien.
    <g key={plan.id} className="vela-plan-enter">
      {plan.assignments.map((assignment) => {
        const points = assignment.route
          .map((wp) => waypoints.get(wp))
          .filter((wp): wp is Waypoint => wp !== undefined)
        if (points.length < 2) return null
        const last = points[points.length - 1]
        const line = points.map((p) => `${p.x},${p.z}`).join(' ')
        return (
          <g key={`${assignment.unit_id}:${assignment.task_id}`}>
            {/* Un pespunte claro debajo: el azul de acento solo, sobre vegetación oscura,
                no se lee desde el fondo de la sala (REQ-226). */}
            <polyline
              points={line}
              fill="none"
              stroke={HALO}
              strokeWidth={stroke * 3.6}
              strokeLinecap="round"
              strokeLinejoin="round"
            />
            <polyline
              points={line}
              fill="none"
              stroke={ACCENT}
              strokeWidth={stroke * 1.8}
              strokeLinecap="round"
              strokeLinejoin="round"
              markerEnd="url(#vela-arrow)"
            />
            {last && (
              <text
                x={last.x + stroke * 3}
                y={last.z - stroke * 4}
                fill={ACCENT}
                fontSize={stroke * 5.8}
                fontWeight={700}
                style={halo(stroke)}
              >
                {seconds(assignment.eta_s)}
              </text>
            )}
          </g>
        )
      })}
    </g>
  )
}

export function UnitsLayer({ units, stroke }: { units: UnitView[]; stroke: number }) {
  // Las cuatro ambulancias arrancan a dos metros unas de otras en el hospital, así que
  // sus cuatro etiquetas caían exactamente encima y no se leía ninguna. Se apilan: la
  // primera en su sitio y las demás un renglón más abajo cada una. Se agrupa por
  // posición redondeada a la rejilla de trazo, que es la resolución a la que dos
  // etiquetas se pisan de verdad.
  const row = new Map<string, number>()
  const stackIndex = new Map<string, number>()
  for (const unit of units) {
    const cell = `${Math.round(unit.x / (stroke * 6))}:${Math.round(unit.z / (stroke * 6))}`
    const n = row.get(cell) ?? 0
    stackIndex.set(unit.id, n)
    row.set(cell, n + 1)
  }
  return (
    <g>
      {units.map((unit) => {
        const down = unit.status === 'unavailable'
        const stacked = stackIndex.get(unit.id) ?? 0
        return (
          // La transición del `transform` interpola el movimiento: las posiciones
          // llegan cada 2-4 s y sin esto el mapa parpadea y parece roto.
          <g
            key={unit.id}
            className="vela-unit"
            style={{ transform: `translate(${unit.x}px, ${unit.z}px)` }}
          >
            {down && (
              // El camión averiado tiene que notarse sin señalarlo (REQ-078, REQ-223).
              <circle
                r={stroke * 6.5}
                fill="none"
                stroke={ALERT}
                strokeWidth={stroke * 1.4}
                strokeDasharray={`${stroke * 2} ${stroke * 1.4}`}
              />
            )}
            <g transform={`rotate(${unit.heading})`}>
              <path
                d={`M 0 ${-stroke * 4.4} L ${stroke * 3} ${stroke * 3.4} L ${-stroke * 3} ${stroke * 3.4} Z`}
                fill={MARKER}
                stroke={MARKER_EDGE}
                strokeWidth={stroke * 1.1}
                strokeLinejoin="round"
                opacity={down ? 0.6 : 1}
              />
            </g>
            {down && (
              <>
                <line
                  x1={-stroke * 4.5}
                  y1={-stroke * 4.5}
                  x2={stroke * 4.5}
                  y2={stroke * 4.5}
                  stroke={ALERT}
                  strokeWidth={stroke * 1.6}
                  strokeLinecap="round"
                />
                <line
                  x1={stroke * 4.5}
                  y1={-stroke * 4.5}
                  x2={-stroke * 4.5}
                  y2={stroke * 4.5}
                  stroke={ALERT}
                  strokeWidth={stroke * 1.6}
                  strokeLinecap="round"
                />
              </>
            )}
            <text
              x={stroke * 5.5}
              y={stroke * 3 + stacked * stroke * 5.4}
              fill={down ? WARN : INK}
              fontSize={stroke * 5.2}
              fontWeight={600}
              style={halo(stroke)}
            >
              {unit.id.replace('unit_', '')}
            </text>
          </g>
        )
      })}
    </g>
  )
}

/** La brújula del viento: la flecha apunta hacia donde SOPLA y el texto da el rumbo del
 *  contrato (de dónde viene), que es lo que dice el resto del sistema. */
export function WindLegend({
  wind,
  box,
  stroke,
}: {
  wind: Wind
  box: Box
  stroke: number
}) {
  const x = box.minX + box.width * 0.92
  const y = box.minZ + box.height * 0.1
  const { deg } = blowTo(wind.bearing_deg)
  return (
    <g>
      <g transform={`translate(${x} ${y})`}>
        <g className="vela-wind" style={{ transform: `rotate(${deg}deg)` }}>
          <line
            y1={stroke * 6}
            y2={-stroke * 6}
            stroke={HALO}
            strokeWidth={stroke * 3.4}
            strokeLinecap="round"
          />
          <line y1={stroke * 6} y2={-stroke * 6} stroke={INK} strokeWidth={stroke * 1.4} strokeLinecap="round" />
          <path
            d={`M 0 ${-stroke * 9} L ${stroke * 3} ${-stroke * 4} L ${-stroke * 3} ${-stroke * 4} Z`}
            fill={INK}
            stroke={HALO}
            strokeWidth={stroke * 1.2}
            paintOrder="stroke"
          />
        </g>
      </g>
      <text
        x={x}
        y={y + stroke * 13}
        fill={INK}
        fontSize={stroke * 5.4}
        fontWeight={600}
        textAnchor="middle"
        style={halo(stroke)}
      >
        viento {Math.round(wind.bearing_deg)}° · {String(wind.speed).replace('.', ',')}
      </text>
    </g>
  )
}

/** Los centros de las celdas conocidas, para que entren en el encuadre. */
export function cellPoints(cells: Map<string, CellState>, geo: Geo) {
  return [...cells.keys()]
    .map((id) => cellCenter(id, geo))
    .filter((p): p is { x: number; z: number } => p !== null)
}

// --- capas propias de la Simulación 2D ---

const TARGET = 'var(--color-vela-target)'
const FIRE = 'var(--color-vela-fire)'

/** Los focos puestos a mano con la herramienta 🔥: un aro para verlos y para poder
 *  quitarlos. No son estado del mundo, son lo que el usuario ha encendido. */
export function IgnitionLayer({
  cells,
  states,
  geo,
  stroke,
}: {
  cells: string[]
  /** Estado vivo de las celdas: el aro solo se pinta sobre las que siguen ardiendo.
   *  Sobre una celda ya consumida no marca nada, y el frente ya se ha ido de ahí. */
  states: ReadonlyMap<string, CellState>
  geo: Geo
  stroke: number
}) {
  return (
    <g pointerEvents="none">
      {cells.map((id) => {
        if (states.get(id) !== 'burning') return null
        const c = cellCenter(id, geo)
        if (!c) return null
        return (
          <circle
            key={id}
            className="vela-pulse"
            cx={c.x}
            cy={c.z}
            r={geo.cellSize}
            fill="none"
            stroke={FIRE}
            strokeWidth={stroke * 1.2}
            style={{ animationDelay: `${phase(id, 1.8)}s` }}
          />
        )
      })}
    </g>
  )
}

/** El punto rojo: a dónde va cada unidad. Es lo que une la fila del panel Solver con
 *  lo que pasa en el mapa, así que al señalar una fila su punto crece y su ruta se
 *  resalta.
 *
 *  Color propio (`--color-vela-target`) y no `--color-vela-fire`: el destino de una
 *  tarea de extinción está, por definición, pegado al fuego, y un punto naranja sobre
 *  una celda naranja no se ve. Tampoco `--color-vela-replan`, que es del banner. */
export function TargetsLayer({
  targets,
  waypoints,
  stroke,
  focus,
}: {
  targets: Array<{ key: string; waypointId: string; label: string }>
  waypoints: Map<string, Waypoint>
  stroke: number
  focus: string | null
}) {
  return (
    <g pointerEvents="none">
      {targets.map((t) => {
        const wp = waypoints.get(t.waypointId)
        if (!wp) return null
        const on = focus === t.key
        return (
          <g key={t.key}>
            {on && (
              <circle
                className="vela-target-pulse"
                cx={wp.x}
                cy={wp.z}
                r={stroke * 5}
                fill="none"
                stroke={TARGET}
                strokeWidth={stroke * 1.4}
              />
            )}
            <circle
              cx={wp.x}
              cy={wp.z}
              r={on ? stroke * 3 : stroke * 1.9}
              fill={TARGET}
              stroke={HALO}
              strokeWidth={stroke * 0.7}
            />
            {on && (
              <text
                x={wp.x + stroke * 4.5}
                y={wp.z + stroke * 5.5}
                fill={TARGET}
                fontSize={stroke * 5.4}
                fontWeight={700}
                style={halo(stroke)}
              >
                {t.label}
              </text>
            )}
          </g>
        )
      })}
    </g>
  )
}

/** Rutas ya ordenadas pero RETENIDAS: la unidad tiene orden y no se mueve porque su
 *  dotación todavía está al teléfono. En gris discontinuo, que es la imagen del beat. */
export function PendingRoutesLayer({
  routes,
  waypoints,
  stroke,
}: {
  routes: Array<{ unitId: string; route: string[] }>
  waypoints: Map<string, Waypoint>
  stroke: number
}) {
  return (
    <g pointerEvents="none">
      {routes.map(({ unitId, route }) => {
        const pts = route.map((id) => waypoints.get(id)).filter((w): w is Waypoint => w !== undefined)
        if (pts.length < 2) return null
        return (
          <polyline
            key={unitId}
            className="vela-pending"
            points={pts.map((p) => `${p.x},${p.z}`).join(' ')}
            fill="none"
            stroke={INK}
            strokeWidth={stroke * 1.4}
            strokeLinejoin="round"
            strokeLinecap="round"
          />
        )
      })}
    </g>
  )
}
