// El mapa 2D: unidades, POIs, celdas y flechas de asignación.
// Las coordenadas llegan en (x, z) del mundo Minecraft; la proyección a píxeles
// es cosa de este panel y de nadie más.
//
// Si Minecraft falla, este panel es la demo (plan B nivel 3).
//
// Tres piezas y este fichero solo las junta: `useScenario` trae la geometría estática
// (no viaja por eventos), `useWorldView` trae lo que se mueve, y `map/project.ts` hace
// la única traducción de mundo a SVG que existe en el proyecto.
import { useMemo, useRef } from 'react'

import type { Event, Plan, WorldState } from '../types'
import { Empty, Panel } from '../components/Panel'
import { useScenario } from '../hooks/useScenario'
import { useWorldView } from '../hooks/useWorldView'
import {
  ArrowMarker,
  AssignmentsLayer,
  CellsLayer,
  PoisLayer,
  RoadsLayer,
  UnitsLayer,
  WindLegend,
  cellPoints,
} from '../map/layers'
import {
  boxOf,
  geoOf,
  growViewBox,
  strokeFor,
  viewBoxAttr,
  waypointMap,
  type Box,
} from '../map/project'

export function MapPanel({
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
  const layer = useScenario(scenarioId)
  const view = useWorldView(state, events)
  // El encuadre se guarda entre renders porque solo puede CRECER: si se recalculase
  // con cada posición, el mapa daría un salto por segundo (project.ts, growViewBox).
  const boxRef = useRef<Box | null>(null)

  const geo = useMemo(() => (layer ? geoOf(layer) : null), [layer])

  const box = useMemo(() => {
    if (!layer || !geo) return null
    const points = [
      ...layer.waypoints.map((wp) => ({ x: wp.x, z: wp.z })),
      ...layer.pois.map((poi) => ({ x: poi.x, z: poi.z })),
      ...[...view.units.values()].map((u) => ({ x: u.x, z: u.z })),
      ...cellPoints(view.cells, geo),
    ]
    boxRef.current = growViewBox(boxRef.current, boxOf(points, geo.cellSize * 2))
    return boxRef.current
  }, [layer, geo, view])

  const waypoints = useMemo(() => waypointMap(layer?.waypoints ?? []), [layer])

  // Un id con datos pero sin geometría conocida no se esconde: va a un carril al pie
  // del panel. El día que el golden traiga ids distintos a los de la capa provisional,
  // quiero verlo ahí y no descubrirlo en el pitch.
  const unlocated = useMemo(() => {
    const known = new Set(layer?.pois.map((p) => p.id))
    if (!layer) return []
    return [...view.civilians.values()].filter((c) => !known.has(c.poiId)).map((c) => c.poiId)
  }, [layer, view])

  if (!layer || !geo || !box) {
    return (
      // El vacío dice qué falta y de dónde tiene que llegar; el endpoint va al nivel
      // *registro*, que es para mí y no para la sala (REQ-193, REQ-197).
      <Panel title="Mapa" count={0} level={1}>
        <Empty>Esperando geometría del escenario.</Empty>
        <p className="mt-1 text-xs text-vela-dim">GET /api/scenario sin responder</p>
      </Panel>
    )
  }

  const stroke = strokeFor(box)
  const units = [...view.units.values()]

  return (
    // `level={1}`: el mapa es el primer nivel de la jerarquía y los otros cinco el
    // segundo (REQ-196). Sin navegación que ordene la pantalla, el borde es lo único que
    // dice desde el fondo de la sala por dónde empezar a mirar.
    <Panel
      title="Mapa"
      count={units.length}
      level={1}
      note={
        layer.provisional ? (
          // Honestidad antes que estética: esta geometría es de prueba mientras P2 no
          // rellene el YAML, y quien mire la pantalla tiene derecho a saberlo.
          <span className="text-vela-warn">
            escenario provisional ({layer.provisional_lists.join(', ')})
          </span>
        ) : (
          layer.name
        )
      }
      className="relative"
    >
      <svg
        viewBox={viewBoxAttr(box)}
        preserveAspectRatio="xMidYMid meet"
        className="h-full w-full"
      >
        <ArrowMarker stroke={stroke} />
        <CellsLayer cells={view.cells} geo={geo} />
        <RoadsLayer
          roads={layer.roads}
          waypoints={waypoints}
          cutRoads={view.cutRoads}
          stroke={stroke}
        />
        <PoisLayer pois={layer.pois} civilians={[...view.civilians.values()]} stroke={stroke} />
        {/* Esperando el snapshot, el mapa se dibuja SIN lo que se mueve (REQ-202): el
            terreno es geometría cacheada y sigue siendo cierto, pero unidades y flechas
            son estado del run y todavía no se sabe dónde están. Enseñar las de antes de
            la reconexión sería pintar un mundo que quizá ya no existe. */}
        {!awaitingSnapshot && (
          <>
            <AssignmentsLayer plan={plan} waypoints={waypoints} stroke={stroke} />
            <UnitsLayer units={units} stroke={stroke} />
          </>
        )}
        {view.wind && (
          <WindLegend
            bearing={view.wind.bearing_deg}
            speed={view.wind.speed}
            box={box}
            stroke={stroke}
          />
        )}
      </svg>
      {unlocated.length > 0 && (
        <p className="absolute bottom-1 left-3 text-xs text-vela-warn">
          sin ubicar: {[...new Set(unlocated)].join(', ')}
        </p>
      )}
    </Panel>
  )
}
