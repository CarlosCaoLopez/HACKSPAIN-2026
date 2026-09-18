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
import { Panel } from '../components/Panel'
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
}: {
  state: WorldState | null
  plan: Plan | null
  events: Event[]
  scenarioId: string | null
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
      <Panel title="Mapa" note="esperando la capa del escenario">
        <p>sin datos · GET /api/scenario no ha respondido todavía</p>
      </Panel>
    )
  }

  const stroke = strokeFor(box)
  const units = [...view.units.values()]

  return (
    <Panel
      title="Mapa"
      count={units.length}
      note={
        layer.provisional ? (
          // Honestidad antes que estética: esta geometría es de prueba mientras P2 no
          // rellene el YAML, y quien mire la pantalla tiene derecho a saberlo.
          <span className="text-amber-400">
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
        <AssignmentsLayer plan={plan} waypoints={waypoints} stroke={stroke} />
        <UnitsLayer units={units} stroke={stroke} />
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
        <p className="absolute bottom-1 left-3 text-xs text-amber-400">
          sin ubicar: {[...new Set(unlocated)].join(', ')}
        </p>
      )}
    </Panel>
  )
}
