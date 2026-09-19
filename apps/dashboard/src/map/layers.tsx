// Las capas del mapa, cada una con una responsabilidad y ningún estado.
//
// Reciben datos ya proyectables y devuelven SVG. Se dibujan en este orden desde
// `MapPanel`: celdas → carreteras → POIs → flechas → unidades. Las unidades encima de
// todo porque son lo que se mira.
import type { CellState, POI, Plan, RoadEdge, Waypoint } from '../types'
import type { CivilianView, UnitView } from '../hooks/useWorldView'
import { CIV_STATE } from '../story/labels'
import { seconds } from '../story/format'
import { cellCenter, projectCell, type Box, type Geo } from './project'

/** Los colores del mapa salen de `@theme` y no hay un solo literal en este fichero
 *  (REQ-199): un valor suelto aquí dentro no se puede comparar con el del banner sin
 *  abrir dos ficheros, y esa comparación —que `burning` y `vela-replan` no se confundan a
 *  diez metros— es la que decide si el banner sigue siendo un acontecimiento. */
const INK = 'var(--color-vela-ink)'
const DIM = 'var(--color-vela-dim)'
const EDGE = 'var(--color-vela-edge)'
const ACCENT = 'var(--color-vela-accent)'
const WARN = 'var(--color-vela-warn)'

/** El fuego va en ámbar y naranja, nunca en rojo: `vela-replan` está reservado al
 *  banner de replan y si el mapa lo gasta, el banner deja de significar nada.
 *
 *  Sigue siendo una tabla explícita sobre `CellState` y no una plantilla de nombres: así
 *  un estado nuevo de P1 rompe la compilación en vez de pintar una celda transparente. */
const CELL_FILL: Record<CellState, string> = {
  intact: 'transparent',
  at_risk: 'var(--color-vela-cell-at-risk)',
  burning: 'var(--color-vela-cell-burning)',
  burnt: 'var(--color-vela-cell-burnt)',
  flooded: 'var(--color-vela-cell-flooded)',
  dark: 'var(--color-vela-cell-dark)',
}

export function CellsLayer({ cells, geo }: { cells: Map<string, CellState>; geo: Geo }) {
  return (
    <g>
      {[...cells].map(([id, state]) => {
        const box = projectCell(id, geo)
        if (!box || state === 'intact') return null
        return (
          <rect
            key={id}
            x={box.minX}
            y={box.minZ}
            width={box.width}
            height={box.height}
            fill={CELL_FILL[state]}
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
        return (
          <g key={road.id}>
            <line
              x1={a.x}
              y1={a.z}
              x2={b.x}
              y2={b.z}
              stroke={cut ? WARN : EDGE}
              strokeWidth={cut ? stroke * 1.6 : stroke}
              strokeDasharray={cut ? `${stroke * 3} ${stroke * 2}` : undefined}
            />
            {cut && (
              <>
                <text
                  x={(a.x + b.x) / 2}
                  y={(a.z + b.z) / 2 - stroke * 3}
                  fill={WARN}
                  fontSize={stroke * 9}
                  textAnchor="middle"
                >
                  ✕ cortada
                </text>
                {/* La causa va EN el mapa, no en un tooltip: un tooltip no existe en
                    un proyector. */}
                {cause && (
                  <text
                    x={(a.x + b.x) / 2}
                    y={(a.z + b.z) / 2 + stroke * 9}
                    fill={WARN}
                    fontSize={stroke * 7}
                    textAnchor="middle"
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
              fill="none"
              stroke={DIM}
              strokeWidth={stroke}
            />
            <text x={poi.x + stroke * 5} y={poi.z} fill={INK} fontSize={stroke * 9}>
              {poi.name}
            </text>
            {civ && (
              <text
                x={poi.x + stroke * 5}
                y={poi.z + stroke * 9}
                fill={civ.state === 'safe' ? ACCENT : WARN}
                fontSize={stroke * 8}
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
        return (
          <g key={`${assignment.unit_id}:${assignment.task_id}`}>
            <polyline
              points={points.map((p) => `${p.x},${p.z}`).join(' ')}
              fill="none"
              stroke={ACCENT}
              strokeWidth={stroke * 1.4}
              markerEnd="url(#vela-arrow)"
            />
            {last && (
              <text
                x={last.x + stroke * 3}
                y={last.z - stroke * 4}
                fill={ACCENT}
                fontSize={stroke * 8}
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
  return (
    <g>
      {units.map((unit) => {
        const down = unit.status === 'unavailable'
        return (
          // La transición del `transform` interpola el movimiento: las posiciones
          // llegan cada 2-4 s y sin esto el mapa parpadea y parece roto.
          <g
            key={unit.id}
            className="vela-unit"
            style={{ transform: `translate(${unit.x}px, ${unit.z}px)` }}
          >
            <g transform={`rotate(${unit.heading})`}>
              <path
                d={`M 0 ${-stroke * 4} L ${stroke * 2.6} ${stroke * 3} L ${-stroke * 2.6} ${stroke * 3} Z`}
                fill={down ? DIM : INK}
                opacity={down ? 0.5 : 1}
              />
            </g>
            {down && (
              <line
                x1={-stroke * 4}
                y1={-stroke * 4}
                x2={stroke * 4}
                y2={stroke * 4}
                stroke={WARN}
                strokeWidth={stroke}
              />
            )}
            <text
              x={stroke * 5}
              y={stroke * 3}
              fill={down ? WARN : DIM}
              fontSize={stroke * 7}
            >
              {unit.id.replace('unit_', '')}
            </text>
          </g>
        )
      })}
    </g>
  )
}

export function WindLegend({
  bearing,
  speed,
  box,
  stroke,
}: {
  bearing: number
  speed: number
  box: Box
  stroke: number
}) {
  const x = box.minX + box.width * 0.92
  const y = box.minZ + box.height * 0.1
  return (
    <g>
      <g transform={`translate(${x} ${y}) rotate(${bearing})`}>
        <line y1={stroke * 6} y2={-stroke * 6} stroke={ACCENT} strokeWidth={stroke} />
        <path
          d={`M 0 ${-stroke * 8} L ${stroke * 2} ${-stroke * 4} L ${-stroke * 2} ${-stroke * 4} Z`}
          fill={ACCENT}
        />
      </g>
      <text x={x} y={y + stroke * 12} fill={DIM} fontSize={stroke * 7} textAnchor="middle">
        viento {Math.round(bearing)}° · {speed}
      </text>
    </g>
  )
}

/** La punta de las flechas de asignación, definida una vez. */
export function ArrowMarker({ stroke }: { stroke: number }) {
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
    </defs>
  )
}

/** Los centros de las celdas conocidas, para que entren en el encuadre. */
export function cellPoints(cells: Map<string, CellState>, geo: Geo) {
  return [...cells.keys()]
    .map((id) => cellCenter(id, geo))
    .filter((p): p is { x: number; z: number } => p !== null)
}
